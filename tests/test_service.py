"""End-to-end tests for the app's network service: two real devices in one process.

Real TLS, real pairing, real files over loopback. mDNS advertising is switched off (it is
covered elsewhere); sending uses the manual address, which the app also offers as a fallback.
These prove the logic. They do not prove two physical laptops can reach each other.
"""
import io
import threading
import time

import pytest
from PIL import Image

from aircatch.pairing.payload import PairingPayload
from aircatch import service as service_module
from aircatch.service import NetworkService, parse_address, reason_text


class Sink:
    """Collects events from a service thread and lets a test wait for one."""

    def __init__(self):
        self.events = []
        self._cond = threading.Condition()

    def __call__(self, kind, payload):
        with self._cond:
            self.events.append((kind, payload))
            self._cond.notify_all()

    def wait_for(self, kind, predicate=lambda p: True, timeout=15.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for k, p in self.events:
                    if k == kind and predicate(p):
                        return p
                left = deadline - time.monotonic()
                assert left > 0, f"timed out waiting for {kind}; saw {[k for k, _ in self.events]}"
                self._cond.wait(left)

    def kinds(self):
        with self._cond:
            return [k for k, _ in self.events]


def make_service(tmp_path, name, **kwargs):
    sink = Sink()
    service = NetworkService(sink, config_dir=tmp_path / name / "cfg",
                             download_dir=tmp_path / name / "inbox", name=name,
                             advertise=False, **kwargs)
    service.start()
    assert service.ready.wait(20), "service did not start"
    assert not service.start_error, service.start_error
    return service, sink


@pytest.fixture
def pair(tmp_path):
    a, sa = make_service(tmp_path, "Laptop-A")
    b, sb = make_service(tmp_path, "Laptop-B")
    yield a, sa, b, sb, tmp_path
    a.stop()
    b.stop()


def jpeg_bytes(size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 140, 220)).save(buf, "JPEG", quality=70)
    return buf.getvalue()


def do_pairing(a, sa, b, sb):
    """A shows a code; B joins it; A's user approves."""
    uri = a.open_pairing()
    assert uri.startswith("aircatch://pair?")
    payload = PairingPayload.parse(uri)
    local = PairingPayload(host="127.0.0.1", port=payload.port, fingerprint=payload.fingerprint,
                           token=payload.token, name=payload.name).to_uri()
    b.join_pairing(local)
    request = sa.wait_for("pairing_request")
    assert request["name"] == "Laptop-B"
    a.answer_pairing(True)
    result = sb.wait_for("join_result")
    assert result["ok"], result
    sa.wait_for("paired")


def test_parse_address():
    assert parse_address("192.168.1.23:5001") == ("192.168.1.23", 5001)
    assert parse_address(" my-pc.local:80 ") == ("my-pc.local", 80)
    for bad in ("", "nope", "1.2.3.4", "1.2.3.4:0", "1.2.3.4:99999", "1.2.3.4:abc", "a b:5",
                "../x:5"):
        assert parse_address(bad) is None


def test_every_known_reason_has_readable_text():
    assert "pair" in reason_text("not_paired").lower()
    assert "boom" in reason_text("boom")     # unknown codes still produce a sentence


