"""Milestone 10: a 'monkey' presses every button and fires every event in random order.

Thousands of random user actions, gestures, network events and timer ticks are thrown at the app's
logic (the controller) with fake network and camera. Nothing may raise, and the state must always
make sense. This finds the "nobody would ever do that" sequences before a live demo does.
"""
import random

import pytest
from PIL import Image

from aircatch.gestures.types import EngineStatus, GestureEvent, Pose
from aircatch.transfer.events import TransferEvent
from aircatch.ui import controller as controller_module
from tests.test_controller import make_controller

SENDER_PHASES = {"idle", "holding", "launched", "waiting", "sending", "done", "failed"}
RECEIVE_PHASES = {"none", "offered", "receiving", "verifying", "arrived", "failed"}
READY = {"ready", "wait", "moving", "nohand"}


def devices(r):
    items = []
    for i in range(r.randrange(0, 4)):
        online = r.random() < 0.7
        items.append({"device_id": f"{i:064x}", "name": r.choice(["Laptop-B", "PC " * 30, "", "ß∂ƒ 🙂"]),
                      "paired": r.random() < 0.8, "online": online,
                      "address": "192.168.1.9:5001" if online else "", "status": "ONLINE" if online else "OFFLINE"})
    return items


def actions(ctl, r, files, tmp_path):
    def gesture(kind, detail=""):
        return lambda: ctl._on_gestures([GestureEvent(kind, 1.0, speed=r.uniform(0, 9), direction=(1.0, 0.0),
                                                      detail=detail)])

    def net(kind, payload):
        return lambda: ctl._on_net(kind, payload() if callable(payload) else payload)

    def transfer(direction, kind):
        def fire():
            tid = ctl._offer_id if direction == "receive" and r.random() < 0.8 else r.choice(["t1", "t2", ""])
            ctl._on_net("transfer", {"event": TransferEvent(
                kind, direction, tid or "", "f.bin", peer="Laptop-B", bytes_done=r.randrange(0, 2000),
                total=r.choice([0, 1000, 2000]), message=r.choice(["", "busy", "declined", "hash_mismatch"]),
                path=str(r.choice(files)) if r.random() < 0.7 else str(tmp_path / "gone.bin"))})
        return fire

    def offer():
        return {"transfer_id": r.choice(["t1", "t2", "t3"]), "filename": r.choice(["a.png", "run.exe", "x" * 200]),
                "size": r.choice([0, 5, 10 ** 12]), "sender": r.choice(["Laptop-B", ""]),
                "thumbnail": r.choice([b"", b"\xff\xd8\xff garbage", b"\x89PNG\r\n\x1a\n" + b"0" * 50]),
                "verified": True, "device_id": "0" * 64}

    def status():
        ctl._on_gesture_status(EngineStatus(
            mode=r.choice(["send", "receive", "browse", "idle"]), state=r.choice(["idle", "no_hand", "holding"]),
            pose=Pose.OPEN, fingers=(True,) * 4, hand_present=r.random() < 0.7, progress=r.random(),
            cooldown_left=0.0, speed=r.uniform(0, 9), palm_center=None, hint=r.choice(["", "Swipe"]),
            swipe_note=r.choice(["", "left: ok"]), ready=r.choice(sorted(READY))))

    acts = [
        ctl.toggleReducedMotion, ctl.toggleEasyThrow, ctl.dismissWelcome, ctl.showWelcomeAgain, ctl.toggleCamera, ctl.setSendMode, ctl.setReceiveMode,
        ctl.openBrowser, ctl.closeBrowser, ctl.browserOpenFolder, ctl.browserBack, ctl.browserSelect,
        lambda: ctl.browserMove(r.choice([-3, -1, 1, 2, 500])), lambda: ctl.browserActivate(r.randrange(-2, 60)),
        lambda: ctl.chooseFile(str(r.choice(files))), lambda: ctl.chooseFile(r.choice(["", "nope", str(tmp_path)])),
        ctl.clearFile, lambda: ctl.selectDevice(r.randrange(-2, 6)),
        lambda: ctl.setManualAddress(r.choice(["", "192.168.1.5:5001", "junk", "::::"])),
        lambda: ctl.unpairDevice(r.randrange(-1, 5)), ctl.sendSelected, ctl.testConnection, ctl.cancelSend,
        ctl.acceptIncoming, ctl.declineIncoming, ctl.dismissIncoming, ctl.openReceived, ctl.showReceivedFolder,
        ctl.openPairing, ctl.closePairing, lambda: ctl.answerPairing(r.random() < 0.5),
        lambda: ctl.joinPairing(r.choice(["", "junk", "aircatch://pair?v=1&h=1.2.3.4&p=5&f=ab&t=cd"])),
        ctl._phase_timeout, status,
        net("status", lambda: {"text": "Visible", "ready": r.random() < 0.8, "port": 5001, "name": "Me"}),
        net("devices", lambda: {"items": devices(r)}), net("offer", offer),
        net("offer_closed", lambda: {"transfer_id": r.choice(["t1", "t2", ctl._offer_id or "t3"])}),
        net("send_step", lambda: {"text": "Looking for the device"}),
        net("connection_result", lambda: {"ok": r.random() < 0.5, "text": "x"}),
        net("send_result", lambda: {"ok": r.random() < 0.5, "reason": "busy", "text": r.choice(["", "Busy"])}),
        net("pairing_request", lambda: {"name": "Laptop-B", "address": "1.2.3.4:5"}), net("pairing_closed", {}),
        net("paired", lambda: {"name": "Laptop-B"}),
        net("join_result", lambda: {"ok": r.random() < 0.5, "text": "x", "name": "Laptop-B"}),
        net("nonsense_kind", {}),
    ]
    for kind in ("ready", "grab", "drop", "throw", "catch", "cancelled", "browse_open", "browse_close", "select"):
        acts.append(gesture(kind))
    for d in ("up", "down", "left", "right", "sideways"):
        acts.append(gesture("swipe", d))
    for direction in ("send", "receive"):
        for kind in ("offer", "accepted", "progress", "verifying", "completed", "rejected", "failed"):
            acts.append(transfer(direction, kind))
    return acts


