"""Identity, trust store, pairing and encrypted transfer.

Localhost tests prove the logic. They cannot prove two real devices can reach each other.
"""
import asyncio
import contextlib
import dataclasses
import hashlib
import os
import ssl
import stat
import sys

import pytest

from aircatch.pairing.joiner import pair_with
from aircatch.pairing.payload import PairingPayload, PayloadError
from aircatch.security import tls
from aircatch.security.identity import (IdentityError, clean_device_name, fingerprint_from_der,
                                        load_or_create, short_fingerprint)
from aircatch.security.trust import TrustedPeer, TrustStore
from aircatch.transfer import Receiver, send_file
from aircatch.transfer.protocol import read_message, send_message


class Device:
    """One simulated device: its own config folder, identity and trust list."""

    def __init__(self, tmp_path, name):
        self.config = tmp_path / name.lower()
        self.identity = load_or_create(self.config, name)
        self.trust = TrustStore(self.config / "trusted_peers.json")
        self.inbox = self.config / "received"


async def approve(_request):
    return True


async def deny(_request):
    return False


async def accept_all(_offer):
    return True


@contextlib.asynccontextmanager
async def secure_receiver(device, **kwargs):
    events = []
    receiver = Receiver(device.inbox, accept_all, device_name=device.identity.name,
                        identity=device.identity, trust=device.trust,
                        on_event=events.append, **kwargs)
    port = await receiver.start("127.0.0.1", 0)
    try:
        yield receiver, port, events
    finally:
        await receiver.stop()


async def pair(host_device, receiver, joiner_device, decide=approve, **kwargs):
    payload = receiver.open_pairing("127.0.0.1", decide=decide, **kwargs)
    return payload, await pair_with(payload, joiner_device.identity, joiner_device.trust)


def pair_directly(a, b):
    a.trust.add(TrustedPeer.from_identity(b.identity))
    b.trust.add(TrustedPeer.from_identity(a.identity))


# ---------------- identity ----------------
def test_identity_is_created_once_and_reloaded(tmp_path):
    first = load_or_create(tmp_path / "cfg", "Laptop")
    again = load_or_create(tmp_path / "cfg")
    assert first.fingerprint == again.fingerprint and len(first.fingerprint) == 64
    assert again.name == "Laptop"
    der = ssl.PEM_cert_to_DER_cert(first.cert_pem)
    assert fingerprint_from_der(der) == first.fingerprint


def test_different_config_folders_are_different_devices(tmp_path):
    a = load_or_create(tmp_path / "a", "A")
    b = load_or_create(tmp_path / "b", "B")
    assert a.fingerprint != b.fingerprint


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_private_key_is_owner_only(tmp_path):
    ident = load_or_create(tmp_path / "cfg", "A")
    assert stat.S_IMODE(os.stat(ident.key_path).st_mode) == 0o600


def test_incomplete_identity_is_reported(tmp_path):
    ident = load_or_create(tmp_path / "cfg", "A")
    ident.key_path.unlink()
    with pytest.raises(IdentityError):
        load_or_create(tmp_path / "cfg")


def test_device_names_are_cleaned():
    assert clean_device_name("  Bob\x00\n  PC  ") == "Bob PC"
    assert clean_device_name("") == "AirCatch Device"
    assert len(clean_device_name("x" * 200)) == 60
    assert short_fingerprint("a1b2c3d4e5f6" + "0" * 52) == "A1B2 C3D4 E5F6"


# ---------------- trust store ----------------
def test_trust_store_persists_and_removes(tmp_path):
    a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
    a.trust.add(TrustedPeer.from_identity(b.identity))
    reloaded = TrustStore(a.config / "trusted_peers.json")
    assert reloaded.get(b.identity.fingerprint).name == "Bob"
    assert reloaded.find("bob") and reloaded.find(b.identity.fingerprint[:8])
    assert reloaded.remove(b.identity.fingerprint) is True
    assert TrustStore(a.config / "trusted_peers.json").peers() == []
    assert reloaded.remove("0" * 64) is False


