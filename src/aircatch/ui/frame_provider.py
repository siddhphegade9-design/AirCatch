"""Lets QML display the latest camera frame via image://camera/..."""
from __future__ import annotations

import threading

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtQuick import QQuickImageProvider


class FrameProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._lock = threading.Lock()  # QML requests images on another thread
        self._image = QImage()

    def set_image(self, image: QImage) -> None:
        with self._lock:
            self._image = image

    def requestImage(self, id: str, size: QSize, requested_size: QSize) -> QImage:
        with self._lock:
            image = self._image
        if image.isNull():
            image = QImage(2, 2, QImage.Format.Format_RGB888)
            image.fill(QColor("#0b1020"))
        return image