@pytest.mark.parametrize("seed", range(6))
def test_no_sequence_of_actions_breaks_the_app(qapp, tmp_path, monkeypatch, seed):
    monkeypatch.setattr(controller_module.QDesktopServices, "openUrl", lambda *_: True)   # do not open windows
    r = random.Random(seed)
    folder = tmp_path / "stuff"
    (folder / "sub").mkdir(parents=True)
    files = [folder / "notes.txt", folder / "sub" / "deep.bin", folder / "pic.png", folder / "run.exe"]
    for f in files:
        f.write_bytes(b"x" * 10)
    Image.new("RGB", (40, 30), (9, 99, 199)).save(files[2])
    ctl = make_controller(qapp, tmp_path)
    try:
        acts = actions(ctl, r, files, tmp_path)
        for step in range(4000):
            act = r.choice(acts)
            try:
                act()
            except Exception as exc:                                    # show which step did it
                raise AssertionError(f"seed {seed}, step {step}: {exc!r}") from exc
            if step % 7 == 0:
                qapp.processEvents()
            assert ctl.senderPhase in SENDER_PHASES and ctl.receivePhase in RECEIVE_PHASES
            assert ctl.mode in ("send", "receive", "browse", "idle") and ctl.gestureReady in READY
            assert ctl.hasFile == bool(ctl.fileName)                    # a card without a file, or the reverse
            assert 0.0 <= ctl.transferProgress <= 1.0
            if ctl.browsing:
                assert 0 <= ctl.browserIndex < max(1, ctl.browserCount)
            if ctl.senderPhase in ("launched", "waiting", "sending"):
                assert ctl.hasFile                                      # never "sending" nothing
    finally:
        ctl.shutdown()
    ctl.setSendMode()                                                   # after shutdown nothing may blow up
    ctl._on_net("devices", {"items": []})
