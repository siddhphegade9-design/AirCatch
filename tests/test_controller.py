"""The app's brain: gestures and buttons in, real transfers and animation phases out."""
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from aircatch.gestures.types import GestureEvent
from aircatch.transfer.events import TransferEvent
from aircatch.ui import controller as controller_module
from aircatch.ui.controller import (AppController, AppSettings, ImageProvider, ThumbProvider)

from tests.fakes import FakeService, FakeWorker

DEVICE = {"device_id": "aa" * 16, "name": "Laptop-B", "paired": True, "online": True,
          "address": "192.168.1.23", "status": "ONLINE"}


def make_controller(qapp, tmp_path, **kwargs):
    return AppController(ImageProvider(), ImageProvider(), ThumbProvider(),
                         AppSettings(tmp_path / "cfg", tmp_path / "in", name="Me"),
                         service_factory=FakeService, worker_factory=FakeWorker, **kwargs)


@pytest.fixture
def ctl(qapp, tmp_path):
    c = make_controller(qapp, tmp_path)
    yield c
    c.shutdown()


def devices(ctl, *items):
    ctl._on_net("devices", {"items": list(items)})


def give_file(ctl, tmp_path, name="note.txt", data=b"hello"):
    path = tmp_path / name
    path.write_bytes(data)
    ctl.chooseFile(str(path))
    return path


def throw(ctl, direction=(1.0, 0.0)):
    ctl._on_gestures([GestureEvent("throw", 1.0, speed=12.0, direction=direction)])


def gesture(ctl, kind):
    ctl._on_gestures([GestureEvent(kind, 1.0)])


def tev(kind, direction, tid="t1", **kw):
    return {"event": TransferEvent(kind, direction, tid, kw.pop("filename", "note.txt"), **kw)}


def offer(ctl, tid="t1", name="photo.jpg", thumb=b""):
    ctl._on_net("offer", {"transfer_id": tid, "sender": "Laptop-B", "filename": name,
                          "size": 1000, "thumbnail": thumb, "verified": True})


# ------------------------------------------------------------------ throwing never fakes a send
def test_throw_without_a_file_sends_nothing_and_plays_no_flight(ctl):
    devices(ctl, DEVICE)
    throw(ctl)
    assert ctl._service.sent() == []
    assert ctl.senderPhase == "idle"
    assert "file" in ctl.toast.lower()


def test_throw_without_a_selected_device_sends_nothing_and_says_why_on_the_card(ctl, tmp_path):
    give_file(ctl, tmp_path)
    throw(ctl)
    assert ctl._service.sent() == []
    assert ctl.senderPhase == "failed" and "pair" in ctl.senderMessage.lower()   # red flash + message
    ctl._phase_timeout()
    assert ctl.senderPhase == "idle"


def test_a_device_shown_offline_is_still_tried(ctl, tmp_path):
    """Discovery can be wrong or slow. The attempt either works or fails visibly with a reason."""
    give_file(ctl, tmp_path)
    devices(ctl, {**DEVICE, "online": False, "status": "OFFLINE", "address": ""})
    throw(ctl)
    assert len(ctl._service.sent()) == 1 and ctl.senderPhase == "launched"
    assert ctl._service.sent()[0][4] == ""
    ctl._on_net("send_step", {"text": "Looking for Laptop-B on the network..."})
    assert ctl.sendStep == "Looking for Laptop-B on the network..."
    ctl._on_net("send_result", {"ok": False, "reason": "not_found", "text": "Could not find that device.",
                                "filename": "note.txt"})
    assert ctl.senderPhase == "failed" and "find" in ctl.senderMessage.lower()
    ctl._phase_timeout()
    ctl.setManualAddress("192.168.1.23:5001")
    throw(ctl)
    assert len(ctl._service.sent()) == 2 and ctl._service.sent()[1][4] == "192.168.1.23:5001"


