"""The bridge between the QML screens and everything else (camera, gestures, network).

Design rules (from the project brief):
  * the camera, MediaPipe and the gesture engine run on a worker thread (VisionWorker);
  * all networking runs on the service thread (NetworkService); neither blocks the window;
  * a gesture never sends anything by itself: the controller decides, and the transfer engine
    does the work;
  * animations are driven by `senderPhase` / `receivePhase`, which only change when something
    REAL happens (a send really started, bytes really moved, the hash really verified).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, Property, Qt, QTimer, QUrl,
                            Signal, Slot)
from PySide6.QtGui import QColor, QDesktopServices, QImage
from PySide6.QtQuick import QQuickImageProvider

from aircatch.gestures.config import GestureConfig
from aircatch.service import NetworkService, reason_text
from aircatch.ui.thumbnails import (decode_incoming_preview, file_kind, human_size, jpeg_for_offer,
                                    load_preview)
from aircatch.ui.vision_worker import VisionWorker

# Opening one of these would run code. AirCatch never launches them; it only shows the folder.
RISKY_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".vbe", ".js",
                    ".jse", ".jar", ".lnk", ".reg", ".hta", ".dll", ".cpl", ".wsf", ".msc", ".apk",
                    ".sh", ".pif", ".gadget"}
BUSY_PHASES = ("launched", "waiting", "sending")


def default_config_dir() -> Path:
    try:
        from platformdirs import user_data_dir
        return Path(user_data_dir("AirCatch", appauthor=False))
    except ImportError:
        return Path.home() / ".aircatch"


@dataclass
class AppSettings:
    config_dir: Path
    download_dir: Path
    name: str | None = None
    port: int = 0
    advertise: bool = True
    interface: str | None = None
    camera_index: int = 0

    @staticmethod
    def defaults() -> "AppSettings":
        return AppSettings(default_config_dir(), Path.home() / "Downloads" / "AirCatch")


class ImageProvider(QQuickImageProvider):
    """Hands one 'current' picture (camera frame, QR code) to QML."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._lock = threading.Lock()
        self._image = QImage()

    def set_image(self, image: QImage) -> None:
        with self._lock:
            self._image = image.copy()

    def requestImage(self, image_id, size, requested_size):
        with self._lock:
            image = self._image.copy()
        if image.isNull():
            image = QImage(2, 2, QImage.Format.Format_RGB32)
            image.fill(QColor("#101827"))
        return image


