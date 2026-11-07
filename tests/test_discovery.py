"""Discovery tests.

The 'real mDNS' tests use the loopback interface, so they prove the code works with the
discovery library, but NOT that your router and firewall let multicast through between two
real devices. They are skipped automatically on machines where mDNS cannot run.
"""
import asyncio
import os

import pytest
from zeroconf.asyncio import AsyncZeroconf

from aircatch.discovery.browser import Discovery
from aircatch.discovery.connect import send_to_discovered
from aircatch.discovery.net import is_acceptable_address, lan_ipv4_addresses
from aircatch.discovery.service import (Advertiser, Advertisement, discovery_hint,
                                        parse_advertisement)
from aircatch.security.identity import load_or_create
from aircatch.security.trust import TrustedPeer, TrustStore
from aircatch.transfer import Receiver


class Device:
    def __init__(self, tmp_path, name):
        self.config = tmp_path / name.lower()
        self.identity = load_or_create(self.config, name)
        self.trust = TrustStore(self.config / "trusted_peers.json")
        self.inbox = self.config / "inbox"

    def trusts(self, other):
        self.trust.add(TrustedPeer.from_identity(other.identity))


def advert(device, *, port=5001, addresses=("192.168.1.9",), name=None, service=None):
    hint = discovery_hint(device.identity.fingerprint)
    return Advertisement(service or f"AirCatch-{hint[:8]}._aircatch._tcp.local.",
                         name or device.identity.name, hint, tuple(addresses), port)


async def accept_all(_offer):
    return True


# ---------------- hints and parsing ----------------
def test_hint_is_stable_short_and_not_the_device_id(tmp_path):
    a, b = Device(tmp_path, "A"), Device(tmp_path, "B")
    hint = discovery_hint(a.identity.fingerprint)
    assert hint == discovery_hint(a.identity.fingerprint) and len(hint) == 16
    assert hint != discovery_hint(b.identity.fingerprint)
    assert hint not in a.identity.fingerprint


GOOD = {b"v": b"1", b"h": b"ab" * 8, b"n": b"Laptop"}


def test_parse_valid_advertisement():
    adv = parse_advertisement("svc", GOOD, ["192.168.1.5"], 5001)
    assert adv.name == "Laptop" and adv.port == 5001 and adv.addresses == ("192.168.1.5",)


@pytest.mark.parametrize("props", [
    {**GOOD, b"v": b"2"}, {**GOOD, b"h": b"short"}, {**GOOD, b"h": b"ZZ" * 8},
    {k: v for k, v in GOOD.items() if k != b"n"}, {**GOOD, b"n": b"x" * 500},
    {**GOOD, b"n": b"\xff\xfe"}, {**GOOD, b"v": None}, {},
])
def test_malformed_advertisements_are_ignored(props):
    assert parse_advertisement("svc", props, ["192.168.1.5"], 5001) is None


def test_bad_port_or_no_usable_address_is_ignored():
    assert parse_advertisement("svc", GOOD, ["192.168.1.5"], 0) is None
    assert parse_advertisement("svc", GOOD, ["192.168.1.5"], 70000) is None
    assert parse_advertisement("svc", GOOD, ["8.8.8.8"], 5001) is None
    assert parse_advertisement("svc", GOOD, [], 5001) is None


def test_names_in_advertisements_are_cleaned():
    adv = parse_advertisement("svc", {**GOOD, b"n": b"  Bob\x00\n PC "}, ["10.0.0.2"], 5001)
    assert adv.name == "Bob PC"


def test_public_and_odd_addresses_are_never_used_as_targets():
    assert not any(is_acceptable_address(x) for x in
                   ["8.8.8.8", "1.1.1.1", "0.0.0.0", "224.0.0.251", "::1", "fe80::1", "nonsense", ""])
    assert all(is_acceptable_address(x) for x in
               ["192.168.1.5", "10.1.2.3", "172.16.0.4", "169.254.1.1", "127.0.0.1", "100.64.1.1"])


def test_lan_addresses_exclude_loopback():
    assert all(not a.startswith("127.") for a in lan_ipv4_addresses())