def test_pair_then_send_both_ways_with_preview(pair):
    a, sa, b, sb, tmp = pair
    do_pairing(a, sa, b, sb)

    # B -> A with a preview image
    source = tmp / "photo.bin"
    source.write_bytes(b"hello airCatch" * 1000)
    preview = jpeg_bytes()
    b.send(a.identity.fingerprint, str(source), preview, f"127.0.0.1:{a.port}")
    offer = sa.wait_for("offer")
    assert offer["filename"] == "photo.bin" and offer["size"] == source.stat().st_size
    assert offer["verified"] is True and offer["sender"] == "Laptop-B"
    assert offer["thumbnail"] == preview
    a.answer_offer(offer["transfer_id"], True)
    done = sa.wait_for("transfer", lambda p: p["event"].kind == "completed")["event"]
    assert (tmp / "Laptop-A" / "inbox" / "photo.bin").read_bytes() == source.read_bytes()
    assert done.path.endswith("photo.bin")
    result = sb.wait_for("send_result")
    assert result["ok"] and result["saved_as"] == "photo.bin"
    kinds = [p["event"].kind for k, p in sb.events if k == "transfer"]
    assert kinds[0] == "offer" and kinds[-1] == "completed" and "progress" in kinds

    # A -> B: proves the joining side was let through TLS immediately (allow_peer)
    back = tmp / "reply.txt"
    back.write_text("reply")
    a.send(b.identity.fingerprint, str(back), None, f"127.0.0.1:{b.port}")
    offer = sb.wait_for("offer")
    assert offer["thumbnail"] == b""             # no preview was sent
    b.answer_offer(offer["transfer_id"], True)
    assert sa.wait_for("send_result")["ok"]
    assert (tmp / "Laptop-B" / "inbox" / "reply.txt").read_text() == "reply"


def test_decline_is_reported_and_nothing_is_saved(pair):
    a, sa, b, sb, tmp = pair
    do_pairing(a, sa, b, sb)
    source = tmp / "x.txt"
    source.write_text("no thanks")
    b.send(a.identity.fingerprint, str(source), None, f"127.0.0.1:{a.port}")
    offer = sa.wait_for("offer")
    a.answer_offer(offer["transfer_id"], False)
    result = sb.wait_for("send_result")
    assert not result["ok"] and result["reason"] == "declined"
    closed = sa.wait_for("offer_closed")
    assert closed["accepted"] is False
    assert not list((tmp / "Laptop-A" / "inbox").glob("x*"))


def test_unanswered_offer_times_out_and_is_declined(tmp_path):
    a, sa = make_service(tmp_path, "Laptop-A", decide_timeout=0.6)
    b, sb = make_service(tmp_path, "Laptop-B")
    try:
        do_pairing(a, sa, b, sb)
        source = tmp_path / "slow.txt"
        source.write_text("nobody answers")
        b.send(a.identity.fingerprint, str(source), None, f"127.0.0.1:{a.port}")
        sa.wait_for("offer")
        sa.wait_for("offer_closed")
        result = sb.wait_for("send_result")
        assert not result["ok"] and result["reason"] == "declined"
    finally:
        a.stop()
        b.stop()


def test_cannot_send_to_unpaired_or_unknown_device(pair):
    a, sa, b, sb, tmp = pair
    source = tmp / "y.txt"
    source.write_text("y")
    b.send(a.identity.fingerprint, str(source), None, f"127.0.0.1:{a.port}")   # never paired
    assert sb.wait_for("send_result")["reason"] == "not_paired"
    b.send("", str(source), None, "")
    assert sb.wait_for("send_result", lambda p: p["reason"] == "not_paired")


def test_bad_manual_address_is_reported(pair):
    a, sa, b, sb, tmp = pair
    do_pairing(a, sa, b, sb)
    source = tmp / "z.txt"
    source.write_text("z")
    b.send(a.identity.fingerprint, str(source), None, "not an address")
    assert sb.wait_for("send_result")["reason"] == "bad_address"


def test_unpairing_blocks_later_sends(pair):
    a, sa, b, sb, tmp = pair
    do_pairing(a, sa, b, sb)
    assert a.unpair(b.identity.fingerprint) is True
    source = tmp / "w.txt"
    source.write_text("w")
    b.send(a.identity.fingerprint, str(source), None, f"127.0.0.1:{a.port}")
    result = sb.wait_for("send_result")
    assert not result["ok"] and result["reason"] == "not_paired"
    assert "offer" not in sa.kinds()


def test_declined_pairing_leaves_devices_unpaired(pair):
    a, sa, b, sb, tmp = pair
    uri = a.open_pairing()
    payload = PairingPayload.parse(uri)
    b.join_pairing(PairingPayload("127.0.0.1", payload.port, payload.fingerprint, payload.token,
                                  payload.name).to_uri())
    sa.wait_for("pairing_request")
    a.answer_pairing(False)
    result = sb.wait_for("join_result")
    assert not result["ok"]
    assert b.trust.peers() == [] and a.trust.peers() == []


