"""Milestone 10: the receiver under attack and under load.

Every test talks to a real receiver over real TLS on localhost. They prove the logic holds against
hostile input; they cannot prove anything about a real Wi-Fi network.
"""
import asyncio
import contextlib
import datetime
import hashlib
import json
import logging
import os
import random
import ssl
import struct

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from aircatch.security import tls
from aircatch.security.trust import TrustedPeer
from aircatch.transfer import send_file
from aircatch.transfer.filenames import sanitize_filename
from aircatch.transfer.protocol import read_message, send_message
from tests.test_security import Device, pair_directly, secure_receiver


def files_in(inbox):
    return sorted(p.name for p in inbox.glob("*") if not p.name.startswith(".")) if inbox.exists() else []


def partials(inbox):
    return list((inbox / ".aircatch-incoming").glob("*")) if inbox.exists() else []


async def connect(port, identity=None, server=None, source=None):
    ctx = tls.build_client_context(identity, server.cert_pem if server else None)
    return await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname="aircatch",
                                         local_addr=(source, 0) if source else None)


async def close(writer):
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


def random_junk(r):
    kind = r.randrange(9)
    if kind == 0:
        return os.urandom(r.randrange(1, 400))
    if kind == 1:
        return struct.pack(">I", r.choice([0, 1, 65537, 2 ** 31, 2 ** 32 - 1])) + os.urandom(r.randrange(0, 50))
    if kind == 2:
        body = os.urandom(r.randrange(1, 200))
        return struct.pack(">I", len(body)) + body
    values = [None, True, False, 0, -1, 2 ** 70, 1.5, "", "x" * r.randrange(1, 3000), "../../evil", "\x00",
              [], {}, ["a"], {"a": 1}, "offer", "pair_request", "done", 1]
    keys = ["type", "version", "transfer_id", "filename", "size", "sender_name", "thumbnail", "cert",
            "proof", "device_name", "listen_port", "sha256", "extra"]
    message = {k: r.choice(values) for k in r.sample(keys, r.randrange(0, len(keys)))}
    if kind in (3, 4):
        message["type"] = r.choice(["offer", "pair_request", "done", "accept", "result", "nonsense"])
    if kind == 5:
        message.update(type="offer", version=1, transfer_id=r.choice(["ok-id", "../x", "a" * 65, ""]))
    body = json.dumps(message).encode()
    if kind == 6:
        body = body[: r.randrange(0, len(body) + 1)]                 # cut off part way
    return struct.pack(">I", len(body) if kind != 7 else len(body) + r.randrange(1, 50)) + body


@pytest.mark.parametrize("paired", [False, True])
def test_two_thousand_malformed_messages_do_not_break_the_receiver(tmp_path, caplog, paired):
    """Garbage from an unpaired device, and from a paired one, never crashes anything, never writes a
    file, and a proper transfer still works afterwards."""
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        r = random.Random(7 if paired else 8)
        async with secure_receiver(alice, max_per_address=64, max_connections=64) as (_, port, _):
            async def one():
                reader, writer = await connect(port, bob.identity if paired else None,
                                               TrustedPeer.from_identity(alice.identity) if paired else None)
                try:
                    writer.write(random_junk(r))
                    if r.random() < 0.3:
                        writer.write(random_junk(r))
                    await writer.drain()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(reader.read(4096), 0.05)
                except (OSError, ssl.SSLError):
                    pass
                finally:
                    await close(writer)
            for _ in range(125):
                await asyncio.gather(*(one() for _ in range(8)))
            assert files_in(alice.inbox) == [] and partials(alice.inbox) == []
            src = tmp_path / "after.bin"
            src.write_bytes(os.urandom(300_000))
            result = await send_file("127.0.0.1", port, src, sender_name="Bob", identity=bob.identity,
                                     peer=TrustedPeer.from_identity(alice.identity))
            assert result.ok, result.reason
        assert files_in(alice.inbox) == ["after.bin"] and partials(alice.inbox) == []
    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR], caplog.text[:2000]


NAMES = ["../../../Windows/System32/evil.dll", "..\\..\\evil.exe", "C:\\Users\\x\\Startup\\run.bat", "/etc/passwd",
         "notes.txt:hidden.exe", "CON", "nul.txt", "NUL.tar.gz", "com1.log", "LPT9", "aux .txt", "....", " ", ".",
         "photo\u202egpj.exe", "invoice\u200b.pdf\u2066.exe", "a" * 600 + ".txt", "x." + "e" * 300, "tab\tname\n.txt",
         "\x00\x01\x02", "CONIN$", "desktop.ini", "~$report.docx", "\u00e9te\u0301.txt", "\ud83d\ude00.png", "a/b/c/d.txt"]


