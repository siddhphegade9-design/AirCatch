"""End-to-end tests of the transfer engine over localhost.

IMPORTANT: localhost tests prove the logic works. They do NOT prove that two real
devices can reach each other (firewall, router isolation). That needs two devices.
"""
import asyncio
import contextlib
import hashlib
import os
import socket
import time

from aircatch.transfer import Receiver, send_file
from aircatch.transfer.protocol import read_message, send_message


async def accept_all(offer):
    return True


async def decline_all(offer):
    return False


@contextlib.asynccontextmanager
async def running_receiver(dest, decide=accept_all, **kwargs):
    events = []
    receiver = Receiver(dest, decide, on_event=events.append, **kwargs)
    port = await receiver.start("127.0.0.1", 0)
    try:
        yield receiver, port, events
    finally:
        await receiver.stop()


def visible_files(dest):
    return sorted(p.name for p in dest.iterdir() if p.is_file())


def partial_files(dest):
    return list((dest / ".aircatch-incoming").glob("*.part"))


async def raw_client(port, *, transfer_id="t1", filename="a.bin", data=b"", size=None,
                     sha=None, stop_after=None):
    """Speak the protocol by hand, so tests can misbehave on purpose."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await send_message(writer, {"type": "offer", "version": 1, "transfer_id": transfer_id,
                                "sender_name": "raw", "filename": filename,
                                "size": len(data) if size is None else size})
    reply = await read_message(reader, 5)
    if reply["type"] != "accept":
        writer.close()
        return reply, None
    writer.write(data if stop_after is None else data[:stop_after])
    await writer.drain()
    if stop_after is not None:
        writer.close()
        return reply, None
    await send_message(writer, {"type": "done", "sha256": sha or hashlib.sha256(data).hexdigest()})
    result = await read_message(reader, 5)
    writer.close()
    return reply, result


def test_roundtrip_small_file(tmp_path):
    async def scenario():
        src = tmp_path / "hello.txt"
        src.write_text("hello AirCatch")
        async with running_receiver(tmp_path / "in") as (_, port, events):
            result = await send_file("127.0.0.1", port, src, sender_name="tester")
        assert result.ok and result.saved_as == "hello.txt"
        assert (tmp_path / "in" / "hello.txt").read_text() == "hello AirCatch"
        kinds = [e.kind for e in events]
        assert kinds[0] == "offer" and kinds[-1] == "completed" and "verifying" in kinds
    asyncio.run(scenario())


def test_large_multi_chunk_file_is_identical(tmp_path):
    async def scenario():
        src = tmp_path / "big.bin"
        src.write_bytes(os.urandom(3 * 1024 * 1024 + 12345))
        async with running_receiver(tmp_path / "in") as (_, port, events):
            result = await send_file("127.0.0.1", port, src, sender_name="tester")
        assert result.ok
        saved = tmp_path / "in" / "big.bin"
        assert hashlib.sha256(saved.read_bytes()).hexdigest() == \
            hashlib.sha256(src.read_bytes()).hexdigest()
        progress = [e for e in events if e.kind == "progress"]
        assert progress and progress[-1].bytes_done == src.stat().st_size
    asyncio.run(scenario())


def test_empty_file(tmp_path):
    async def scenario():
        src = tmp_path / "empty.txt"
        src.write_bytes(b"")
        async with running_receiver(tmp_path / "in") as (_, port, _):
            result = await send_file("127.0.0.1", port, src, sender_name="tester")
        assert result.ok and (tmp_path / "in" / "empty.txt").read_bytes() == b""
    asyncio.run(scenario())


def test_receiver_can_decline(tmp_path):
    async def scenario():
        src = tmp_path / "x.txt"
        src.write_text("data")
        async with running_receiver(tmp_path / "in", decide=decline_all) as (_, port, events):
            result = await send_file("127.0.0.1", port, src, sender_name="tester")
        assert not result.ok and result.reason == "declined"
        assert visible_files(tmp_path / "in") == []
        assert events[-1].kind == "rejected"
    asyncio.run(scenario())


def test_file_too_large_is_rejected(tmp_path):
    async def scenario():
        src = tmp_path / "x.bin"
        src.write_bytes(b"a" * 5000)
        async with running_receiver(tmp_path / "in", max_file_size=1000) as (_, port, _):
            result = await send_file("127.0.0.1", port, src, sender_name="tester")
        assert not result.ok and result.reason == "too_large"
    asyncio.run(scenario())


def test_path_traversal_name_is_neutralised(tmp_path):
    async def scenario():
        async with running_receiver(tmp_path / "in") as (_, port, _):
            _, result = await raw_client(port, filename="../../evil.txt", data=b"boo")
        assert result["ok"] is True and result["saved_as"] == "evil.txt"
        assert visible_files(tmp_path / "in") == ["evil.txt"]
        assert not (tmp_path / "evil.txt").exists()
    asyncio.run(scenario())


def test_never_overwrites_existing_files(tmp_path):
    async def scenario():
        dest = tmp_path / "in"
        dest.mkdir()
        (dest / "note.txt").write_text("ORIGINAL")
        src = tmp_path / "note.txt"
        src.write_text("NEW")
        async with running_receiver(dest) as (_, port, _):
            first = await send_file("127.0.0.1", port, src, sender_name="tester")
            second = await send_file("127.0.0.1", port, src, sender_name="tester")
        assert first.saved_as == "note (1).txt" and second.saved_as == "note (2).txt"
        assert (dest / "note.txt").read_text() == "ORIGINAL"
    asyncio.run(scenario())


def test_wrong_hash_is_detected_and_nothing_is_saved(tmp_path):
    async def scenario():
        dest = tmp_path / "in"
        async with running_receiver(dest) as (_, port, events):
            _, result = await raw_client(port, data=b"real data", sha="0" * 64)
        assert result["ok"] is False and result["reason"] == "hash_mismatch"
        assert visible_files(dest) == [] and partial_files(dest) == []
        assert events[-1].kind == "failed"
    asyncio.run(scenario())


def test_sender_disconnecting_midway_leaves_no_files(tmp_path):
    async def scenario():
        dest = tmp_path / "in"
        async with running_receiver(dest) as (_, port, events):
            await raw_client(port, data=b"x" * 100_000, stop_after=40_000)
            for _ in range(50):                       # wait up to 5 s for cleanup
                if events and events[-1].kind == "failed":
                    break
                await asyncio.sleep(0.1)
        assert events[-1].kind == "failed"
        assert visible_files(dest) == [] and partial_files(dest) == []
    asyncio.run(scenario())


def test_garbage_does_not_crash_the_receiver(tmp_path):
    async def scenario():
        src = tmp_path / "ok.txt"
        src.write_text("still works")
        async with running_receiver(tmp_path / "in") as (_, port, _):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"\xff\xff\xff\xffthis is not the protocol")
            await writer.drain()
            writer.close()
            reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
            await send_message(writer2, {"type": "offer", "version": 1, "transfer_id": "../x",
                                         "filename": "a", "size": 1})
            reply = await read_message(reader2, 5)
            writer2.close()
            assert reply["type"] == "reject" and reply["reason"] == "malformed"
            result = await send_file("127.0.0.1", port, src, sender_name="tester")
        assert result.ok
    asyncio.run(scenario())


def test_duplicate_transfer_id_is_rejected(tmp_path):
    async def scenario():
        gate = asyncio.Event()

        async def slow_decide(offer):
            await gate.wait()
            return False

        async with running_receiver(tmp_path / "in", decide=slow_decide) as (_, port, _):
            first = asyncio.create_task(raw_client(port, transfer_id="same", data=b"1"))
            await asyncio.sleep(0.3)
            reply, _ = await raw_client(port, transfer_id="same", data=b"2")
            assert reply["type"] == "reject" and reply["reason"] == "duplicate"
            gate.set()
            await first
    asyncio.run(scenario())


def test_too_many_simultaneous_transfers_is_busy(tmp_path):
    async def scenario():
        gate = asyncio.Event()

        async def slow_decide(offer):
            await gate.wait()
            return False

        async with running_receiver(tmp_path / "in", decide=slow_decide,
                                    max_concurrent=1) as (_, port, _):
            first = asyncio.create_task(raw_client(port, transfer_id="one", data=b"1"))
            await asyncio.sleep(0.3)
            reply, _ = await raw_client(port, transfer_id="two", data=b"2")
            assert reply["type"] == "reject" and reply["reason"] == "busy"
            gate.set()
            await first
    asyncio.run(scenario())


def test_cannot_connect_returns_failure(tmp_path):
    async def scenario():
        src = tmp_path / "x.txt"
        src.write_text("x")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()                                   # nothing is listening here now
        result = await send_file("127.0.0.1", port, src, sender_name="tester",
                                 connect_timeout=2)
        assert not result.ok and result.reason == "could_not_connect"
    asyncio.run(scenario())


def test_missing_file_returns_failure(tmp_path):
    async def scenario():
        result = await send_file("127.0.0.1", 1, tmp_path / "nope.txt", sender_name="tester")
        assert not result.ok and result.reason == "file_not_found"
    asyncio.run(scenario())


def test_receiver_stop_cancels_active_transfer_and_cleans_up(tmp_path):
    async def scenario():
        dest = tmp_path / "in"
        receiver = Receiver(dest, accept_all)
        port = await receiver.start("127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await send_message(writer, {"type": "offer", "version": 1, "transfer_id": "s1",
                                    "sender_name": "raw", "filename": "a.bin", "size": 10_000})
        await read_message(reader, 5)
        writer.write(b"x" * 100)
        await writer.drain()
        await asyncio.sleep(0.2)
        await receiver.stop()
        writer.close()
        assert partial_files(dest) == [] and visible_files(dest) == []
    asyncio.run(scenario())