def test_garbage_pairing_text_is_a_friendly_error(pair):
    a, sa, b, sb, tmp = pair
    b.join_pairing("hello")
    result = sb.wait_for("join_result")
    assert not result["ok"] and "pairing code" in result["text"]


def test_stop_is_clean_and_repeatable(tmp_path):
    a, sa = make_service(tmp_path, "Laptop-A")
    a.stop()
    assert not a.is_alive()
    a.stop()                                      # second call is harmless
    a.send("abc", str(tmp_path / "nothing"), None, "")
    assert sa.wait_for("send_result")["ok"] is False


def test_port_in_use_is_reported_not_raised(tmp_path):
    a, sa = make_service(tmp_path, "Laptop-A")
    try:
        sink = Sink()
        second = NetworkService(sink, config_dir=tmp_path / "other" / "cfg",
                                download_dir=tmp_path / "other" / "inbox", name="Other",
                                port=a.port, advertise=False)
        second.start()
        second.ready.wait(20)
        status = sink.wait_for("status")
        assert status["ready"] is False and "port" in status["text"].lower()
        second.stop()
    finally:
        a.stop()


def test_the_real_network_adapter_is_preferred_over_virtual_ones(monkeypatch):
    # A laptop with WSL / Hyper-V / VirtualBox has extra adapters that sort before the Wi-Fi one.
    monkeypatch.setattr(service_module, "lan_ipv4_addresses",
                        lambda: ["10.0.75.1", "172.20.0.1", "192.168.1.23", "192.168.56.1"])
    monkeypatch.setattr(service_module, "local_ip", lambda: "192.168.1.23")
    assert service_module.lan_addresses_preferred_first()[0] == "192.168.1.23"
    assert len(service_module.lan_addresses_preferred_first()) == 4      # others are kept as backups
    monkeypatch.setattr(service_module, "local_ip", lambda: "127.0.0.1")  # no clue: keep the old order
    assert service_module.lan_addresses_preferred_first()[0] == "10.0.75.1"
    monkeypatch.setattr(service_module, "lan_ipv4_addresses", lambda: [])
    assert service_module.lan_addresses_preferred_first() == []


def test_the_pairing_code_carries_the_real_address(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "lan_ipv4_addresses", lambda: ["172.20.0.1", "192.168.1.23"])
    monkeypatch.setattr(service_module, "local_ip", lambda: "192.168.1.23")
    a, sa = make_service(tmp_path, "Laptop-A")
    try:
        assert PairingPayload.parse(a.open_pairing()).host == "192.168.1.23"
    finally:
        a.stop()


# ====================================================== addresses, fallbacks, diagnostics
import socket  # noqa: E402

from aircatch.discovery.connect import order_addresses  # noqa: E402
from aircatch.service import KnownAddresses  # noqa: E402


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port                                                  # nothing listens here now


@pytest.fixture
def quick_pair(tmp_path):
    a, sa = make_service(tmp_path, "Laptop-A", find_timeout=0.3)
    b, sb = make_service(tmp_path, "Laptop-B", find_timeout=0.3)
    yield a, sa, b, sb, tmp_path
    a.stop()
    b.stop()


def test_pairing_teaches_both_devices_where_the_other_one_lives(quick_pair):
    a, sa, b, sb, tmp = quick_pair
    do_pairing(a, sa, b, sb)
    assert b.known.get(a.identity.fingerprint) == ("127.0.0.1", a.port)    # from the code
    assert a.known.get(b.identity.fingerprint) == ("127.0.0.1", b.port)    # the joiner reported its port


