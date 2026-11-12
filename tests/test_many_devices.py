"""Several complete app brains in one process (real TLS, real files, loopback).

Answers "what happens with more than two devices?" without needing more than one PC:
  * one hub paired with several devices sends to exactly the one that is selected;
  * several devices throwing at one receiver at the same time: one is served, the rest are told "busy";
  * after a delivered file the sender's card is cleared so a new file can be chosen.
Camera and screen are faked. This does not prove physical Wi-Fi reachability or mDNS across a real LAN.
"""
import time

import pytest

from aircatch.pairing.payload import PairingPayload
from aircatch.ui.controller import AppController, AppSettings, ImageProvider, ThumbProvider
from tests.fakes import FakeWorker

NAMES = ["Hub", "Phone-1", "Laptop-2", "Tablet-3"]


def make_app(qapp, tmp_path, name):
    app = AppController(ImageProvider(), ImageProvider(), ThumbProvider(),
                         AppSettings(tmp_path / name / "cfg", tmp_path / name / "inbox", name=name,
                                     advertise=False), worker_factory=FakeWorker)
    wait(qapp, lambda: app.networkReady, f"{name} network ready")
    return app


def wait(qapp, condition, what, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for: {what}")


def pair(qapp, host, guest):
    seen = []
    guest.joinFinished.connect(lambda ok, text: seen.append((ok, text)))
    host.pairingRequest.connect(lambda name, addr: host.answerPairing(True))
    assert host.openPairing()
    p = PairingPayload.parse(host.pairingUri)
    guest.joinPairing(PairingPayload("127.0.0.1", p.port, p.fingerprint, p.token, p.name).to_uri())
    wait(qapp, lambda: seen, f"{guest.deviceName} joins {host.deviceName}")
    assert seen[-1][0], seen
    wait(qapp, lambda: any(d["paired"] for d in guest._devices.items), f"{guest.deviceName} lists a paired device")


@pytest.fixture
def swarm(qapp, tmp_path):
    apps = {n: make_app(qapp, tmp_path, n) for n in NAMES}
    hub = apps["Hub"]
    for n in NAMES[1:]:
        pair(qapp, hub, apps[n])
    wait(qapp, lambda: sum(1 for d in hub._devices.items if d["paired"]) == 3, "hub sees 3 paired devices")
    yield apps, tmp_path
    for a in apps.values():
        a.shutdown()


def select(hub, name):
    index = next(i for i, d in enumerate(hub._devices.items) if d["name"] == name and d["paired"])
    hub.selectDevice(index)
    assert hub.selectedDeviceName == name


def test_hub_sends_only_to_the_selected_device(qapp, swarm):
    apps, tmp = swarm
    hub = apps["Hub"]
    for n, target in enumerate(NAMES[1:]):
        f = tmp / f"for_{target}.txt"
        f.write_text(f"hello {target}")
        hub.chooseFile(str(f))
        select(hub, target)
        hub.setManualAddress(f"127.0.0.1:{apps[target]._service.port}")
        hub.sendSelected()
        wait(qapp, lambda: apps[target].receivePhase == "offered", f"offer on {target}")
        for other in NAMES[1:]:
            if other != target:
                assert apps[other].receivePhase == "none", f"{other} must not see {target}'s file"
        apps[target]._on_gestures([__import__("aircatch.gestures.types", fromlist=["GestureEvent"]).GestureEvent("catch", 1.0)])
        wait(qapp, lambda: apps[target].receivePhase == "arrived", f"arrival on {target}")
        wait(qapp, lambda: hub.senderPhase == "done", "hub done")
        assert (tmp / target / "inbox" / f.name).read_text() == f"hello {target}"
        hub._phase_timeout()
        assert not hub.hasFile and hub.senderPhase == "idle"      # card is gone, ready for a new file
        apps[target].dismissIncoming()
    # nobody got a file that was meant for someone else
    for n in NAMES[1:]:
        assert len([p for p in (tmp / n / "inbox").glob("*") if not p.name.startswith(".")]) == 1


def test_many_senders_one_receiver_serves_one_and_reports_busy_to_the_rest(qapp, swarm):
    apps, tmp = swarm
    hub = apps["Hub"]
    senders = [apps[n] for n in NAMES[1:]]
    for s in senders:
        f = tmp / f"from_{s.deviceName}.txt"
        f.write_text(s.deviceName)
        s.chooseFile(str(f))
        s.setManualAddress(f"127.0.0.1:{hub._service.port}")
    for s in senders:
        s.sendSelected()
    wait(qapp, lambda: hub.receivePhase == "offered", "hub shows one offer")
    wait(qapp, lambda: sum(1 for s in senders if s.senderPhase == "failed") == len(senders) - 1,
         "the others are told it is busy")
    losers = [s for s in senders if s.senderPhase == "failed"]
    assert all("busy" in s.senderMessage.lower() for s in losers), [s.senderMessage for s in losers]
    winner = next(s for s in senders if s.senderPhase != "failed")
    assert hub.incomingSender == winner.deviceName
    # losers keep their file for a retry; the winner is delivered and cleared
    assert all(s.hasFile for s in losers)
    from aircatch.gestures.types import GestureEvent
    hub._on_gestures([GestureEvent("catch", 1.0)])
    wait(qapp, lambda: winner.senderPhase == "done", "winner delivered")
    wait(qapp, lambda: hub.receivePhase == "arrived", "hub got it")
    winner._phase_timeout()
    assert not winner.hasFile
