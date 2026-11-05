"""Application entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from aircatch.ui.controller import AppController
from aircatch.ui.frame_provider import FrameProvider


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName("AirCatch")

    provider = FrameProvider()
    engine = QQmlApplicationEngine()
    controller = AppController(provider, parent=engine)  # outlive the QML UI
    engine.addImageProvider("camera", provider)
    engine.rootContext().setContextProperty("backend", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "ui" / "main.qml")))
    if not engine.rootObjects():
        return 1

    app.aboutToQuit.connect(controller.shutdown)
    code = app.exec()
    del engine  # tear down the UI before the controller
    return code