def test_sending_works_without_discovery_or_a_typed_address(quick_pair):
    """The case that matters on networks where mDNS is blocked: no list entry, no typed address."""
    a, sa, b, sb, tmp = quick_pair
    do_pairing(a, sa, b, sb)
    for sender, receiver, s_sink, r_sink, name in ((b, a, sb, sa, "b2a.txt"), (a, b, sa, sb, "a2b.txt")):
        source = tmp / name
        source.write_text(name)
        sender.send(receiver.identity.fingerprint, str(source), None, "")      # no manual address
        steps = s_sink.wait_for("send_step")
        assert "Looking for" in steps["text"]
        offer = r_sink.wait_for("offer", lambda p, n=name: p["filename"] == n)
        receiver.answer_offer(offer["transfer_id"], True)
        result = s_sink.wait_for("send_result", lambda p, n=name: p["filename"] == n)
        assert result["ok"], result
    assert (tmp / "Laptop-A" / "inbox" / "b2a.txt").read_text() == "b2a.txt"
    assert (tmp / "Laptop-B" / "inbox" / "a2b.txt").read_text() == "a2b.txt"


def test_a_wrong_saved_address_fails_quickly_with_a_helpful_reason(quick_pair):
    a, sa, b, sb, tmp = quick_pair
    do_pairing(a, sa, b, sb)
    b.known.set(a.identity.fingerprint, "127.0.0.1", free_port())
    source = tmp / "lost.txt"
    source.write_text("x")
    started = time.monotonic()
    b.send(a.identity.fingerprint, str(source), None, "")
    result = sb.wait_for("send_result")
    assert time.monotonic() - started < 8
    assert not result["ok"] and result["reason"] == "could_not_connect"
    assert "Firewall" in result["text"] and "Test connection" in result["text"]
    assert "offer" not in sa.kinds()


def test_nothing_known_and_nothing_found_is_not_found(quick_pair):
    a, sa, b, sb, tmp = quick_pair
    do_pairing(a, sa, b, sb)
    b.known.forget(a.identity.fingerprint)
    source = tmp / "n.txt"
    source.write_text("x")
    b.send(a.identity.fingerprint, str(source), None, "")
    result = sb.wait_for("send_result")
    assert result["reason"] == "not_found" and "address" in result["text"].lower()


def test_unpairing_forgets_the_saved_address(quick_pair):
    a, sa, b, sb, tmp = quick_pair
    do_pairing(a, sa, b, sb)
    assert a.known.get(b.identity.fingerprint) is not None
    a.unpair(b.identity.fingerprint)
    assert a.known.get(b.identity.fingerprint) is None


def test_test_connection_reports_success_and_failure(quick_pair):
    a, sa, b, sb, tmp = quick_pair
    b.test_connection("", "")
    assert "Select a paired device" in sb.wait_for("connection_result")["text"]
    do_pairing(a, sa, b, sb)
    b.test_connection(a.identity.fingerprint, "")                       # uses the saved address
    ok = sb.wait_for("connection_result", lambda p: p["ok"])
    assert "Connected to Laptop-A" in ok["text"] and str(a.port) in ok["text"]
    b.test_connection(a.identity.fingerprint, f"127.0.0.1:{free_port()}")
    bad = sb.wait_for("connection_result", lambda p: not p["ok"] and "Could not connect" in p["text"])
    assert "Firewall" in bad["text"]
    b.test_connection(a.identity.fingerprint, "nonsense")
    assert "not valid" in sb.wait_for("connection_result", lambda p: "not valid" in p["text"])["text"]
    assert "offer" not in sa.kinds()                                     # a test never sends anything


def test_test_connection_notices_the_wrong_device_at_an_address(tmp_path):
    a, sa = make_service(tmp_path, "Laptop-A", find_timeout=0.3)
    b, sb = make_service(tmp_path, "Laptop-B", find_timeout=0.3)
    c, sc = make_service(tmp_path, "Stranger", find_timeout=0.3)
    try:
        do_pairing(a, sa, b, sb)
        b.test_connection(a.identity.fingerprint, f"127.0.0.1:{c.port}")   # that is NOT Laptop-A
        result = sb.wait_for("connection_result", lambda p: not p["ok"])
        assert "not the device you paired with" in result["text"]
    finally:
        a.stop(); b.stop(); c.stop()


