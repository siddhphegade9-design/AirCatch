"""Webcam capture on a background thread.

This module only captures frames. It knows nothing about gestures, networking
or the UI, so each of those can be changed independently later.
"""
from __future__ import annotations

import sys
import threading

import cv2
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class CameraWorker(QThread):
    """Reads frames from a webcam and emits them as QImage objects."""

    frame_ready = Signal(QImage)
    error = Signal(str)

    def __init__(self, index: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._index = index
        self._stop_event = threading.Event()

    def run(self) -> None:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._index, backend)
        if not cap.isOpened():
            self.error.emit(
                "Could not open the camera. Check that it is plugged in, "
                "not used by another app, and that Windows camera access is "
                "enabled (Settings > Privacy & security > Camera)."
            )
            return
        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    self.error.emit("The camera stopped delivering frames.")
                    break
                frame = cv2.flip(frame, 1)  # mirror, feels natural
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, _ = rgb.shape
                image = QImage(
                    rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888
                ).copy()  # copy: detach from the numpy buffer
                self.frame_ready.emit(image)
        finally:
            cap.release()

    def stop(self) -> None:
        self._stop_event.set()
        self.wait(3000)