class ThumbProvider(QQuickImageProvider):
    """Two named pictures: 'send' (the selected file) and 'recv' (the incoming file)."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._lock = threading.Lock()
        self._images: dict[str, QImage] = {}

    def set_image(self, key: str, image: QImage) -> None:
        with self._lock:
            self._images[key] = image.copy()

    def requestImage(self, image_id, size, requested_size):
        key = str(image_id).split("/")[0].split("?")[0]
        with self._lock:
            image = self._images.get(key, QImage()).copy()
        if image.isNull():
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
        return image


class DeviceModel(QAbstractListModel):
    IdRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    PairedRole = Qt.ItemDataRole.UserRole + 3
    OnlineRole = Qt.ItemDataRole.UserRole + 4
    AddressRole = Qt.ItemDataRole.UserRole + 5
    StatusRole = Qt.ItemDataRole.UserRole + 6
    _KEYS = {IdRole: "device_id", NameRole: "name", PairedRole: "paired", OnlineRole: "online",
             AddressRole: "address", StatusRole: "status"}

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict] = []

    def roleNames(self):
        return {self.IdRole: b"deviceId", self.NameRole: b"name", self.PairedRole: b"paired",
                self.OnlineRole: b"online", self.AddressRole: b"address", self.StatusRole: b"status"}

    def rowCount(self, parent=QModelIndex()):
        return len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        key = self._KEYS.get(role)
        return self.items[index.row()].get(key) if key else None

    def replace(self, items: list[dict]) -> None:
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()


_DEFAULTS: dict = {
    "status": "Starting AirCatch…", "deviceName": "", "networkReady": False, "downloadDir": "",
    "cameraActive": False, "cameraStarting": False, "frameCounter": 0, "mode": "send",
    "gestureState": "NO HAND", "gestureHint": "Start the camera to use gestures",
    "gestureProgress": 0.0, "handPresent": False, "handX": 0.5, "handY": 0.5,
    "hasFile": False, "fileName": "", "fileSize": "", "fileKind": "other", "fileBadge": "",
    "thumbVersion": 0, "hasThumb": False,
    "selectedDevice": -1, "selectedDeviceName": "", "canSend": False, "manualAddress": "",
    "senderPhase": "idle", "senderMessage": "", "throwDx": 0.0, "throwDy": -1.0,
    "sendProgress": 0.0,
    "incomingActive": False, "incomingName": "", "incomingSender": "", "incomingSize": "",
    "incomingKind": "other", "incomingBadge": "", "incomingThumbVersion": 0,
    "hasIncomingThumb": False, "receivePhase": "none", "receiveMessage": "",
    "receiveProgress": 0.0, "receivedName": "",
    "transferLabel": "No active transfer", "transferProgress": 0.0,
    "history": [], "reducedMotion": False, "pairingUri": "", "qrVersion": 0,
}


class AppController(QObject):
    # one change-signal per property (QML re-reads the property when it fires)
    statusChanged = Signal(); deviceNameChanged = Signal(); networkReadyChanged = Signal()
    downloadDirChanged = Signal(); cameraActiveChanged = Signal(); cameraStartingChanged = Signal()
    frameCounterChanged = Signal(); modeChanged = Signal(); gestureStateChanged = Signal()
    gestureHintChanged = Signal(); gestureProgressChanged = Signal(); handPresentChanged = Signal()
    handXChanged = Signal(); handYChanged = Signal(); hasFileChanged = Signal()
    fileNameChanged = Signal(); fileSizeChanged = Signal(); fileKindChanged = Signal()
    fileBadgeChanged = Signal(); thumbVersionChanged = Signal(); hasThumbChanged = Signal()
    selectedDeviceChanged = Signal(); selectedDeviceNameChanged = Signal()
    canSendChanged = Signal(); manualAddressChanged = Signal(); senderPhaseChanged = Signal()
    senderMessageChanged = Signal(); throwDxChanged = Signal(); throwDyChanged = Signal()
    sendProgressChanged = Signal(); incomingActiveChanged = Signal(); incomingNameChanged = Signal()
    incomingSenderChanged = Signal(); incomingSizeChanged = Signal(); incomingKindChanged = Signal()
    incomingBadgeChanged = Signal(); incomingThumbVersionChanged = Signal()
    hasIncomingThumbChanged = Signal(); receivePhaseChanged = Signal()
    receiveMessageChanged = Signal(); receiveProgressChanged = Signal()
    receivedNameChanged = Signal(); transferLabelChanged = Signal()
    transferProgressChanged = Signal(); historyChanged = Signal(); reducedMotionChanged = Signal()
    pairingUriChanged = Signal(); qrVersionChanged = Signal()
    # events (not state)
    toastChanged = Signal()
    pairingRequest = Signal(str, str)
    pairingClosed = Signal()
    joinFinished = Signal(bool, str)

    status = Property(str, lambda s: s._v["status"], notify=statusChanged)
    deviceName = Property(str, lambda s: s._v["deviceName"], notify=deviceNameChanged)
    networkReady = Property(bool, lambda s: s._v["networkReady"], notify=networkReadyChanged)
    downloadDir = Property(str, lambda s: s._v["downloadDir"], notify=downloadDirChanged)
    cameraActive = Property(bool, lambda s: s._v["cameraActive"], notify=cameraActiveChanged)
    cameraStarting = Property(bool, lambda s: s._v["cameraStarting"], notify=cameraStartingChanged)
    frameCounter = Property(int, lambda s: s._v["frameCounter"], notify=frameCounterChanged)
    mode = Property(str, lambda s: s._v["mode"], notify=modeChanged)
    gestureState = Property(str, lambda s: s._v["gestureState"], notify=gestureStateChanged)
    gestureHint = Property(str, lambda s: s._v["gestureHint"], notify=gestureHintChanged)
    gestureProgress = Property(float, lambda s: s._v["gestureProgress"], notify=gestureProgressChanged)
    handPresent = Property(bool, lambda s: s._v["handPresent"], notify=handPresentChanged)
    handX = Property(float, lambda s: s._v["handX"], notify=handXChanged)
    handY = Property(float, lambda s: s._v["handY"], notify=handYChanged)
    hasFile = Property(bool, lambda s: s._v["hasFile"], notify=hasFileChanged)
    fileName = Property(str, lambda s: s._v["fileName"], notify=fileNameChanged)
    fileSize = Property(str, lambda s: s._v["fileSize"], notify=fileSizeChanged)
    fileKind = Property(str, lambda s: s._v["fileKind"], notify=fileKindChanged)
    fileBadge = Property(str, lambda s: s._v["fileBadge"], notify=fileBadgeChanged)
    thumbVersion = Property(int, lambda s: s._v["thumbVersion"], notify=thumbVersionChanged)
    hasThumb = Property(bool, lambda s: s._v["hasThumb"], notify=hasThumbChanged)
    selectedDevice = Property(int, lambda s: s._v["selectedDevice"], notify=selectedDeviceChanged)
    selectedDeviceName = Property(str, lambda s: s._v["selectedDeviceName"],
                                  notify=selectedDeviceNameChanged)
    canSend = Property(bool, lambda s: s._v["canSend"], notify=canSendChanged)
    manualAddress = Property(str, lambda s: s._v["manualAddress"], notify=manualAddressChanged)
    senderPhase = Property(str, lambda s: s._v["senderPhase"], notify=senderPhaseChanged)
    senderMessage = Property(str, lambda s: s._v["senderMessage"], notify=senderMessageChanged)
    throwDx = Property(float, lambda s: s._v["throwDx"], notify=throwDxChanged)
    throwDy = Property(float, lambda s: s._v["throwDy"], notify=throwDyChanged)
    sendProgress = Property(float, lambda s: s._v["sendProgress"], notify=sendProgressChanged)
    incomingActive = Property(bool, lambda s: s._v["incomingActive"], notify=incomingActiveChanged)
    incomingName = Property(str, lambda s: s._v["incomingName"], notify=incomingNameChanged)
    incomingSender = Property(str, lambda s: s._v["incomingSender"], notify=incomingSenderChanged)
    incomingSize = Property(str, lambda s: s._v["incomingSize"], notify=incomingSizeChanged)
    incomingKind = Property(str, lambda s: s._v["incomingKind"], notify=incomingKindChanged)
    incomingBadge = Property(str, lambda s: s._v["incomingBadge"], notify=incomingBadgeChanged)
    incomingThumbVersion = Property(int, lambda s: s._v["incomingThumbVersion"],
                                    notify=incomingThumbVersionChanged)
    hasIncomingThumb = Property(bool, lambda s: s._v["hasIncomingThumb"],
                                notify=hasIncomingThumbChanged)
    receivePhase = Property(str, lambda s: s._v["receivePhase"], notify=receivePhaseChanged)
    receiveMessage = Property(str, lambda s: s._v["receiveMessage"], notify=receiveMessageChanged)
    receiveProgress = Property(float, lambda s: s._v["receiveProgress"], notify=receiveProgressChanged)
    receivedName = Property(str, lambda s: s._v["receivedName"], notify=receivedNameChanged)
    transferLabel = Property(str, lambda s: s._v["transferLabel"], notify=transferLabelChanged)
    transferProgress = Property(float, lambda s: s._v["transferProgress"],
                                notify=transferProgressChanged)
    history = Property("QVariantList", lambda s: s._v["history"], notify=historyChanged)
    reducedMotion = Property(bool, lambda s: s._v["reducedMotion"], notify=reducedMotionChanged)
    pairingUri = Property(str, lambda s: s._v["pairingUri"], notify=pairingUriChanged)
    qrVersion = Property(int, lambda s: s._v["qrVersion"], notify=qrVersionChanged)
    toast = Property(str, lambda s: s._toast_text, notify=toastChanged)
    devices = Property(QObject, lambda s: s._devices, constant=True)

    def __init__(self, camera_provider: ImageProvider, qr_provider: ImageProvider,
                 thumb_provider: ThumbProvider, settings: AppSettings | None = None, parent=None, *,
                 service_factory: Callable[..., NetworkService] | None = None,
                 worker_factory: Callable[..., VisionWorker] | None = None) -> None:
        super().__init__(parent)
        self._settings = settings or AppSettings.defaults()
        self._camera_provider, self._qr_provider = camera_provider, qr_provider
        self._thumb_provider = thumb_provider
        self._v = dict(_DEFAULTS)
        self._v["history"] = []
        self._devices = DeviceModel()
        self._worker_factory = worker_factory or VisionWorker
        self._worker: VisionWorker | None = None
        self._file_path: Path | None = None
        self._thumb_jpeg = b""
        self._offer_id = ""
        self._received: str | None = None
        self._mode_before_offer: str | None = None
        self._toast_text = ""
        self._last_toast_text, self._last_toast_time = "", 0.0
        self._closed = False

        self._phase_timer = QTimer(self)
        self._phase_timer.setSingleShot(True)
        self._phase_timer.timeout.connect(self._phase_timeout)
        self._phase_next: tuple[str, str] | None = None     # (only if currently X, then go to Y)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismissIncoming)

        self._v["downloadDir"] = str(self._settings.download_dir)
        self._load_ui_settings()
        # The service thread only drops events into this thread-safe queue; the window thread
        # collects them on a timer. No Qt object is ever touched from a foreign thread.
        self._inbox: queue.SimpleQueue = queue.SimpleQueue()
        self._pump = QTimer(self)
        self._pump.setInterval(15)
        self._pump.timeout.connect(self._drain_inbox)
        self._pump.start()
        make = service_factory or NetworkService
        self._service = make(self._emit_from_service, config_dir=self._settings.config_dir,
                             download_dir=self._settings.download_dir, name=self._settings.name,
                             port=self._settings.port, advertise=self._settings.advertise,
                             interface=self._settings.interface)
        self._service.start()

    # ------------------------------------------------------------------ small helpers
    def _emit_from_service(self, kind: str, payload: dict) -> None:
        """Called on the service thread. Only queues; never touches Qt."""
        if not self._closed:
            self._inbox.put((kind, payload))

    def _drain_inbox(self) -> None:
        for _ in range(200):                      # bounded, so a burst can never freeze the window
            try:
                kind, payload = self._inbox.get_nowait()
            except queue.Empty:
                return
            self._on_net(kind, payload)

    def _set(self, name: str, value) -> None:
        if self._v.get(name) != value:
            self._v[name] = value
            getattr(self, name + "Changed").emit()

    def _toast(self, text: str) -> None:
        now = time.monotonic()
        if text == self._last_toast_text and now - self._last_toast_time < 1.5:
            return
        self._last_toast_text, self._last_toast_time = text, now
        self._toast_text = text
        self.toastChanged.emit()

    def _log(self, title: str, detail: str) -> None:
        entry = {"title": title, "detail": detail, "when": time.strftime("%H:%M")}
        self._set("history", ([entry] + self._v["history"])[:30])

    # ------------------------------------------------------------------ persisted UI settings
    def _ui_settings_path(self) -> Path:
        return self._settings.config_dir / "ui_settings.json"

    def _load_ui_settings(self) -> None:
        try:
            data = json.loads(self._ui_settings_path().read_text("utf-8"))
            self._v["reducedMotion"] = bool(data.get("reduced_motion", False))
        except (OSError, ValueError, AttributeError):
            pass

    def _save_ui_settings(self) -> None:
        try:
            self._settings.config_dir.mkdir(parents=True, exist_ok=True)
            self._ui_settings_path().write_text(
                json.dumps({"reduced_motion": self._v["reducedMotion"]}), "utf-8")
        except OSError:
            pass

    @Slot()
    def toggleReducedMotion(self) -> None:
        self._set("reducedMotion", not self._v["reducedMotion"])
        self._save_ui_settings()

    # ------------------------------------------------------------------ phases and timers
    def _set_sender_phase(self, phase: str) -> None:
        self._set("senderPhase", phase)

    def _after(self, ms: int, only_if: str, then: str) -> None:
        self._phase_next = (only_if, then)
        self._phase_timer.start(ms)

    def _phase_timeout(self) -> None:
        if self._phase_next and self._v["senderPhase"] == self._phase_next[0]:
            self._set_sender_phase(self._phase_next[1])
            self._refresh_can_send()
        self._phase_next = None

    # ------------------------------------------------------------------ camera and gestures
    def _gesture_config(self) -> GestureConfig:
        for path in (self._settings.config_dir / "gesture_config.json", Path("gesture_config.json")):
            if path.exists():
                try:
                    return GestureConfig.load(path)
                except ValueError as exc:
                    self._toast(f"Ignoring gesture settings: {exc}")
        return GestureConfig()

    @Slot()
    def toggleCamera(self) -> None:
        if self._v["cameraActive"] or self._v["cameraStarting"]:
            self._stop_camera()
        else:
            self._start_camera()

    def _start_camera(self) -> None:
        if self._worker is not None:
            return
        worker = self._worker_factory(self._gesture_config(), self._v["mode"],
                                      camera_index=self._settings.camera_index)
        worker.live.connect(self._camera_live)
        worker.frameReady.connect(self._on_frame)
        worker.statusReady.connect(self._on_gesture_status)
        worker.gestures.connect(self._on_gestures)
        worker.failed.connect(self._camera_failed)
        self._worker = worker
        self._set("cameraStarting", True)
        self._set("status", "Starting camera…")
        worker.start()

    def _camera_live(self) -> None:
        self._set("cameraStarting", False)
        self._set("cameraActive", True)
        self._set("status", "Camera live · gestures active")

    def _camera_failed(self, message: str) -> None:
        self._stop_camera()
        self._set("status", message)
        self._toast(message)

    def _stop_camera(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            for signal in (worker.live, worker.frameReady, worker.statusReady, worker.gestures,
                           worker.failed):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            worker.stop()
        self._set("cameraStarting", False)
        self._set("cameraActive", False)
        self._set("handPresent", False)
        self._set("gestureState", "NO HAND")
        self._set("gestureProgress", 0.0)
        self._set("gestureHint", "Start the camera to use gestures")
        if self._v["senderPhase"] == "holding":
            self._set_sender_phase("idle")
        if not self._closed:
            self._set("status", "Camera off")

    def _on_frame(self, image: QImage) -> None:
        self._camera_provider.set_image(image)
        self._set("frameCounter", self._v["frameCounter"] + 1)

    def _on_gesture_status(self, status) -> None:
        self._set("gestureState", status.state.replace("_", " ").upper())
        self._set("gestureHint", status.hint)
        self._set("gestureProgress", round(status.progress, 2))
        self._set("handPresent", status.hand_present)
        if status.palm_center is not None and status.hand is not None:
            aspect = status.hand.aspect or 1.0
            self._set("handX", round(min(1.0, max(0.0, status.palm_center[0] / aspect)), 3))
            self._set("handY", round(min(1.0, max(0.0, status.palm_center[1])), 3))

    def _on_gestures(self, events) -> None:
        """Gesture events in; decisions out. Nothing here touches the network directly."""
        for event in events:
            kind = event.kind
            if kind == "grab":
                if not self._v["hasFile"]:
                    self._toast("Choose a file first, then grab it.")
                elif self._v["senderPhase"] == "idle":
                    self._set_sender_phase("holding")
            elif kind == "throw":
                if self._v["mode"] == "send":
                    if not self._start_send(event.direction) and self._v["senderPhase"] == "holding":
                        self._set_sender_phase("idle")
            elif kind in ("drop", "cancelled"):
                if self._v["senderPhase"] == "holding":
                    self._set_sender_phase("idle")
            elif kind == "catch":
                if self._v["receivePhase"] == "offered":
                    self._log("Caught", self._v["incomingName"])
                    self.acceptIncoming()
                else:
                    self._toast("Nothing to catch right now.")

    def _apply_mode(self, mode: str) -> None:
        self._set("mode", mode)
        if self._worker is not None:
            self._worker.set_mode(mode)
        if mode != "send" and self._v["senderPhase"] == "holding":
            self._set_sender_phase("idle")

    @Slot()
    def setSendMode(self) -> None:
        self._mode_before_offer = None
        self._apply_mode("send")

    @Slot()
    def setReceiveMode(self) -> None:
        self._mode_before_offer = None
        self._apply_mode("receive")

    # ------------------------------------------------------------------ choosing a file
    @Slot(str)
    def chooseFile(self, value: str) -> None:
        if self._v["senderPhase"] in BUSY_PHASES:
            self._toast("Wait for the current transfer to finish.")
            return
        text = str(value).strip()
        path = Path(QUrl(text).toLocalFile()) if text.startswith("file:") else Path(text)
        try:
            if not path.is_file():
                self._toast("That is not a file AirCatch can send.")
                return
            size = path.stat().st_size
        except OSError:
            self._toast("Could not read that file.")
            return
        kind, badge = file_kind(path.name)
        image = load_preview(path)
        self._thumb_provider.set_image("send", image)
        self._thumb_jpeg = jpeg_for_offer(image)
        self._file_path = path
        self._set("fileName", path.name)
        self._set("fileSize", human_size(size))
        self._set("fileKind", kind)
        self._set("fileBadge", badge)
        self._set("hasThumb", not image.isNull())
        self._set("thumbVersion", self._v["thumbVersion"] + 1)
        self._set("hasFile", True)
        self._set_sender_phase("idle")
        self._refresh_can_send()

    @Slot()
    def clearFile(self) -> None:
        if self._v["senderPhase"] in BUSY_PHASES:
            return
        self._file_path = None
        self._thumb_jpeg = b""
        for name, value in (("hasFile", False), ("fileName", ""), ("fileSize", ""),
                            ("hasThumb", False), ("senderPhase", "idle")):
            self._set(name, value)
        self._refresh_can_send()

    # ------------------------------------------------------------------ devices
    def _selected_device(self) -> dict | None:
        index = self._v["selectedDevice"]
        items = self._devices.items
        return items[index] if 0 <= index < len(items) else None

    def _refresh_can_send(self) -> None:
        device = self._selected_device()
        ready = bool(self._v["hasFile"] and device and device["paired"]
                     and self._v["senderPhase"] not in BUSY_PHASES)
        self._set("canSend", ready)
        self._set("selectedDeviceName", device["name"] if device else "")

    @Slot(int)
    def selectDevice(self, index: int) -> None:
        items = self._devices.items
        if 0 <= index < len(items) and items[index]["paired"]:
            self._set("selectedDevice", index)
        self._refresh_can_send()

    @Slot(str)
    def setManualAddress(self, text: str) -> None:
        self._set("manualAddress", text.strip())

    @Slot(int)
    def unpairDevice(self, index: int) -> None:
        items = self._devices.items
        if 0 <= index < len(items) and items[index]["paired"]:
            name = items[index]["name"]
            if self._service.unpair(items[index]["device_id"]):
                self._toast(f"Unpaired {name}. Do the same on the other device.")
                self._log("Unpaired", name)

    def _update_devices(self, items: list[dict]) -> None:
        previous = self._selected_device()
        keep_id = previous["device_id"] if previous else ""
        self._devices.replace(items)
        index = next((i for i, d in enumerate(items) if d["paired"] and d["device_id"] == keep_id), -1)
        if index < 0:
            index = next((i for i, d in enumerate(items) if d["paired"]), -1)
        self._set("selectedDevice", index)
        self._refresh_can_send()

    # ------------------------------------------------------------------ sending
    @Slot()
    def sendSelected(self) -> None:
        """The button version of a throw."""
        self._start_send((0.0, -1.0))

    def _start_send(self, direction=(0.0, -1.0)) -> bool:
        """Start a REAL transfer. Returns False (and explains why) when it cannot start."""
        if self._v["senderPhase"] in BUSY_PHASES:
            self._toast("A transfer is already running.")
            return False
        if self._file_path is None:
            self._toast("Choose a file first.")
            return False
        device = self._selected_device()
        if device is None or not device["paired"]:
            self._toast("Pair a device and select it first.")
            return False
        if not device["online"] and not self._v["manualAddress"]:
            self._toast(f"{device['name']} is not visible on the network. Check that AirCatch is "
                        "open there, or type its address.")
            return False
        if not self._file_path.is_file():
            self._toast("That file no longer exists.")
            self.clearFile()
            return False
        dx, dy = direction
        length = (dx * dx + dy * dy) ** 0.5
        dx, dy = (dx / length, dy / length) if length > 0.2 else (0.0, -1.0)
        self._set("throwDx", dx)
        self._set("throwDy", dy)
        self._set("sendProgress", 0.0)
        self._set("senderMessage", "")
        self._set("transferLabel", f"Sending {self._v['fileName']} to {device['name']}")
        self._set("transferProgress", 0.0)
        self._set_sender_phase("launched")
        self._refresh_can_send()
        self._after(750, "launched", "waiting")
        self._service.send(device["device_id"], str(self._file_path), self._thumb_jpeg,
                           self._v["manualAddress"])
        return True

    @Slot()
    def cancelSend(self) -> None:
        if self._v["senderPhase"] in BUSY_PHASES:
            self._service.cancel_send()

    def _send_failed(self, text: str) -> None:
        if self._v["senderPhase"] not in BUSY_PHASES:
            return
        self._set("senderMessage", text)
        self._set_sender_phase("failed")
        self._log("Send failed", f"{self._v['fileName']} · {text}")
        self._toast(text)
        self._refresh_can_send()
        self._after(3200, "failed", "idle")

    # ------------------------------------------------------------------ incoming files
    def _show_offer(self, offer: dict) -> None:
        if self._v["receivePhase"] != "none":
            self._service.answer_offer(offer["transfer_id"], False)   # one at a time
            self._toast(f"Declined a second incoming file from {offer['sender']} (busy).")
            return
        self._dismiss_timer.stop()
        name = offer["filename"]
        kind, badge = file_kind(name)
        image = decode_incoming_preview(offer.get("thumbnail", b""))
        self._thumb_provider.set_image("recv", image)
        self._offer_id = offer["transfer_id"]
        for key, value in (("incomingName", name), ("incomingSender", offer["sender"]),
                           ("incomingSize", human_size(offer["size"])), ("incomingKind", kind),
                           ("incomingBadge", badge), ("hasIncomingThumb", not image.isNull()),
                           ("incomingThumbVersion", self._v["incomingThumbVersion"] + 1),
                           ("receiveProgress", 0.0), ("receiveMessage", ""), ("receivedName", "")):
            self._set(key, value)
        self._set("receivePhase", "offered")
        self._set("incomingActive", True)
        self._set("transferLabel", f"Incoming {name} from {offer['sender']}")
        self._set("transferProgress", 0.0)
        if self._mode_before_offer is None:                # a catch gesture needs receive mode
            self._mode_before_offer = self._v["mode"]
        self._apply_mode("receive")

    def _end_incoming(self) -> None:
        self._dismiss_timer.stop()
        self._offer_id = ""
        self._set("receivePhase", "none")
        self._set("incomingActive", False)
        if self._mode_before_offer is not None:
            restore, self._mode_before_offer = self._mode_before_offer, None
            self._apply_mode(restore)

    @Slot()
    def acceptIncoming(self) -> None:
        if self._v["receivePhase"] != "offered":
            return
        self._set("receivePhase", "receiving")
        self._service.answer_offer(self._offer_id, True)

    @Slot()
    def declineIncoming(self) -> None:
        if self._v["receivePhase"] != "offered":
            return
        self._service.answer_offer(self._offer_id, False)
        self._log("Declined", self._v["incomingName"])
        self._end_incoming()

    @Slot()
    def dismissIncoming(self) -> None:
        if self._v["receivePhase"] in ("arrived", "failed"):
            self._end_incoming()

    def _received_path(self) -> Path | None:
        path = Path(self._received) if self._received else None
        return path if path is not None and path.exists() else None

    @Slot()
    def openReceived(self) -> None:
        path = self._received_path()
        if path is None:
            self._toast("The received file could not be found.")
            return
        if path.suffix.lower() in RISKY_EXTENSIONS:
            self._toast("For safety AirCatch does not launch programs. Showing the folder instead.")
            self.showReceivedFolder()
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot()
    def showReceivedFolder(self) -> None:
        path = self._received_path()
        folder = path.parent if path is not None else self._settings.download_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ------------------------------------------------------------------ events from the network
    def _on_net(self, kind: str, payload: dict) -> None:
        if self._closed:
            return
        handler = getattr(self, f"_net_{kind}", None)
        if handler is not None:
            handler(payload)

    def _net_status(self, p: dict) -> None:
        self._set("networkReady", bool(p.get("ready")))
        self._set("deviceName", p.get("name", ""))
        if not self._v["cameraActive"]:
            self._set("status", p["text"])
        if not p.get("ready"):
            self._toast(p["text"])

    def _net_devices(self, p: dict) -> None:
        self._update_devices(p["items"])

    def _net_offer(self, p: dict) -> None:
        self._show_offer(p)

    def _net_offer_closed(self, p: dict) -> None:
        if p["transfer_id"] == self._offer_id and self._v["receivePhase"] == "offered":
            self._toast("The incoming file expired or was cancelled.")
            self._end_incoming()

    def _net_transfer(self, p: dict) -> None:
        event = p["event"]
        if event.direction == "send":
            self._on_send_event(event)
        elif event.transfer_id == self._offer_id:
            self._on_receive_event(event)

    def _on_send_event(self, event) -> None:
        if self._v["senderPhase"] not in BUSY_PHASES:
            return
        if event.kind == "accepted":
            self._set_sender_phase("sending")
        elif event.kind == "progress" and event.total:
            self._set_sender_phase("sending")
            fraction = min(1.0, event.bytes_done / event.total)
            self._set("sendProgress", fraction)
            self._set("transferProgress", fraction)
        elif event.kind == "completed":
            self._set("sendProgress", 1.0)
            self._set("transferProgress", 1.0)
            self._set("senderMessage", f"Delivered to {event.peer or 'the other device'}")
            self._set_sender_phase("done")
            self._log("Sent", f"{event.filename} → {event.peer}")
            self._refresh_can_send()
            self._after(2600, "done", "idle")

    def _net_send_result(self, p: dict) -> None:
        if not p["ok"]:
            self._send_failed(p["text"] or "The transfer failed.")

    def _on_receive_event(self, event) -> None:
        if event.kind == "accepted":
            if self._v["receivePhase"] == "offered":
                self._set("receivePhase", "receiving")
        elif event.kind == "progress" and event.total:
            fraction = min(1.0, event.bytes_done / event.total)
            self._set("receiveProgress", fraction)
            self._set("transferProgress", fraction)
        elif event.kind == "verifying":
            self._set("receivePhase", "verifying")
        elif event.kind == "completed":                   # saved AND hash verified
            self._received = event.path
            self._set("receiveProgress", 1.0)
            self._set("transferProgress", 1.0)
            self._set("receivedName", Path(event.path).name if event.path else event.filename)
            self._set("receivePhase", "arrived")
            self._log("Received", f"{event.filename} ← {event.peer}")
            self._dismiss_timer.start(15000)
        elif event.kind == "failed":
            self._set("receiveMessage", reason_text(event.message))
            self._set("receivePhase", "failed")
            self._log("Receive failed", f"{event.filename} · {event.message}")
            self._dismiss_timer.start(5000)

    # ------------------------------------------------------------------ pairing
    @Slot(result=bool)
    def openPairing(self) -> bool:
        uri = self._service.open_pairing()
        if not uri:
            self._toast("The network service is still starting. Try again in a moment.")
            return False
        self._set("pairingUri", uri)
        try:
            import qrcode
            qr = qrcode.QRCode(box_size=8, border=2)
            qr.add_data(uri)
            qr.make(fit=True)
            pil = qr.make_image(fill_color="#07111f", back_color="#e9fbff").convert("RGB")
            image = QImage(pil.tobytes(), pil.width, pil.height, pil.width * 3,
                           QImage.Format.Format_RGB888).copy()
            self._qr_provider.set_image(image)
            self._set("qrVersion", self._v["qrVersion"] + 1)
        except Exception as exc:
            self._toast(f"Could not draw the QR code: {exc}")
        return True

    @Slot()
    def closePairing(self) -> None:
        self._service.close_pairing()
        self._set("pairingUri", "")

    @Slot(bool)
    def answerPairing(self, accepted: bool) -> None:
        self._service.answer_pairing(accepted)

    @Slot(str)
    def joinPairing(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.joinFinished.emit(False, "Paste the pairing code first.")
            return
        self._service.join_pairing(text)

    def _net_pairing_request(self, p: dict) -> None:
        self.pairingRequest.emit(p["name"], p["address"])

    def _net_pairing_closed(self, p: dict) -> None:
        self.pairingClosed.emit()

    def _net_paired(self, p: dict) -> None:
        self._set("pairingUri", "")
        self._toast(f"Paired with {p['name']}")
        self._log("Paired", p["name"])

    def _net_join_result(self, p: dict) -> None:
        self.joinFinished.emit(bool(p["ok"]), p["text"])
        if p["ok"]:
            self._log("Paired", p["text"])

    # ------------------------------------------------------------------ shutdown
    @Slot()
    def shutdown(self) -> None:
        if self._closed:
            return
        self._phase_timer.stop()
        self._dismiss_timer.stop()
        self._stop_camera()
        self._closed = True
        self._pump.stop()
        self._service.stop()