def test_trust_store_rejects_mismatched_certificate(tmp_path):
    a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
    bad = TrustedPeer("0" * 64, "Mallory", b.identity.cert_pem)
    with pytest.raises(ValueError):
        a.trust.add(bad)


def test_tampered_trust_file_entry_is_ignored(tmp_path):
    a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
    a.trust.add(TrustedPeer.from_identity(b.identity))
    path = a.config / "trusted_peers.json"
    path.write_text(path.read_text().replace(b.identity.fingerprint, "f" * 64))
    assert TrustStore(path).peers() == []


def test_corrupt_trust_file_does_not_crash(tmp_path):
    path = tmp_path / "trusted_peers.json"
    path.write_text("{not json")
    assert TrustStore(path).peers() == []
    assert path.with_suffix(".corrupt").exists()


# ---------------- pairing string ----------------
def test_pairing_string_roundtrip():
    p = PairingPayload("192.168.1.5", 5001, "ab" * 32, "T" * 22, "Siddh's PC \u2713")
    assert PairingPayload.parse(p.to_uri()) == p


@pytest.mark.parametrize("bad", [
    "", "hello", "https://example.com/pair?v=1",
    "aircatch://pair?v=2&h=1.2.3.4&p=5001&f=" + "a" * 64 + "&t=" + "T" * 22,
    "aircatch://pair?v=1&h=1.2.3.4&p=99999&f=" + "a" * 64 + "&t=" + "T" * 22,
    "aircatch://pair?v=1&h=1.2.3.4&p=5001&f=short&t=" + "T" * 22,
    "aircatch://pair?v=1&h=1.2.3.4&p=5001&f=" + "a" * 64 + "&t=bad token!",
    "aircatch://pair?v=1&h=1.2.3.4&p=5001&f=" + "a" * 64,
])
def test_bad_pairing_strings_are_rejected(bad):
    with pytest.raises(PayloadError):
        PairingPayload.parse(bad)


