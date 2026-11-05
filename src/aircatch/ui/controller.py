"""Bridge between the Python logic and the QML interface."""
from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QImage

from aircatch.ui.frame_provider import FrameProvider
from aircatch.vision.camera import CameraWorker


class AppController(QObject):
    statusChanged = Signal()
    frameCounterChanged = Signal()
    cameraActiveChanged = Signal()

    def __init__(self, provider: FrameProvider, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._worker: CameraWorker | None = None
        self._status = "Camera off"
        self._frame_counter = 0
        self._camera_active = False

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(int, notify=frameCounterChanged)
    def frameCounter(self) -> int:
        return self._frame_counter

    @Property(bool, notify=cameraActiveChanged)
    def cameraActive(self) -> bool:
        return self._camera_active

    @Slot()
    def toggleCamera(self) -> None:
        if self._camera_active:
            self.stopCamera()
        else:
            self.startCamera()

    @Slot()
    def startCamera(self) -> None:
        if self._worker is not None:
            return
        self._worker = CameraWorker(0)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self._set_active(True)
        self._set_status("Camera starting...")

    @Slot()
    def stopCamera(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        self._set_active(False)
        self._set_status("Camera off")

    @Slot()
    def shutdown(self) -> None:
        self.stopCamera()

    def _on_frame(self, image: QImage) -> None:
        self._provider.set_image(image)
        self._frame_counter += 1
        self.frameCounterChanged.emit()
        if self._status != "Camera live":
            self._set_status("Camera live")

    def _on_error(self, message: str) -> None:
        self.stopCamera()
        self._set_status(message)

    def _set_status(self, text: str) -> None:
        self._status = text
        self.statusChanged.emit()

    def _set_active(self, active: bool) -> None:
        self._camera_active = active
        self.cameraActiveChanged.emit()
