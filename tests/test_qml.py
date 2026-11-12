"""The real main.qml loads, shows every state, and logs no QML errors or warnings."""
import io
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from aircatch.transfer.events import TransferEvent
from aircatch.ui.controller import AppController, AppSettings, ImageProvider, ThumbProvider
from tests.fakes import FakeService, FakeWorker

QML = Path(__file__).parent.parent / "src" / "aircatch" / "ui" / "main.qml"
DEVICE = {"device_id": "aa" * 16, "name": "Laptop-B", "paired": True, "online": True,
          "address": "192.168.1.23", "status": "ONLINE"}


def pump(qapp, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


@pytest.fixture
def ui(qapp, tmp_path):
    QQuickStyle.setStyle("Basic")
    messages = []

    def collect(mode, context, message):
        """Only real problems count: warnings and errors, not Qt's routine platform notes
        (on Windows Qt logs font-database timing the first time it starts)."""
        category = (context.category or "") if context is not None else ""
        if message.startswith("QFontDatabase:"):       # headless Qt on Windows has no font folder
            return
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) \
                and not category.startswith("qt.qpa"):
            messages.append(message)
    qInstallMessageHandler(collect)
    cam, qr, thumbs = ImageProvider(), ImageProvider(), ThumbProvider()
    engine = QQmlApplicationEngine()
    ctl = AppController(cam, qr, thumbs, AppSettings(tmp_path / "cfg", tmp_path / "in", name="Me"),
                        parent=engine, service_factory=FakeService, worker_factory=FakeWorker)
    engine.addImageProvider("camera", cam)
    engine.addImageProvider("qr", qr)
    engine.addImageProvider("thumb", thumbs)
    engine.rootContext().setContextProperty("backend", ctl)
    engine.load(QUrl.fromLocalFile(str(QML)))
    yield engine, ctl, messages, tmp_path
    ctl.shutdown()
    del engine
    qInstallMessageHandler(None)


def test_qml_loads_cleanly(ui, qapp):
    engine, ctl, messages, _ = ui
    assert len(engine.rootObjects()) == 1
    pump(qapp, 0.3)
    assert [m for m in messages if m.strip()] == []


def test_every_state_renders_without_qml_errors(ui, qapp):
    engine, ctl, messages, tmp = ui
    photo = tmp / "p.png"
    Image.new("RGB", (500, 300), (200, 80, 40)).save(photo)
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (40, 90, 200)).save(buf, "JPEG")

    def tev(kind, direction, **kw):
        return {"event": TransferEvent(kind, direction, "t1", "p.png", **kw)}

    ctl._on_net("status", {"text": "Visible on this network · port 5001", "ready": True, "port": 5001, "name": "Me"})
    ctl._on_net("devices", {"items": [DEVICE, {**DEVICE, "device_id": "", "name": "S", "paired": False,
                                                 "status": "NOT PAIRED"}]})
    ctl.chooseFile(str(photo))
    pump(qapp, 0.5)
    ctl._set_sender_phase("holding")
    pump(qapp, 0.4)
    ctl.sendSelected()
    pump(qapp, 0.6)
    ctl._on_net("transfer", tev("accepted", "send", total=10))
    ctl._on_net("transfer", tev("progress", "send", bytes_done=5, total=10))
    pump(qapp, 0.2)
    ctl._on_net("transfer", tev("completed", "send", bytes_done=10, total=10, peer="Laptop-B"))
    pump(qapp, 0.3)
    ctl._on_net("send_result", {"ok": True, "reason": "", "text": "", "filename": "p.png"})
    ctl._phase_timeout()
    pump(qapp, 0.4)
    saved = tmp / "in" / "p.png"
    saved.parent.mkdir()
    saved.write_bytes(b"x")
    ctl._on_net("offer", {"transfer_id": "t1", "sender": "Laptop-B", "filename": "p.png", "size": 10,
                          "thumbnail": buf.getvalue(), "verified": True})
    pump(qapp, 0.8)
    ctl.acceptIncoming()
    ctl._on_net("transfer", tev("progress", "receive", bytes_done=5, total=10))
    pump(qapp, 0.3)
    ctl._on_net("transfer", tev("verifying", "receive", bytes_done=10, total=10))
    ctl._on_net("transfer", tev("completed", "receive", bytes_done=10, total=10, peer="Laptop-B", path=str(saved)))
    pump(qapp, 0.7)
    ctl.dismissIncoming()
    pump(qapp, 0.5)
    # a failed receive and a failed send, then reduced motion
    ctl._on_net("offer", {"transfer_id": "t2", "sender": "Laptop-B", "filename": "q.zip", "size": 10,
                          "thumbnail": b"", "verified": True})
    pump(qapp, 0.6)
    ctl.acceptIncoming()
    ctl._on_net("transfer", {"event": TransferEvent("failed", "receive", "t2", "q.zip", message="timeout")})
    pump(qapp, 0.4)
    ctl.dismissIncoming()
    ctl.toggleReducedMotion()
    ctl._on_net("offer", {"transfer_id": "t3", "sender": "Laptop-B", "filename": "r.txt", "size": 1,
                          "thumbnail": b"", "verified": True})
    pump(qapp, 0.3)
    ctl.declineIncoming()
    pump(qapp, 0.3)
    ctl.openPairing()
    ctl._on_net("pairing_request", {"name": "Laptop-B", "address": "1.2.3.4", "device_id": "x"})
    pump(qapp, 0.4)
    assert [m for m in messages if m.strip()] == []


def test_explorer_overlay_renders_and_reacts(ui, qapp, tmp_path):
    engine, ctl, messages, tmp = ui
    from aircatch.ui.filebrowser import Entry, FileBrowser
    (tmp / "D" / "Sub").mkdir(parents=True)
    (tmp / "D" / "one.txt").write_text("1")
    ctl._browser = FileBrowser([Entry("D", tmp / "D", True, "Folder")])
    ctl.openBrowser()
    pump(qapp, 0.3)
    assert ctl.browsing and ctl.browserCount == 1
    ctl.browserSelect()
    pump(qapp, 0.2)
    ctl.browserMove(1)
    ctl.browserSelect()                                                   # picks one.txt, closes
    pump(qapp, 0.3)
    assert not ctl.browsing and ctl.fileName == "one.txt"
    errors = [m for m in messages if m.strip() and "QFontDatabase" not in m]
    assert errors == []