def test_a_joiner_that_does_not_report_its_port_still_pairs(quick_pair):
    """Older versions never sent listen_port. Pairing must still work; the host just cannot learn the address."""
    import asyncio
    from aircatch.pairing.joiner import pair_with
    a, sa, b, sb, tmp = quick_pair
    payload = PairingPayload.parse(a.open_pairing())
    local = PairingPayload("127.0.0.1", payload.port, payload.fingerprint, payload.token, payload.name)
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.setdefault(
        "r", asyncio.run(pair_with(local, b.identity, b.trust))))
    thread.start()
    sa.wait_for("pairing_request")
    a.answer_pairing(True)
    thread.join(15)
    assert outcome["r"].ok
    assert a.known.get(b.identity.fingerprint) is None


def test_known_addresses_store(tmp_path):
    path = tmp_path / "known.json"
    store = KnownAddresses(path)
    assert store.set("id1", "192.168.1.5", 5001) is True
    assert store.set("id1", "192.168.1.5", 5001) is False                # unchanged
    assert store.set("id2", "bad host!", 5001) is False                  # invalid host
    assert store.set("id2", "192.168.1.6", 0) is False                   # invalid port
    assert store.set("id2", "192.168.1.6", 70000) is False
    assert KnownAddresses(path).get("id1") == ("192.168.1.5", 5001)      # survives a restart
    store.forget("id1")
    assert KnownAddresses(path).get("id1") is None
    path.write_text("{ this is not json")
    assert KnownAddresses(path).get("id1") is None                       # corrupt file is ignored
    path.write_text('{"x": {"host": 5, "port": "no"}, "y": "z"}')
    assert KnownAddresses(path).get("x") is None


def test_addresses_on_our_own_network_are_tried_first():
    mine = ["192.168.1.20", "172.28.80.1"]
    ordered = order_addresses(("10.0.75.1", "192.168.56.1", "192.168.1.7", "172.28.80.9"), mine)
    assert ordered[:2] == ["192.168.1.7", "172.28.80.9"] and len(ordered) == 4
    assert order_addresses(("192.168.1.7",), []) == ["192.168.1.7"]


def test_the_log_file_records_decisions_but_not_secrets(tmp_path):
    import faulthandler
    import logging
    import sys
    from aircatch import logsetup
    path = logsetup.setup_logging(tmp_path / "cfg")
    try:
        assert path == tmp_path / "cfg" / "aircatch.log"
        assert logsetup.setup_logging(tmp_path / "cfg") == path          # second call adds no duplicate
        handlers = [h for h in logging.getLogger("aircatch").handlers if getattr(h, "_aircatch_file", False)]
        assert len(handlers) == 1
        logging.getLogger("aircatch.app").info("send started: 'a.txt' to 'Laptop-B'")
        handlers[0].flush()
        text = path.read_text("utf-8")
        assert "AirCatch started" in text and "send started: 'a.txt'" in text
        assert (tmp_path / "cfg" / "crash.log").exists()
    finally:
        for h in list(logging.getLogger("aircatch").handlers):
            if getattr(h, "_aircatch_file", False):
                h.close()
                logging.getLogger("aircatch").removeHandler(h)
        faulthandler.disable()
        sys.excepthook = sys.__excepthook__
        if logsetup._crash_file is not None:
            logsetup._crash_file.close()


def test_services_never_write_secrets_to_the_log(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="aircatch")
    a, sa = make_service(tmp_path, "Laptop-A", find_timeout=0.3)
    b, sb = make_service(tmp_path, "Laptop-B", find_timeout=0.3)
    try:
        uri = a.open_pairing()
        token = PairingPayload.parse(uri).token
        payload = PairingPayload.parse(uri)
        b.join_pairing(PairingPayload("127.0.0.1", payload.port, payload.fingerprint, token, payload.name).to_uri())
        sa.wait_for("pairing_request")
        a.answer_pairing(True)
        sb.wait_for("join_result")
    finally:
        a.stop(); b.stop()
    logged = caplog.text
    assert "paired with" in logged
    assert token not in logged and "BEGIN CERTIFICATE" not in logged and "PRIVATE KEY" not in logged