@pytest.mark.parametrize("name", NAMES)
def test_hostile_file_names_are_made_safe(name):
    safe = sanitize_filename(name)
    assert safe and len(safe) <= 150
    assert not any(c in safe for c in '<>:"/\\|?*') and not any(ord(c) < 32 for c in safe)
    assert safe == safe.strip(" .") and safe not in (".", "..")
    assert safe.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "CONIN$"}
    assert "\u202e" not in safe and "\u200b" not in safe and "\u2066" not in safe     # nothing invisible


def test_a_disguised_program_shows_its_real_extension():
    assert sanitize_filename("photo\u202egpj.exe").endswith(".exe")
    assert sanitize_filename("photo\u202egpj.exe") == "photogpj.exe"


def test_files_with_hostile_names_land_inside_the_inbox_and_nowhere_else(tmp_path):
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        body = b"payload"
        before = {p for p in tmp_path.rglob("*")}
        async with secure_receiver(alice, max_per_address=64) as (_, port, _):
            for i, name in enumerate(NAMES):
                reader, writer = await connect(port, bob.identity, TrustedPeer.from_identity(alice.identity))
                try:
                    await send_message(writer, {"type": "offer", "version": 1, "transfer_id": f"t{i}",
                                                "sender_name": "Bob", "filename": name, "size": len(body)})
                    reply = await read_message(reader, 5)
                    if reply["type"] != "accept":
                        continue                                   # refused outright is fine too
                    writer.write(body)
                    await send_message(writer, {"type": "done", "sha256": hashlib.sha256(body).hexdigest()})
                    assert (await read_message(reader, 5)).get("ok") is True
                finally:
                    await close(writer)
        new = {p for p in tmp_path.rglob("*")} - before
        outside = [p for p in new if alice.inbox not in p.parents and p != alice.inbox]
        assert outside == [], outside
        assert all(p.parent == alice.inbox for p in new if p.is_file()), new
        assert len(files_in(alice.inbox)) >= len(NAMES) - 2
    asyncio.run(scenario())


