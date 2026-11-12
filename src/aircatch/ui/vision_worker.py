"""Camera + hand tracking + gesture engine on a background thread.

The window thread never waits for the camera or for MediaPipe: this worker reads a frame,
finds the hand, runs the gesture engine, draws the skeleton, and hands the finished picture
and the engine's report to the UI through signals.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

import cv2
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from aircatch.gestures.config import GestureConfig
from aircatch.gestures.engine import GestureEngine
from aircatch.gestures.smoothing import LandmarkSmoother
from aircatch.gestures.tracker import HandTracker, TrackerError

_LINKS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
          (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
          (0, 17))

CAMERA_HELP = ("Could not open the camera. Check that it is plugged in, not used by another app, "
               "and that Windows camera access is enabled (Settings > Privacy & security > Camera).")


class CameraError(Exception):
    pass


def open_camera(index: int):
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        raise CameraError(CAMERA_HELP)
    return cap


def draw_skeleton(frame, landmarks) -> None:
    h, w = frame.shape[:2]
    pts = [(int(x * w), int(y * h)) for x, y, _ in landmarks]
    for a, b in _LINKS:
        cv2.line(frame, pts[a], pts[b], (255, 210, 40), 2, cv2.LINE_AA)
    for p in pts:
        cv2.circle(frame, p, 4, (70, 235, 255), -1, cv2.LINE_AA)


def to_qimage(frame_bgr) -> QImage:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()


class VisionWorker(QThread):
    frameReady = Signal(QImage)
    statusReady = Signal(object)      # EngineStatus
    gestures = Signal(object)         # list[GestureEvent]
    failed = Signal(str)
    live = Signal()

    def __init__(self, config: GestureConfig, mode: str, *, camera_index: int = 0,
                 source_factory: Callable[[], object] | None = None,
                 tracker_factory: Callable[[], object] | None = None,
                 clock: Callable[[], float] = time.monotonic, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._mode = mode
        self._source_factory = source_factory or (lambda: open_camera(camera_index))
        self._tracker_factory = tracker_factory or HandTracker
        self._clock = clock
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending_mode: str | None = None
        self._pending_require_motion: bool | None = None

    def set_mode(self, mode: str) -> None:
        """Thread-safe: takes effect before the next frame."""
        with self._lock:
            self._pending_mode = mode

    def set_require_motion(self, value: bool) -> None:
        """Thread-safe: False = 'easy throw' (any release after a grab sends)."""
        with self._lock:
            self._pending_require_motion = value

    def stop(self) -> None:
        self._stop_event.set()
        if self.isRunning() and QThread.currentThread() is not self:
            self.wait(4000)

    def run(self) -> None:
        try:
            source = self._source_factory()
        except CameraError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"Camera error: {exc}")
            return
        try:
            tracker = self._tracker_factory()
        except TrackerError as exc:
            source.release()
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            source.release()
            self.failed.emit(f"Hand tracking could not start: {exc}")
            return
        try:
            engine = GestureEngine(self._config, self._mode)
            smoother = LandmarkSmoother() if self._config.smooth_landmarks else None
            self.live.emit()
            while not self._stop_event.is_set():
                ok, frame = source.read()
                if not ok:
                    self.failed.emit("The camera stopped delivering frames.")
                    break
                with self._lock:
                    pending, self._pending_mode = self._pending_mode, None
                    motion, self._pending_require_motion = self._pending_require_motion, None
                if pending is not None:
                    engine.set_mode(pending)
                if motion is not None:
                    engine.cfg.require_motion = motion
                frame = cv2.flip(frame, 1)                  # mirror: feels natural
                now = self._clock()
                hands = tracker.process(frame)
                if smoother is not None:
                    hands = smoother.apply(hands, now)
                events = engine.update(hands, now)
                status = engine.status
                if status.hand is not None:
                    draw_skeleton(frame, status.hand.landmarks)
                self.frameReady.emit(to_qimage(frame))
                self.statusReady.emit(status)
                if events:
                    self.gestures.emit(events)
        except Exception as exc:
            self.failed.emit(f"Gesture processing error: {exc}")
        finally:
            with_close = getattr(tracker, "close", None)
            if with_close:
                with_close()
            source.release()
