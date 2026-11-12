"""AirCatch desktop application.

Two instances on one PC (for testing) need separate identities, folders and ports:

  python run.py --config-dir devices\A --name Laptop-A --download-dir devices\A\inbox --port 5001
  python run.py --config-dir devices\B --name Laptop-B --download-dir devices\B\inbox --port 5002
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from aircatch.ui.controller import (AppController, AppSettings, ImageProvider, ThumbProvider,
                                    default_config_dir)

UI_DIR = Path(__file__).parent / "ui"


def parse_settings(argv: list[str]) -> tuple[AppSettings, list[str]]:
    """Our options from the command line (or AIRCATCH_* environment variables); Qt keeps the rest."""
    parser = argparse.ArgumentParser(prog="python run.py", description="AirCatch")
    parser.add_argument("--config-dir", default=os.environ.get("AIRCATCH_CONFIG_DIR"),
                        help="where this device keeps its identity and paired devices")
    parser.add_argument("--download-dir", default=os.environ.get("AIRCATCH_DOWNLOAD_DIR"),
                        help="where received files are saved (default: Downloads/AirCatch)")
    parser.add_argument("--name", default=os.environ.get("AIRCATCH_NAME"), help="device name")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AIRCATCH_PORT", "0")),
                        help="listening port (default: any free port)")
    parser.add_argument("--no-advertise", action="store_true", help="do not announce this device")
    parser.add_argument("--interface", default=os.environ.get("AIRCATCH_INTERFACE"),
                        help="local IP of the network adapter to use for discovery (Windows fix)")
    parser.add_argument("--camera", type=int, default=0, help="camera number (default 0)")
    args, rest = parser.parse_known_args(argv)
    settings = AppSettings(
        config_dir=Path(args.config_dir) if args.config_dir else default_config_dir(),
        download_dir=Path(args.download_dir) if args.download_dir
        else Path.home() / "Downloads" / "AirCatch",
        name=args.name, port=args.port, advertise=not args.no_advertise,
        interface=args.interface, camera_index=args.camera)
    return settings, rest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    settings, qt_args = parse_settings(argv[1:])
    app = QGuiApplication([argv[0], *qt_args])
    app.setApplicationName("AirCatch")
    app.setOrganizationName("AirCatch")
    logo = UI_DIR / "assets" / "aircatch-logo.png"
    if logo.exists():
        app.setWindowIcon(QIcon(str(logo)))
    QQuickStyle.setStyle("Basic")           # a plain base we fully restyle in QML

    camera, qr, thumbs = ImageProvider(), ImageProvider(), ThumbProvider()
    engine = QQmlApplicationEngine()
    controller = AppController(camera, qr, thumbs, settings, parent=engine)
    engine.addImageProvider("camera", camera)
    engine.addImageProvider("qr", qr)
    engine.addImageProvider("thumb", thumbs)
    engine.rootContext().setContextProperty("backend", controller)
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "main.qml")))
    if not engine.rootObjects():
        controller.shutdown()
        return 1
    app.aboutToQuit.connect(controller.shutdown)
    code = app.exec()
    controller.shutdown()
    del engine                              # tear the UI down before the controller
    return code


if __name__ == "__main__":
    raise SystemExit(main())