# ---------------- pairing flow ----------------
def test_pairing_makes_both_sides_trust_each_other(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        async with secure_receiver(a) as (receiver, _, _):
            _, result = await pair(a, receiver, b)
        assert result.ok and result.peer.name == "Alice"
        assert a.trust.get(b.identity.fingerprint).name == "Bob"
        assert b.trust.get(a.identity.fingerprint).name == "Alice"
    asyncio.run(scenario())


def test_wrong_code_is_refused_and_window_closes_after_three_tries(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        async with secure_receiver(a) as (receiver, _, _):
            payload = receiver.open_pairing("127.0.0.1", decide=approve)
            wrong = dataclasses.replace(payload, token="x" * 22)
            for _ in range(3):
                r = await pair_with(wrong, b.identity, b.trust)
                assert not r.ok and r.reason == "bad_code"
            late = await pair_with(payload, b.identity, b.trust)   # even the right code
            assert not late.ok and late.reason == "pairing_closed"
        assert a.trust.peers() == [] and b.trust.peers() == []
    asyncio.run(scenario())


def test_wrong_fingerprint_aborts_before_sending_the_code(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        async with secure_receiver(a) as (receiver, _, _):
            payload = receiver.open_pairing("127.0.0.1", decide=approve)
            impostor = dataclasses.replace(payload, fingerprint="0" * 64)
            r = await pair_with(impostor, b.identity, b.trust)
            assert not r.ok and r.reason == "fingerprint_mismatch"
            ok = await pair_with(payload, b.identity, b.trust)    # token was not burned
            assert ok.ok
    asyncio.run(scenario())


def test_pairing_window_is_single_use(tmp_path):
    async def scenario():
        a, b, c = Device(tmp_path, "Alice"), Device(tmp_path, "Bob"), Device(tmp_path, "Carol")
        async with secure_receiver(a) as (receiver, _, _):
            payload, first = await pair(a, receiver, b)
            assert first.ok
            second = await pair_with(payload, c.identity, c.trust)
            assert not second.ok and second.reason == "pairing_closed"
        assert a.trust.get(c.identity.fingerprint) is None
    asyncio.run(scenario())


def test_pairing_window_expires(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        async with secure_receiver(a) as (receiver, _, _):
            payload = receiver.open_pairing("127.0.0.1", ttl=0.05, decide=approve)
            await asyncio.sleep(0.2)
            r = await pair_with(payload, b.identity, b.trust)
            assert not r.ok and r.reason == "pairing_closed"
    asyncio.run(scenario())


def test_host_can_decline_pairing(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        async with secure_receiver(a) as (receiver, _, _):
            _, r = await pair(a, receiver, b, decide=deny)
        assert not r.ok and r.reason == "declined"
        assert a.trust.peers() == [] and b.trust.peers() == []
    asyncio.run(scenario())


def test_pairing_closed_by_default(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        async with secure_receiver(a) as (receiver, port, _):
            fake = PairingPayload("127.0.0.1", port, a.identity.fingerprint, "T" * 22, "Alice")
            r = await pair_with(fake, b.identity, b.trust)
            assert not r.ok and r.reason == "pairing_closed"
    asyncio.run(scenario())


# ---------------- encrypted transfers ----------------
def test_encrypted_transfer_both_directions_with_verified_names(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        src = tmp_path / "doc.bin"
        src.write_bytes(os.urandom(2_500_000))
        async with secure_receiver(a) as (ra, port_a, events_a):
            _, paired = await pair(a, ra, b)
            assert paired.ok
            result = await send_file("127.0.0.1", port_a, src, sender_name="Totally Not Bob",
                                     identity=b.identity, peer=b.trust.get(a.identity.fingerprint))
            assert result.ok
            assert events_a[0].peer == "Bob"                       # the verified name, not the claim
        assert hashlib.sha256((a.inbox / "doc.bin").read_bytes()).hexdigest() == \
            hashlib.sha256(src.read_bytes()).hexdigest()
        async with secure_receiver(b) as (rb, port_b, events_b):   # and the other way round
            back = await send_file("127.0.0.1", port_b, src, sender_name="x", identity=a.identity,
                                   peer=a.trust.get(b.identity.fingerprint))
            assert back.ok and events_b[0].peer == "Alice"
    asyncio.run(scenario())


def test_connection_really_is_tls_1_3(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(a, b)
        async with secure_receiver(a) as (_, port, _):
            ctx = tls.build_client_context(b.identity, a.identity.cert_pem)
            reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx,
                                                           server_hostname="aircatch")
            assert writer.get_extra_info("ssl_object").version() == "TLSv1.3"
            writer.close()
            old = tls.build_client_context(b.identity, a.identity.cert_pem)
            old.minimum_version = ssl.TLSVersion.TLSv1_2
            old.maximum_version = ssl.TLSVersion.TLSv1_2
            with pytest.raises((ssl.SSLError, ConnectionError)):   # old TLS is refused
                await asyncio.open_connection("127.0.0.1", port, ssl=old, server_hostname="aircatch")
    asyncio.run(scenario())


def test_unpaired_device_cannot_send(tmp_path):
    async def scenario():
        a, mallory = Device(tmp_path, "Alice"), Device(tmp_path, "Mallory")
        src = tmp_path / "x.txt"
        src.write_text("data")
        async with secure_receiver(a) as (_, port, _):
            knows_alice = TrustedPeer.from_identity(a.identity)   # she has Alice's public certificate
            result = await send_file("127.0.0.1", port, src, sender_name="Mallory",
                                     identity=mallory.identity, peer=knows_alice)
        assert not result.ok and result.reason in ("tls_error", "connection_lost", "not_paired")
        assert not a.inbox.exists() or list(a.inbox.glob("*")) in ([], [a.inbox / ".aircatch-incoming"])
    asyncio.run(scenario())


def test_unpairing_takes_effect_immediately(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        src = tmp_path / "x.txt"
        src.write_text("data")
        async with secure_receiver(a) as (ra, port, _):
            _, paired = await pair(a, ra, b)
            assert paired.ok
            peer = b.trust.get(a.identity.fingerprint)
            assert (await send_file("127.0.0.1", port, src, sender_name="b",
                                    identity=b.identity, peer=peer)).ok
            assert a.trust.remove(b.identity.fingerprint)
            after = await send_file("127.0.0.1", port, src, sender_name="b",
                                    identity=b.identity, peer=peer)
            assert not after.ok and after.reason == "not_paired"
    asyncio.run(scenario())


def test_sender_refuses_a_receiver_with_a_different_certificate(tmp_path):
    async def scenario():
        a, b, evil = Device(tmp_path, "Alice"), Device(tmp_path, "Bob"), Device(tmp_path, "Evil")
        src = tmp_path / "x.txt"
        src.write_text("secret")
        pair_directly(evil, b)
        async with secure_receiver(evil) as (_, port, events):
            # Bob believes he is talking to Alice, but Evil answers at that address.
            alice_as_known_by_bob = TrustedPeer.from_identity(a.identity)
            result = await send_file("127.0.0.1", port, src, sender_name="Bob",
                                     identity=b.identity, peer=alice_as_known_by_bob)
        assert not result.ok and result.reason in ("untrusted_peer", "tls_error")
        assert events == []                                      # not even an offer was seen
    asyncio.run(scenario())


def test_plaintext_client_cannot_talk_to_a_secure_receiver(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        pair_directly(a, b)
        src = tmp_path / "x.txt"
        src.write_text("hello")
        async with secure_receiver(a) as (_, port, events):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)   # no TLS
            await send_message(writer, {"type": "offer", "version": 1, "transfer_id": "p1",
                                        "sender_name": "x", "filename": "a", "size": 1})
            with pytest.raises((asyncio.IncompleteReadError, ConnectionError, Exception)):
                await asyncio.wait_for(read_message(reader, 3), 5)
            writer.close()
            ok = await send_file("127.0.0.1", port, src, sender_name="b", identity=b.identity,
                                 peer=b.trust.get(a.identity.fingerprint))
            assert ok.ok                                          # receiver still healthy
        assert all(e.kind != "offer" or e.peer == "Bob" for e in events)
    asyncio.run(scenario())


def test_secure_receiver_requires_identity_and_trust_together(tmp_path):
    a = Device(tmp_path, "Alice")
    with pytest.raises(ValueError):
        Receiver(tmp_path / "in", accept_all, identity=a.identity)
    with pytest.raises(ValueError):
        asyncio.run(send_file("127.0.0.1", 1, tmp_path / "x", sender_name="a", identity=a.identity))


def test_unpairing_from_another_program_is_seen_by_a_running_receiver(tmp_path):
    """Like running `unpair` in a second terminal while the receiver is on."""
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        src = tmp_path / "x.txt"
        src.write_text("data")
        async with secure_receiver(a) as (ra, port, _):
            _, paired = await pair(a, ra, b)
            assert paired.ok
            peer = b.trust.get(a.identity.fingerprint)
            other_program = TrustStore(a.config / "trusted_peers.json")
            assert other_program.remove(b.identity.fingerprint)
            result = await send_file("127.0.0.1", port, src, sender_name="b",
                                     identity=b.identity, peer=peer)
            assert not result.ok and result.reason == "not_paired"
    asyncio.run(scenario())


def test_deleting_the_trust_file_removes_all_trust(tmp_path):
    a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
    a.trust.add(TrustedPeer.from_identity(b.identity))
    assert a.trust.get(b.identity.fingerprint) is not None
    (a.config / "trusted_peers.json").unlink()
    assert a.trust.get(b.identity.fingerprint) is None
