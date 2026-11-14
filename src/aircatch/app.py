r"""AirCatch desktop application.

Two instances on one PC (for testing) need separate identities, folders and ports:

  python run.py --config-dir devices\A --name Laptop-A --download-dir devices\A\inbox --port 5001
  python run.py --config-dir devices\B --name Laptop-B --download-dir devices\B\inbox --port 5002
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from aircatch import __version__
from aircatch.logsetup import setup_logging
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


def _prepare_process() -> object | None:
    """Things an installed Windows program needs before any window exists."""
    for name in ("stdout", "stderr"):            # no console window: a stray print() must not crash
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 (lives as long as the app)
    if os.name != "nt":
        return None
    try:
        import ctypes
        # own taskbar button and icon (not "python"); the installer looks for the mutex to see if we are running
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AirCatch.AirCatch")
        return ctypes.windll.kernel32.CreateMutexW(None, False, "AirCatchApplicationMutex")
    except Exception:                            # cosmetic only: never stop the app for this
        return None


def _take_option(argv: list[str], name: str, has_value: bool) -> tuple[list[str], str | None]:
    """Remove `name` (and its value) from argv; return what was there."""
    if name not in argv:
        return argv, None
    i = argv.index(name)
    if not has_value:
        return argv[:i] + argv[i + 1:], ""
    value = argv[i + 1] if i + 1 < len(argv) else ""
    return argv[:i] + argv[i + 2:], value


def _self_test_checks(controller: AppController, engine: QQmlApplicationEngine) -> dict:
    """What a packaged copy must be able to do. Each entry: ok + a short detail."""
    results: dict[str, dict] = {}

    def check(name, fn):
        try:
            results[name] = {"ok": True, "detail": str(fn())}
        except Exception as exc:                 # report, do not stop at the first one
            results[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def window():
        if not engine.rootObjects():
            raise RuntimeError("main.qml did not load")
        return "main.qml loaded"

    def network():
        if not controller.networkReady:
            raise RuntimeError(controller.status)
        return controller.status

    def tracker():
        import numpy as np
        from aircatch.gestures.tracker import HandTracker
        hands = HandTracker()
        try:
            return f"hand tracker ran, {len(hands.process(np.zeros((480, 640, 3), np.uint8)))} hands in a black picture"
        finally:
            hands.close()

    def camera_library():
        import cv2
        return f"OpenCV {cv2.__version__}"

    def qr_code():
        import qrcode
        return f"QR code with {qrcode.make('aircatch').size[0]} px"

    def secure_generator():
        import ssl
        return f"{ssl.OPENSSL_VERSION}, TLS 1.3 {'yes' if ssl.HAS_TLSv1_3 else 'NO'}"

    check("version", lambda: __version__)
    check("window", window)
    check("network_service", network)
    check("hand_tracker", tracker)
    check("camera_library", camera_library)
    check("qr_code", qr_code)
    check("tls", secure_generator)

    def pair_and_send():
        from aircatch.selftest import pair_and_transfer
        return pair_and_transfer()
    check("pair_and_transfer", pair_and_send)
    return results


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    mutex = _prepare_process()                   # noqa: F841 (kept alive until the app ends)
    argv, wants_version = _take_option(argv, "--version", False)
    if wants_version is not None:
        print(f"AirCatch {__version__}")
        return 0
    # --self-test FILE: start everything in a throw-away folder, write what works to FILE, and exit.
    # Used after packaging to prove the installed program can really start (it has no console to look at).
    argv, self_test = _take_option(argv, "--self-test", True)
    if self_test is not None:
        import tempfile
        scratch = Path(tempfile.mkdtemp(prefix="aircatch-selftest-"))
        argv += ["--config-dir", str(scratch / "config"), "--download-dir", str(scratch / "inbox"),
                 "--no-advertise"]
    settings, qt_args = parse_settings(argv[1:])
    setup_logging(settings.config_dir)
    logging.getLogger("aircatch").info("AirCatch %s starting (packaged=%s)", __version__,
                                       bool(getattr(sys, "frozen", False)))
    qt_log = logging.getLogger("aircatch.qt")

    def route_qt_messages(mode, context, message):
        """Qt and QML warnings and errors go to the log file (routine notes are dropped)."""
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            qt_log.warning("%s", message)
    qInstallMessageHandler(route_qt_messages)
    app = QGuiApplication([argv[0], *qt_args])
    app.setApplicationName("AirCatch")
    app.setOrganizationName("AirCatch")
    app.setApplicationVersion(__version__)
    for icon in ("aircatch-icon.png", "aircatch-logo.png"):       # the square icon; the wide logo as a fallback
        if (UI_DIR / "assets" / icon).exists():
            app.setWindowIcon(QIcon(str(UI_DIR / "assets" / icon)))
            break
    QQuickStyle.setStyle("Basic")           # a plain base we fully restyle in QML

    camera, qr, thumbs = ImageProvider(), ImageProvider(), ThumbProvider()
    engine = QQmlApplicationEngine()
    controller = AppController(camera, qr, thumbs, settings, parent=engine)
    engine.addImageProvider("camera", camera)
    engine.addImageProvider("qr", qr)
    engine.addImageProvider("thumb", thumbs)
    engine.rootContext().setContextProperty("backend", controller)
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "main.qml")))
    if not engine.rootObjects() and self_test is None:
        controller.shutdown()
        return 1
    app.aboutToQuit.connect(controller.shutdown)
    if self_test is not None:
        import json
        import time
        from PySide6.QtCore import QTimer
        started = time.monotonic()
        outcome: dict = {}

        def finish_when_ready():
            if not controller.networkReady and time.monotonic() - started < 30:
                return                                            # give the network service time to start
            ticker.stop()
            outcome.update(_self_test_checks(controller, engine))
            app.quit()
        ticker = QTimer()
        ticker.timeout.connect(finish_when_ready)
        ticker.start(200)
        app.exec()
        controller.shutdown()
        del engine
        passed = bool(outcome) and all(r["ok"] for r in outcome.values())
        report = {"passed": passed, "version": __version__, "packaged": bool(getattr(sys, "frozen", False)),
                  "platform": sys.platform, "python": sys.version.split()[0], "checks": outcome}
        if self_test:
            Path(self_test).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if passed else 1
    code = app.exec()
    controller.shutdown()
    del engine                              # tear the UI down before the controller
    return code


if __name__ == "__main__":
    raise SystemExit(main())
