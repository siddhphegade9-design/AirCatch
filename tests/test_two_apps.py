"""Two complete app brains (controller + real network service) in one process.

This is the one-PC demo path: pair, pick an image, send, see the preview pop up on the other side,
catch it, and get a verified file. Real TLS and real files over loopback; only the camera and the
screen are missing. It does NOT prove two physical laptops can reach each other.
"""
import time

import pytest
from PIL import Image

from aircatch.gestures.types import GestureEvent
from aircatch.pairing.payload import PairingPayload
from aircatch.ui.controller import AppController, AppSettings, ImageProvider, ThumbProvider
from tests.fakes import FakeWorker


def make_app(qapp, tmp_path, name):
    app = AppController(ImageProvider(), ImageProvider(), ThumbProvider(),
                         AppSettings(tmp_path / name / "cfg", tmp_path / name / "inbox", name=name,
                                     advertise=False), worker_factory=FakeWorker)
    wait(qapp, lambda: app.networkReady, f"{name} network ready")   # start devices one at a time
    return app


def wait(qapp, condition, what, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for: {what}")


@pytest.fixture
def apps(qapp, tmp_path):
    a, b = make_app(qapp, tmp_path, "Laptop-A"), make_app(qapp, tmp_path, "Laptop-B")
    yield a, b, tmp_path
    a.shutdown()
    b.shutdown()


def pair(qapp, a, b):
    seen = []
    b.joinFinished.connect(lambda ok, text: seen.append((ok, text)))
    a.pairingRequest.connect(lambda name, addr: a.answerPairing(True))
    assert a.openPairing()
    p = PairingPayload.parse(a.pairingUri)
    b.joinPairing(PairingPayload("127.0.0.1", p.port, p.fingerprint, p.token, p.name).to_uri())
    wait(qapp, lambda: seen, "join result")
    assert seen[0][0], seen
    wait(qapp, lambda: b.selectedDevice >= 0 and a.selectedDevice >= 0, "both devices see the pairing")


def test_pair_send_catch_and_arrive(qapp, apps):
    a, b, tmp = apps
    pair(qapp, a, b)
    assert b.selectedDeviceName == "Laptop-A" and a.selectedDeviceName == "Laptop-B"

    photo = tmp / "sunset.png"
    Image.new("RGB", (800, 600), (230, 120, 40)).save(photo)
    b.chooseFile(str(photo))
    b.setManualAddress(f"127.0.0.1:{a._service.port}")        # the app's fallback for no-discovery networks
    # Laptop-B's user grabs and throws
    b._on_gestures([GestureEvent("grab", 1.0)])
    assert b.senderPhase == "holding"
    b._on_gestures([GestureEvent("throw", 2.0, speed=12.0, direction=(0.8, -0.6))])
    assert b.senderPhase == "launched"

    # Laptop-A's screen: the real image pops up, waiting to be caught
    wait(qapp, lambda: a.receivePhase == "offered", "offer shown on A")
    assert a.incomingName == "sunset.png" and a.incomingSender == "Laptop-B"
    assert a.hasIncomingThumb and a.incomingKind == "image" and a.mode == "receive"
    assert b.senderPhase in ("launched", "waiting")          # nothing sent until A catches

    a._on_gestures([GestureEvent("catch", 3.0)])             # A's user closes their hand
    wait(qapp, lambda: a.receivePhase == "arrived", "verified arrival on A")
    wait(qapp, lambda: b.senderPhase == "done", "completion on B")
    saved = tmp / "Laptop-A" / "inbox" / "sunset.png"
    assert saved.read_bytes() == photo.read_bytes()
    assert a.receivedName == "sunset.png" and a.history[0]["title"] == "Received"
    assert b.history[0]["title"] == "Sent" and b.sendProgress == 1.0

    a.dismissIncoming()
    assert a.receivePhase == "none" and a.mode == "send"


def test_declining_comes_back_to_the_sender_as_a_clear_failure(qapp, apps):
    a, b, tmp = apps
    pair(qapp, a, b)
    f = tmp / "doc.txt"
    f.write_text("hello")
    b.chooseFile(str(f))
    b.setManualAddress(f"127.0.0.1:{a._service.port}")
    b.sendSelected()
    wait(qapp, lambda: a.receivePhase == "offered", "offer shown")
    assert not a.hasIncomingThumb                            # a text file gets a badge, not a picture
    a.declineIncoming()
    wait(qapp, lambda: b.senderPhase == "failed", "failure on B")
    assert "no" in b.senderMessage.lower()
    assert not list((tmp / "Laptop-A" / "inbox").glob("doc*"))
    b._phase_timeout()
    assert b.senderPhase == "idle" and b.canSend             # the card comes back, ready to retry


def test_a_gesture_never_sends_without_a_catch(qapp, apps):
    a, b, tmp = apps
    pair(qapp, a, b)
    f = tmp / "x.txt"
    f.write_text("x")
    b.chooseFile(str(f))
    b.setManualAddress(f"127.0.0.1:{a._service.port}")
    b.sendSelected()
    wait(qapp, lambda: a.receivePhase == "offered", "offer")
    time.sleep(0.5)
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.02)
    assert not (tmp / "Laptop-A" / "inbox" / "x.txt").exists()
    assert a.receivePhase == "offered"                       # still waiting for the catch