# ---------------- discovery logic ----------------
def test_paired_device_is_recognised_and_stranger_is_labelled_unpaired(tmp_path):
    async def scenario():
        me, friend, stranger = Device(tmp_path, "Me"), Device(tmp_path, "Friend"), Device(tmp_path, "Stranger")
        me.trusts(friend)
        disc = Discovery(me.trust, own_identity=me.identity)
        disc.ingest(advert(friend, addresses=["192.168.1.10"]))
        disc.ingest(advert(stranger, addresses=["192.168.1.11"]))
        found = {d.name: d for d in disc.devices()}
        assert found["Friend"].paired and found["Friend"].peer.name == "Friend"
        assert not found["Stranger"].paired
        assert [d.name for d in disc.devices()][0] == "Friend"       # paired devices first
    asyncio.run(scenario())


def test_own_advertisement_is_not_listed(tmp_path):
    async def scenario():
        me = Device(tmp_path, "Me")
        disc = Discovery(me.trust, own_identity=me.identity)
        disc.ingest(advert(me))
        assert disc.devices() == []
    asyncio.run(scenario())


def test_pairing_and_unpairing_show_up_live(tmp_path):
    async def scenario():
        me, friend = Device(tmp_path, "Me"), Device(tmp_path, "Friend")
        disc = Discovery(me.trust)
        disc.ingest(advert(friend))
        assert not disc.devices()[0].paired
        me.trusts(friend)
        assert disc.devices()[0].paired
        me.trust.remove(friend.identity.fingerprint)
        assert not disc.devices()[0].paired
    asyncio.run(scenario())


def test_remove_and_update_and_flood_limit(tmp_path):
    async def scenario():
        me, friend = Device(tmp_path, "Me"), Device(tmp_path, "Friend")
        changes = []
        disc = Discovery(me.trust, max_entries=3, on_change=lambda: changes.append(1))
        a = advert(friend, addresses=["192.168.1.10"])
        disc.ingest(a)
        disc.ingest(advert(friend, addresses=["192.168.1.99"]))      # same service: updated
        assert len(disc.devices()) == 1 and disc.devices()[0].addresses == ("192.168.1.99",)
        for i in range(10):                                           # a flood of fake devices
            disc.ingest(Advertisement(f"fake{i}._aircatch._tcp.local.", f"F{i}", f"{i:016x}",
                                      ("192.168.1.50",), 5001))
        assert len(disc.devices()) == 3
        disc.remove(a.service_name)
        assert all(d.name != "Friend" for d in disc.devices())
        assert changes
    asyncio.run(scenario())


def test_find_waits_for_a_device_and_times_out_otherwise(tmp_path):
    async def scenario():
        me, friend = Device(tmp_path, "Me"), Device(tmp_path, "Friend")
        me.trusts(friend)
        peer = me.trust.get(friend.identity.fingerprint)
        disc = Discovery(me.trust)
        assert await disc.find(peer, timeout=0.3) is None
        async def appear_later():
            await asyncio.sleep(0.3)
            disc.ingest(advert(friend))
        task = asyncio.create_task(appear_later())
        device = await disc.find(peer, timeout=3)
        await task
        assert device is not None and device.peer.name == "Friend"
    asyncio.run(scenario())