def _leaf_signed_by(issuer_identity, tmp_path):
    """A brand-new key whose certificate is SIGNED BY a trusted device's key (that device misbehaving)."""
    issuer_key = serialization.load_pem_private_key(issuer_identity.key_path.read_bytes(), None)
    issuer_cert = x509.load_pem_x509_certificate(issuer_identity.cert_pem.encode())
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Forged")]))
            .issuer_name(issuer_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(issuer_key, hashes.SHA256()))
    folder = tmp_path / "forged"
    folder.mkdir()
    (folder / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
    (folder / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return folder / "c.pem", folder / "k.pem"


def test_a_certificate_issued_by_a_paired_device_is_not_itself_trusted(tmp_path):
    """Pairing trusts one exact certificate, not anything that certificate's owner signs."""
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        cert_path, key_path = _leaf_signed_by(bob.identity, tmp_path)
        ctx = tls.build_client_context(None, alice.identity.cert_pem)
        ctx.load_cert_chain(str(cert_path), str(key_path))
        async with secure_receiver(alice) as (_, port, _):
            reply = {}
            with contextlib.suppress(OSError, ssl.SSLError, asyncio.IncompleteReadError, Exception):
                reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname="aircatch")
                await send_message(writer, {"type": "offer", "version": 1, "transfer_id": "forged",
                                            "sender_name": "Bob", "filename": "x.txt", "size": 1})
                reply = await read_message(reader, 5)
                await close(writer)
            assert reply.get("type") != "accept"
        assert files_in(alice.inbox) == []
    asyncio.run(scenario())


@pytest.mark.parametrize("lie", ["short", "wrong_hash", "no_hash", "garbage_done", "vanish"])
def test_a_sender_that_lies_or_vanishes_leaves_nothing_behind(tmp_path, lie):
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        body = os.urandom(200_000)
        async with secure_receiver(alice, idle_timeout=0.6) as (_, port, events):
            reader, writer = await connect(port, bob.identity, TrustedPeer.from_identity(alice.identity))
            await send_message(writer, {"type": "offer", "version": 1, "transfer_id": "liar", "sender_name": "Bob",
                                        "filename": "f.bin", "size": len(body)})
            assert (await read_message(reader, 5))["type"] == "accept"
            if lie == "short":
                writer.write(body[:1000])
                await writer.drain()                       # ...and then says nothing more
            elif lie == "vanish":
                writer.write(body[:50_000])
                await writer.drain()
                await close(writer)
            else:
                writer.write(body)
                if lie == "wrong_hash":
                    await send_message(writer, {"type": "done", "sha256": "0" * 64})
                elif lie == "garbage_done":
                    await send_message(writer, {"type": "done", "sha256": "../../nope"})
                else:
                    await send_message(writer, {"type": "done"})
            for _ in range(60):
                if any(e.kind == "failed" for e in events):
                    break
                await asyncio.sleep(0.05)
            assert any(e.kind == "failed" for e in events)
            await close(writer)
        assert files_in(alice.inbox) == [] and partials(alice.inbox) == []
    asyncio.run(scenario())


def test_one_machine_cannot_take_every_connection_slot(tmp_path):
    """An unpaired machine opens connections and says nothing. A paired device still gets through,
    and the idle connections are dropped within seconds."""
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        src = tmp_path / "real.bin"
        src.write_bytes(os.urandom(100_000))
        async with secure_receiver(alice) as (_, port, _):
            held = []
            for _ in range(12):                                     # more than the 8 slots there are
                with contextlib.suppress(OSError, ssl.SSLError):
                    held.append(await connect(port, source="127.0.0.2"))
            await asyncio.sleep(0.2)
            result = await send_file("127.0.0.1", port, src, sender_name="Bob", identity=bob.identity,
                                     peer=TrustedPeer.from_identity(alice.identity))
            assert result.ok, result.reason
            # the silent ones are closed by the receiver well before the 15 s a paired device gets
            reader, _ = held[0]
            data = await asyncio.wait_for(reader.read(), 8)
            assert b"malformed" in data or data == b""
            for _, w in held:
                await close(w)
        assert files_in(alice.inbox) == ["real.bin"]
    asyncio.run(scenario())


def test_forty_transfers_in_a_row_and_at_once_all_arrive_intact(tmp_path):
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        peer = TrustedPeer.from_identity(alice.identity)
        r = random.Random(3)
        sent = {}
        # (room for all 20 senders to be connected at once: this test is about the files, not the limits)
        async with secure_receiver(alice, max_concurrent=4, max_per_address=64, max_connections=64) as (rcv, port, _):
            async def one(i):
                src = tmp_path / f"file-{i}.bin"
                data = os.urandom(r.choice([0, 1, 4096, 1024 * 1024, 1024 * 1024 + 1, 3_000_000]))
                src.write_bytes(data)
                for _ in range(50):                                  # "busy" is a normal answer: try again
                    result = await send_file("127.0.0.1", port, src, sender_name="Bob", identity=bob.identity,
                                             peer=peer)
                    if result.ok or result.reason != "busy":
                        break
                    await asyncio.sleep(0.02)
                assert result.ok, result.reason
                sent[result.saved_as] = hashlib.sha256(data).hexdigest()
            for i in range(20):
                await one(i)
            await asyncio.gather(*(one(i) for i in range(20, 40)))
            assert not rcv._active and not rcv._per_address
        got = {name: hashlib.sha256((alice.inbox / name).read_bytes()).hexdigest() for name in files_in(alice.inbox)}
        assert got == sent and len(got) == 40
        assert partials(alice.inbox) == []
    asyncio.run(scenario())


def test_cancelling_a_send_part_way_leaves_no_partial_file(tmp_path):
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        src = tmp_path / "big.bin"
        src.write_bytes(os.urandom(30_000_000))
        async with secure_receiver(alice) as (_, port, events):
            task = asyncio.create_task(send_file("127.0.0.1", port, src, sender_name="Bob", identity=bob.identity,
                                                 peer=TrustedPeer.from_identity(alice.identity)))
            for _ in range(400):
                if any(e.kind == "progress" for e in events):
                    break
                await asyncio.sleep(0.005)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            for _ in range(100):
                if any(e.kind in ("failed", "completed") for e in events):
                    break
                await asyncio.sleep(0.05)
        if not any(e.kind == "completed" for e in events):       # (a very fast disk may finish first)
            assert files_in(alice.inbox) == []
        assert partials(alice.inbox) == []
    asyncio.run(scenario())


def test_an_offer_bigger_than_the_free_disk_space_or_the_size_limit_is_refused(tmp_path):
    async def scenario():
        alice, bob = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(alice, bob)
        async with secure_receiver(alice, max_file_size=1000) as (_, port, _):
            for size, reason in ((1001, "too_large"), (2 ** 62, "too_large")):
                reader, writer = await connect(port, bob.identity, TrustedPeer.from_identity(alice.identity))
                await send_message(writer, {"type": "offer", "version": 1, "transfer_id": f"s{size}",
                                            "sender_name": "Bob", "filename": "f", "size": size})
                reply = await read_message(reader, 5)
                assert reply["type"] == "reject" and reply["reason"] == reason
                await close(writer)
    asyncio.run(scenario())


# ---------------------------------------------------------------- damaged files on disk
JUNK = [b"", b"{", b"\x00\xff\xfe garbage", b"[]", b'{"peers": 5}', b'{"peers":[{"cert_pem":5}]}', b"null",
        b'"text"', b'{"name": 12345}', b'{"peers":[5, null, {"device_id": [], "cert_pem": {}}]}']


@pytest.mark.parametrize("junk", JUNK)
def test_a_damaged_trust_list_never_stops_the_app_and_trusts_nobody(tmp_path, junk):
    from aircatch.security.trust import TrustStore
    path = tmp_path / "trusted_peers.json"
    path.write_bytes(junk)
    assert TrustStore(path).peers() == []


@pytest.mark.parametrize("junk", JUNK)
def test_a_damaged_name_file_is_ignored(tmp_path, junk):
    from aircatch.security.identity import load_or_create
    first = load_or_create(tmp_path, "Alice")
    (tmp_path / "identity" / "identity.json").write_bytes(junk)
    again = load_or_create(tmp_path)
    assert again.fingerprint == first.fingerprint and again.name


@pytest.mark.parametrize("which", ["device.crt", "device.key", "swap_key", "missing_key"])
def test_a_damaged_identity_is_reported_or_repaired(tmp_path, which):
    from aircatch.security.identity import IdentityError, load_or_create
    first = load_or_create(tmp_path / "a", "Alice")
    folder = tmp_path / "a" / "identity"
    if which == "swap_key":                                  # a key that belongs to another certificate
        other = load_or_create(tmp_path / "b", "Bob")
        (folder / "device.key").write_bytes(other.key_path.read_bytes())
    elif which == "missing_key":
        (folder / "device.key").unlink()
    else:
        (folder / which).write_bytes(b"-----BEGIN NONSENSE-----\nAAAA\n")
    with pytest.raises(IdentityError):
        load_or_create(tmp_path / "a")                       # tools: say so, change nothing
    fixed = load_or_create(tmp_path / "a", repair=True)      # the app: start afresh, keep the evidence
    assert fixed.recreated and fixed.fingerprint != first.fingerprint
    assert list(folder.glob("damaged-*"))
    ctx = tls.build_server_context(fixed, [])                # the new identity really works for TLS
    assert ctx is not None
    assert not load_or_create(tmp_path / "a", repair=True).recreated      # and it is stable from now on


def test_the_app_starts_and_says_so_when_its_identity_was_damaged(tmp_path):
    from aircatch.service import NetworkService
    from tests.test_service import Sink, make_service
    service, _ = make_service(tmp_path, "Phoenix")
    service.stop()
    (tmp_path / "Phoenix" / "cfg" / "identity" / "device.key").write_bytes(b"garbage")
    (tmp_path / "Phoenix" / "cfg" / "trusted_peers.json").write_bytes(b'{"peers": 5}')
    sink = Sink()
    service = NetworkService(sink, config_dir=tmp_path / "Phoenix" / "cfg",
                             download_dir=tmp_path / "Phoenix" / "inbox", name="Phoenix", advertise=False)
    service.start()
    try:
        assert service.ready.wait(20) and not service.start_error, service.start_error
        assert "reset" in sink.wait_for("notice")["text"]
        assert sink.wait_for("status", lambda p: p["ready"])["port"] > 0
    finally:
        service.stop()


def test_received_files_are_marked_as_from_another_computer_on_windows(tmp_path, monkeypatch):
    """Windows 'Mark of the Web': a hidden side stream of the file, with exactly these bytes (a doubled
    carriage return, which text mode produces on Windows, would spoil it). Other systems are left alone."""
    from aircatch.transfer import receiver as receiver_module
    expected = b"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=about:internet\r\n"
    target = tmp_path / "setup.exe"
    target.write_bytes(b"MZ")
    if os.name != "nt":
        receiver_module._mark_from_network(target)
        assert [p.name for p in tmp_path.iterdir()] == ["setup.exe"]      # not Windows: nothing happens
        monkeypatch.setattr(receiver_module.os, "name", "nt")             # pretend, to see what is written
    receiver_module._mark_from_network(target)
    with open(f"{target}:Zone.Identifier", "rb") as stream:               # (a side stream on real Windows)
        assert stream.read() == expected
    assert target.read_bytes() == b"MZ"                                   # the file itself is untouched
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("no streams here")))
    receiver_module._mark_from_network(target)                            # a disk without streams: no error