def test_live_send_steps_tell_the_sender_what_is_happening(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    throw(ctl)
    assert ctl.sendStep == "Looking for Laptop-B..."
    ctl._on_net("send_step", {"text": "Connecting to Laptop-B at 192.168.1.23..."})
    assert "Connecting" in ctl.sendStep
    ctl._on_net("transfer", tev("offer", "send", total=10))              # connected, offer delivered
    assert ctl.sendStep == "Waiting for Laptop-B to catch it..."
    ctl._on_net("transfer", tev("accepted", "send", total=10))
    ctl._on_net("send_step", {"text": "stale"})
    assert ctl.sendStep == "Waiting for Laptop-B to catch it..."


def test_throw_in_receive_mode_does_nothing(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    ctl.setReceiveMode()
    throw(ctl)
    assert ctl._service.sent() == []


def test_a_throw_starts_one_real_send_with_preview_and_direction(ctl, tmp_path):
    photo = tmp_path / "pic.png"
    Image.new("RGB", (600, 400), (20, 120, 220)).save(photo)
    ctl.chooseFile(str(photo))
    assert ctl.hasThumb and ctl.fileKind == "image"
    devices(ctl, DEVICE)
    throw(ctl, (3.0, 0.0))
    (call,) = ctl._service.sent()
    _, device_id, path, thumb, manual = call
    assert device_id == DEVICE["device_id"] and path == str(photo) and manual == ""
    assert thumb.startswith(b"\xff\xd8\xff") and 0 < len(thumb) <= 20 * 1024
    assert ctl.senderPhase == "launched"
    assert (ctl.throwDx, ctl.throwDy) == (1.0, 0.0)           # normalised
    throw(ctl)                                                 # a second throw while busy
    assert len(ctl._service.sent()) == 1


def test_a_tiny_or_missing_direction_flies_up(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    throw(ctl, (0.0, 0.0))
    assert (ctl.throwDx, ctl.throwDy) == (0.0, -1.0)


def test_send_button_is_the_same_as_a_throw(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    assert ctl.canSend
    ctl.sendSelected()
    assert len(ctl._service.sent()) == 1 and not ctl.canSend


# ------------------------------------------------------------------ phases follow REAL events
def test_sender_phases_follow_real_transfer_events(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    throw(ctl)
    ctl._on_net("transfer", tev("offer", "send", total=100))
    assert ctl.senderPhase == "launched"                       # an offer alone proves nothing
    ctl._on_net("transfer", tev("accepted", "send", total=100))
    assert ctl.senderPhase == "sending"
    ctl._on_net("transfer", tev("progress", "send", bytes_done=40, total=100))
    assert ctl.sendProgress == pytest.approx(0.4)
    assert ctl.transferProgress == pytest.approx(0.4)
    ctl._on_net("transfer", tev("completed", "send", bytes_done=100, total=100, peer="Laptop-B"))
    assert ctl.senderPhase == "done"
    assert ctl.history[0]["title"] == "Sent"
    ctl._phase_timeout()
    # delivered: the card and thumbnail go away so a fresh file can be chosen
    assert ctl.senderPhase == "idle" and not ctl.hasFile and not ctl.hasThumb and ctl.fileName == ""
    assert not ctl.canSend


def test_failed_send_returns_the_card_and_explains_why(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    throw(ctl)
    ctl._on_net("send_result", {"ok": False, "reason": "declined", "text": "The other device said no.",
                                "filename": "note.txt"})
    assert ctl.senderPhase == "failed" and "said no" in ctl.senderMessage
    assert ctl.history[0]["title"] == "Send failed"
    ctl._phase_timeout()
    assert ctl.senderPhase == "idle" and ctl.hasFile and ctl.canSend


def test_a_late_result_after_success_does_not_flip_to_failed(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    throw(ctl)
    ctl._on_net("transfer", tev("completed", "send", total=1, bytes_done=1, peer="B"))
    ctl._on_net("send_result", {"ok": False, "reason": "connection_lost", "text": "x", "filename": "n"})
    assert ctl.senderPhase == "done"


def test_cancel_only_while_busy(ctl, tmp_path):
    ctl.cancelSend()
    assert ctl._service.calls == []
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    throw(ctl)
    ctl.cancelSend()
    assert ("cancel_send",) in ctl._service.calls


def test_grab_and_drop_cycle(ctl, tmp_path):
    gesture(ctl, "grab")
    assert ctl.senderPhase == "idle" and "file" in ctl.toast.lower()
    give_file(ctl, tmp_path)
    gesture(ctl, "grab")
    assert ctl.senderPhase == "holding"
    gesture(ctl, "drop")
    assert ctl.senderPhase == "idle"
    gesture(ctl, "grab")
    gesture(ctl, "cancelled")
    assert ctl.senderPhase == "idle"


def test_a_throw_that_cannot_start_releases_the_held_card(ctl, tmp_path):
    give_file(ctl, tmp_path)                                   # no device
    gesture(ctl, "grab")
    throw(ctl)
    assert ctl.senderPhase == "failed"                         # the held card returns, flashing red
    ctl._phase_timeout()
    assert ctl.senderPhase == "idle"


# ------------------------------------------------------------------ receiving
def test_incoming_offer_catch_and_verified_arrival(ctl, tmp_path):
    saved = tmp_path / "in" / "photo.jpg"
    saved.parent.mkdir()
    saved.write_bytes(b"data")
    assert ctl.mode == "send"
    offer(ctl)
    assert ctl.receivePhase == "offered" and ctl.incomingActive
    assert ctl.mode == "receive"                               # catching needs receive mode
    assert (ctl.incomingName, ctl.incomingSender, ctl.incomingKind) == ("photo.jpg", "Laptop-B", "image")

    gesture(ctl, "catch")                                      # the catch gesture accepts
    assert ctl._service.answers() == [("answer_offer", "t1", True)]
    assert ctl.receivePhase == "receiving"

    ctl._on_net("transfer", tev("progress", "receive", filename="photo.jpg", bytes_done=1000, total=1000))
    assert ctl.receivePhase == "receiving"                     # all bytes in is not "done"
    ctl._on_net("transfer", tev("verifying", "receive", filename="photo.jpg", bytes_done=1000, total=1000))
    assert ctl.receivePhase == "verifying"
    ctl._on_net("transfer", tev("completed", "receive", filename="photo.jpg", bytes_done=1000,
                                total=1000, peer="Laptop-B", path=str(saved)))
    assert ctl.receivePhase == "arrived" and ctl.receivedName == "photo.jpg"
    assert ctl.history[0]["title"] == "Received"

    ctl.dismissIncoming()
    assert ctl.receivePhase == "none" and not ctl.incomingActive
    assert ctl.mode == "send"                                  # restored


def test_the_catch_button_works_without_a_camera(ctl):
    offer(ctl)
    ctl.acceptIncoming()
    assert ctl._service.answers() == [("answer_offer", "t1", True)]


def test_decline_button(ctl):
    offer(ctl)
    ctl.declineIncoming()
    assert ctl._service.answers() == [("answer_offer", "t1", False)]
    assert ctl.receivePhase == "none" and ctl.mode == "send"


def test_catch_with_nothing_incoming_is_harmless(ctl):
    gesture(ctl, "catch")
    assert ctl._service.answers() == [] and ctl.receivePhase == "none"
    assert "nothing to catch" in ctl.toast.lower()


def test_second_offer_is_declined_while_busy(ctl):
    offer(ctl, "t1")
    offer(ctl, "t2", name="other.txt")
    assert ctl._service.answers() == [("answer_offer", "t2", False)]
    assert ctl.incomingName == "photo.jpg" and ctl.receivePhase == "offered"


def test_expired_offer_clears_the_card(ctl):
    offer(ctl)
    ctl._on_net("offer_closed", {"transfer_id": "t1", "accepted": False})
    assert ctl.receivePhase == "none" and ctl.mode == "send"


def test_events_for_other_transfers_are_ignored(ctl):
    offer(ctl, "t1")
    ctl.acceptIncoming()
    ctl._on_net("transfer", tev("completed", "receive", tid="other", bytes_done=1, total=1, path="x"))
    assert ctl.receivePhase == "receiving"


def test_failed_receive_is_shown_and_can_be_dismissed(ctl):
    offer(ctl)
    ctl.acceptIncoming()
    ctl._on_net("transfer", tev("failed", "receive", filename="photo.jpg", message="hash_mismatch"))
    assert ctl.receivePhase == "failed" and "damaged" in ctl.receiveMessage
    ctl.dismissIncoming()
    assert ctl.receivePhase == "none"


def test_a_garbage_preview_from_the_network_is_ignored_not_fatal(ctl):
    offer(ctl, thumb=b"\xff\xd8\xff" + b"not really a jpeg")
    assert ctl.receivePhase == "offered" and not ctl.hasIncomingThumb


def test_programs_are_never_launched(ctl, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(controller_module.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toLocalFile()) or True)
    exe, txt = tmp_path / "setup.exe", tmp_path / "notes.txt"
    exe.write_bytes(b"MZ")
    txt.write_text("hi")
    ctl._received = str(exe)
    ctl.openReceived()
    assert [Path(o) for o in opened] == [tmp_path]             # only the folder (Qt uses / on Windows)
    ctl._received = str(txt)
    ctl.openReceived()
    assert Path(opened[-1]) == txt
    ctl._received = str(tmp_path / "gone.txt")
    ctl.openReceived()
    assert len(opened) == 2 and "could not be found" in ctl.toast


# ------------------------------------------------------------------ choosing files and devices
def test_choose_file_validation_and_kinds(ctl, tmp_path):
    ctl.chooseFile(str(tmp_path / "missing.bin"))
    assert not ctl.hasFile and ctl.toast
    ctl.chooseFile(str(tmp_path))                              # a folder
    assert not ctl.hasFile
    give_file(ctl, tmp_path, "report.pdf", b"%PDF")
    assert ctl.hasFile and ctl.fileKind == "pdf" and ctl.fileBadge == "PDF" and not ctl.hasThumb
    assert ctl.fileSize == "4 B"
    ctl.clearFile()
    assert not ctl.hasFile


def test_file_url_from_the_file_dialog_is_accepted(ctl, tmp_path):
    path = tmp_path / "a b.txt"
    path.write_text("x")
    ctl.chooseFile(path.as_uri())
    assert ctl.hasFile and ctl.fileName == "a b.txt"


def test_cannot_swap_the_file_during_a_transfer(ctl, tmp_path):
    give_file(ctl, tmp_path, "one.txt")
    devices(ctl, DEVICE)
    throw(ctl)
    give_file(ctl, tmp_path, "two.txt")
    assert ctl.fileName == "one.txt"


def test_device_list_selection_and_unpair(ctl):
    unpaired = {"device_id": "", "name": "Stranger", "paired": False, "online": True,
                "address": "", "status": "NOT PAIRED"}
    devices(ctl, unpaired, DEVICE)
    assert ctl.selectedDevice == 1 and ctl.selectedDeviceName == "Laptop-B"   # first PAIRED one
    ctl.selectDevice(0)
    assert ctl.selectedDevice == 1                              # strangers cannot be selected
    ctl.unpairDevice(0)
    assert not [c for c in ctl._service.calls if c[0] == "unpair"]
    ctl.unpairDevice(1)
    assert ("unpair", DEVICE["device_id"]) in ctl._service.calls
    devices(ctl)                                                # it disappeared
    assert ctl.selectedDevice == -1 and not ctl.canSend


# ------------------------------------------------------------------ pairing
def test_pairing_slots(ctl, qapp):
    seen = []
    ctl.joinFinished.connect(lambda ok, text: seen.append((ok, text)))
    assert ctl.openPairing() is True
    assert ctl.pairingUri.startswith("aircatch://pair") and ctl.qrVersion == 1
    ctl._on_net("paired", {"name": "Laptop-B"})
    assert ctl.pairingUri == "" and "Laptop-B" in ctl.toast
    ctl.joinPairing("   ")
    assert seen == [(False, "Paste the pairing code first.")]
    ctl.joinPairing(" aircatch://pair?x ")
    assert ("join", "aircatch://pair?x") in ctl._service.calls
    ctl._on_net("join_result", {"ok": True, "text": "Paired with Laptop-B."})
    assert seen[-1] == (True, "Paired with Laptop-B.")
    ctl.answerPairing(True)
    assert ("answer_pairing", True) in ctl._service.calls


def test_pairing_request_is_forwarded_to_the_screen(ctl):
    seen = []
    ctl.pairingRequest.connect(lambda n, a: seen.append((n, a)))
    ctl._on_net("pairing_request", {"name": "Laptop-B", "address": "1.2.3.4", "device_id": "x"})
    assert seen == [("Laptop-B", "1.2.3.4")]


def test_pairing_unavailable_is_explained(qapp, tmp_path):
    c = make_controller(qapp, tmp_path)
    c._service.pairing_uri = ""
    assert c.openPairing() is False and "starting" in c.toast
    c.shutdown()


# ------------------------------------------------------------------ camera
def status(state="idle", hint="Open your palm", progress=0.0, present=True, center=(0.8, 0.4)):
    hand = SimpleNamespace(aspect=2.0)
    return SimpleNamespace(state=state, hint=hint, progress=progress, hand_present=present,
                           palm_center=center if present else None, hand=hand if present else None)


def test_camera_lifecycle_and_hand_position(ctl):
    ctl.toggleCamera()
    worker = ctl._worker
    assert worker.started and ctl.cameraStarting and not ctl.cameraActive
    worker.live.emit()
    assert ctl.cameraActive and not ctl.cameraStarting
    worker.statusReady.emit(status("ready", "Make a fist to grab the file", 0.5))
    assert ctl.gestureState == "READY" and ctl.gestureProgress == 0.5 and ctl.handPresent
    assert ctl.handX == pytest.approx(0.4) and ctl.handY == pytest.approx(0.4)   # x / aspect
    worker.statusReady.emit(status("no_hand", "Show your hand", present=False))
    assert not ctl.handPresent and ctl.gestureState == "NO HAND"
    ctl.setReceiveMode()
    assert worker.mode == "receive"
    ctl.toggleCamera()
    assert worker.stopped and ctl._worker is None and not ctl.cameraActive


def test_camera_failure_is_reported_and_recoverable(ctl):
    ctl.toggleCamera()
    ctl._worker.failed.emit("Could not open the camera.")
    assert not ctl.cameraActive and not ctl.cameraStarting and ctl._worker is None
    assert ctl.status == "Could not open the camera." and ctl.toast == "Could not open the camera."
    ctl.toggleCamera()                                          # can try again
    assert ctl._worker is not None


def test_stopping_the_camera_releases_a_held_card(ctl, tmp_path):
    give_file(ctl, tmp_path)
    gesture(ctl, "grab")
    assert ctl.senderPhase == "holding"
    ctl.toggleCamera()
    ctl.toggleCamera()
    assert ctl.senderPhase == "idle"


# ------------------------------------------------------------------ misc
def test_events_from_another_thread_reach_the_window_thread(ctl, qapp):
    thread = threading.Thread(target=lambda: ctl._service.emit(
        "status", {"text": "Visible on this network · port 5001", "ready": True, "port": 5001,
                   "name": "Me"}))
    thread.start()
    thread.join()
    deadline = time.monotonic() + 3
    while not ctl.networkReady and time.monotonic() < deadline:
        qapp.processEvents()
    assert ctl.networkReady and ctl.deviceName == "Me" and "5001" in ctl.status


def test_reduced_motion_is_remembered(qapp, tmp_path):
    first = make_controller(qapp, tmp_path)
    assert not first.reducedMotion
    first.toggleReducedMotion()
    first.shutdown()
    second = make_controller(qapp, tmp_path)
    assert second.reducedMotion
    second.shutdown()


def test_shutdown_is_repeatable_and_stops_everything(qapp, tmp_path):
    c = make_controller(qapp, tmp_path)
    c.toggleCamera()
    worker = c._worker
    c.shutdown()
    c.shutdown()
    assert c._service.stopped == 1 and worker.stopped
    c._on_net("offer", {"transfer_id": "x", "sender": "s", "filename": "f", "size": 1,
                        "thumbnail": b"", "verified": True})        # ignored after shutdown
    assert c.receivePhase == "none"


# ------------------------------------------------------------------ feedback, easy throw, connection test
def test_every_gesture_is_reported_on_screen(ctl, tmp_path):
    ctl._on_gestures([GestureEvent("ready", 1.0, detail="catch")])
    assert "open your hand" in ctl.lastGesture.lower()
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    gesture(ctl, "grab")
    assert ctl.lastGesture == "GRAB"
    throw(ctl, (1.0, 0.0))
    assert ctl.lastGesture.startswith("THROW") and "12.0" in ctl.lastGesture


def test_a_too_gentle_release_explains_itself_with_the_numbers(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    ctl._throw_speed = 7.5
    gesture(ctl, "grab")
    ctl._on_gestures([GestureEvent("drop", 2.0, speed=3.2, detail="released")])
    assert ctl.senderPhase == "idle" and ctl._service.sent() == []
    assert "3.2" in ctl.lastGesture and "7.5" in ctl.lastGesture and "Easy throw" in ctl.lastGesture
    assert "too gently" in ctl.toast
    gesture(ctl, "grab")
    ctl._on_gestures([GestureEvent("drop", 3.0, detail="timeout")])
    assert "too long" in ctl.lastGesture


def test_easy_throw_is_on_by_default_reaches_the_camera_and_is_remembered(qapp, tmp_path):
    first = make_controller(qapp, tmp_path)
    assert first.easyThrow
    first.toggleCamera()
    assert first._worker.require_motion is False               # any release after a grab sends
    first.toggleEasyThrow()
    assert not first.easyThrow and first._worker.require_motion is True
    first.shutdown()
    second = make_controller(qapp, tmp_path)
    assert not second.easyThrow                                 # remembered
    second.toggleCamera()
    assert second._worker.require_motion is True
    second.shutdown()


def test_test_connection_asks_the_service_and_shows_the_answer(ctl):
    ctl.testConnection()
    assert "paired device" in ctl.connectionNote.lower() and not ctl.testingConnection
    devices(ctl, DEVICE)
    ctl.setManualAddress("10.0.0.5:5001")
    ctl.testConnection()
    assert ctl.testingConnection
    assert ("test_connection", DEVICE["device_id"], "10.0.0.5:5001") in ctl._service.calls
    ctl._on_net("connection_result", {"ok": True, "text": "Connected to Laptop-B at 10.0.0.5:5001."})
    assert ctl.connectionOk and not ctl.testingConnection and "Connected" in ctl.connectionNote
    ctl._on_net("connection_result", {"ok": False, "text": "Could not connect."})
    assert not ctl.connectionOk


# ------------------------------------------------------------------ the gesture file explorer
def browse_tree(ctl, tmp_path):
    from aircatch.ui.filebrowser import Entry, FileBrowser
    (tmp_path / "Desk" / "Sub").mkdir(parents=True)
    (tmp_path / "Desk" / "a.txt").write_text("a")
    (tmp_path / "Desk" / "b.txt").write_text("b")
    ctl._browser = FileBrowser([Entry("Desk", tmp_path / "Desk", True, "Folder")])


def ev(kind, **kw):
    return GestureEvent(kind, 1.0, **kw)


def test_peace_sign_opens_the_explorer_and_swipes_move_thumbs_up_picks_the_file(ctl, tmp_path):
    browse_tree(ctl, tmp_path)
    ctl.toggleCamera()                                                # the fake camera worker
    ctl._on_gestures([ev("browse_open")])
    assert ctl.browsing and ctl.browserTitle == "This computer" and ctl.browserCount == 1
    ctl._on_gestures([ev("select")])                                   # Desk
    assert ctl.browserTitle.endswith("Desk") and ctl.browserCount == 3        # Sub, a.txt, b.txt
    ctl._on_gestures([ev("swipe", detail="down")])
    ctl._on_gestures([ev("swipe", detail="down")])
    assert ctl.browserIndex == 2
    ctl._on_gestures([ev("swipe", detail="up")])
    assert ctl.browserIndex == 1
    ctl._on_gestures([ev("select")])                                   # a.txt
    assert not ctl.browsing and ctl.hasFile and ctl.fileName == "a.txt"
    assert ctl._worker.mode == "send"                                 # back to the normal throw gestures


def test_swipe_right_enters_a_folder_and_left_goes_back(ctl, tmp_path):
    browse_tree(ctl, tmp_path)
    ctl.toggleCamera()
    ctl.openBrowser()
    assert ctl._worker.mode == "browse"
    ctl._on_gestures([ev("swipe", detail="right")])
    assert ctl.browserTitle.endswith("Desk")
    ctl._on_gestures([ev("swipe", detail="right")])                   # Sub (a folder)
    assert ctl.browserTitle.endswith("Sub")
    ctl._on_gestures([ev("swipe", detail="left")])
    assert ctl.browserTitle.endswith("Desk")
    ctl._on_gestures([ev("swipe", detail="left")])
    assert ctl.browserTitle == "This computer"
    ctl._on_gestures([ev("swipe", detail="left")])                    # nowhere further
    assert ctl.browsing and "top" in ctl.toast.lower()


def test_peace_sign_closes_it_and_gestures_outside_the_explorer_are_ignored(ctl, tmp_path):
    browse_tree(ctl, tmp_path)
    ctl._on_gestures([ev("swipe", detail="down"), ev("select")])      # explorer closed: nothing happens
    assert not ctl.browsing and not ctl.hasFile
    ctl._on_gestures([ev("browse_open")])
    ctl._on_gestures([ev("browse_close")])
    assert not ctl.browsing


def test_explorer_refuses_to_open_while_sending_or_receiving(ctl, tmp_path):
    give_file(ctl, tmp_path)
    devices(ctl, DEVICE)
    throw(ctl)                                                        # now busy
    ctl.openBrowser()
    assert not ctl.browsing and "current send" in ctl.toast.lower()


def test_an_incoming_file_closes_the_explorer(ctl, tmp_path):
    browse_tree(ctl, tmp_path)
    ctl.openBrowser()
    ctl._on_net("offer", {"transfer_id": "t9", "sender": "Laptop-B", "filename": "x.txt", "size": 3,
                          "thumbnail": b"", "verified": True})
    assert not ctl.browsing and ctl.receivePhase == "offered"


def test_clicking_a_row_works_like_a_thumbs_up(ctl, tmp_path):
    browse_tree(ctl, tmp_path)
    ctl.openBrowser()
    ctl.browserActivate(0)                                            # Desk
    ctl.browserActivate(2)                                            # b.txt
    assert ctl.fileName == "b.txt" and not ctl.browsing