# ---------------- sending to a discovered device ----------------
def test_send_to_discovered_device_tries_the_next_address(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        a.trusts(b)
        b.trusts(a)
        src = tmp_path / "hello.txt"
        src.write_text("found you")
        receiver = Receiver(a.inbox, accept_all, device_name="Alice", identity=a.identity, trust=a.trust)
        port = await receiver.start("127.0.0.1", 0)
        try:
            disc = Discovery(b.trust)
            # the first address is dead (nothing listens there), the second one works
            disc.ingest(advert(a, port=port, addresses=["127.0.0.2", "127.0.0.1"]))
            device = disc.devices()[0]
            result = await send_to_discovered(device, src, identity=b.identity, sender_name="Bob")
        finally:
            await receiver.stop()
        assert result.ok and (a.inbox / "hello.txt").read_text() == "found you"
    asyncio.run(scenario())


def test_cannot_send_to_an_unpaired_discovered_device(tmp_path):
    async def scenario():
        me, stranger = Device(tmp_path, "Me"), Device(tmp_path, "Stranger")
        disc = Discovery(me.trust)
        disc.ingest(advert(stranger))
        with pytest.raises(ValueError):
            await send_to_discovered(disc.devices()[0], tmp_path / "x", identity=me.identity,
                                     sender_name="Me")
    asyncio.run(scenario())


def test_discovery_cannot_make_a_sender_trust_an_impostor(tmp_path):
    """A fake advertisement may point us at the wrong device, but TLS pinning refuses it."""
    async def scenario():
        alice, bob, evil = Device(tmp_path, "Alice"), Device(tmp_path, "Bob"), Device(tmp_path, "Evil")
        bob.trusts(alice)
        evil.trusts(bob)
        src = tmp_path / "secret.txt"
        src.write_text("secret")
        events = []
        receiver = Receiver(evil.inbox, accept_all, identity=evil.identity, trust=evil.trust,
                            on_event=events.append)
        port = await receiver.start("127.0.0.1", 0)
        try:
            disc = Discovery(bob.trust)
            # Evil advertises with Alice's hint to lure Bob's files
            disc.ingest(Advertisement("fake._aircatch._tcp.local.", "Alice",
                                      discovery_hint(alice.identity.fingerprint),
                                      ("127.0.0.1",), port))
            result = await send_to_discovered(disc.devices()[0], src, identity=bob.identity,
                                              sender_name="Bob")
        finally:
            await receiver.stop()
        assert not result.ok and result.reason in ("untrusted_peer", "tls_error")
        assert events == []
    asyncio.run(scenario())


# ---------------- real mDNS on loopback ----------------
def test_real_mdns_advertise_and_find(tmp_path):
    async def scenario():
        a, b, c = Device(tmp_path, "Alice"), Device(tmp_path, "Bob"), Device(tmp_path, "Carol")
        b.trusts(a)                                    # Bob knows Alice; Carol does not
        adv_zc = AsyncZeroconf(interfaces=["127.0.0.1"])
        bob_zc = AsyncZeroconf(interfaces=["127.0.0.1"])
        carol_zc = AsyncZeroconf(interfaces=["127.0.0.1"])
        advertiser = Advertiser(a.identity, 5055, addresses=["127.0.0.1"], zeroconf=adv_zc)
        bob_disc = Discovery(b.trust, own_identity=b.identity, zeroconf=bob_zc)
        carol_disc = Discovery(c.trust, own_identity=c.identity, zeroconf=carol_zc)
        try:
            await advertiser.start()
            await bob_disc.start()
            await carol_disc.start()
            peer = b.trust.get(a.identity.fingerprint)
            found = await bob_disc.find(peer, timeout=8)
            if found is None:
                return None
            seen_by_carol = None
            for _ in range(60):
                seen_by_carol = carol_disc.devices()
                if seen_by_carol:
                    break
                await asyncio.sleep(0.1)
            return found, seen_by_carol
        finally:
            await advertiser.stop()
            await bob_disc.stop()
            await carol_disc.stop()
            for zc in (adv_zc, bob_zc, carol_zc):
                await zc.async_close()
    outcome = asyncio.run(scenario())
    if outcome is None:
        pytest.skip("mDNS is not available on this machine/network")
    found, seen_by_carol = outcome
    assert found.paired and found.peer.name == "Alice" and found.port == 5055
    assert found.addresses == ("127.0.0.1",)
    assert seen_by_carol and not seen_by_carol[0].paired      # a stranger sees only a label
    assert seen_by_carol[0].name == "Alice"


def test_real_mdns_device_disappears_when_it_stops(tmp_path):
    async def scenario():
        a, b = Device(tmp_path, "Alice"), Device(tmp_path, "Bob")
        b.trusts(a)
        adv_zc = AsyncZeroconf(interfaces=["127.0.0.1"])
        bob_zc = AsyncZeroconf(interfaces=["127.0.0.1"])
        advertiser = Advertiser(a.identity, 5056, addresses=["127.0.0.1"], zeroconf=adv_zc)
        disc = Discovery(b.trust, zeroconf=bob_zc)
        try:
            await advertiser.start()
            await disc.start()
            if await disc.find(b.trust.get(a.identity.fingerprint), timeout=8) is None:
                return None
            await advertiser.stop()                    # polite goodbye packet
            for _ in range(60):
                if not disc.devices():
                    return True
                await asyncio.sleep(0.1)
            return False
        finally:
            await disc.stop()
            await adv_zc.async_close()
            await bob_zc.async_close()
    outcome = asyncio.run(scenario())
    if outcome is None:
        pytest.skip("mDNS is not available on this machine/network")
    assert outcome is True


def test_discovered_devices_carry_their_hint(tmp_path):
    async def scenario():
        me, friend = Device(tmp_path, "Me"), Device(tmp_path, "Friend")
        disc = Discovery(me.trust)
        disc.ingest(advert(friend))
        assert disc.devices()[0].hint == discovery_hint(friend.identity.fingerprint)
    asyncio.run(scenario())
