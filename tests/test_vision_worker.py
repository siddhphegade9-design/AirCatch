"""The camera thread: frames out, gestures out, clean stop, graceful failures. No camera needed."""
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt

from aircatch.gestures.config import GestureConfig
from aircatch.gestures.synthetic import Seg, render
from aircatch.gestures.tracker import TrackerError
from aircatch.ui.vision_worker import CameraError, VisionWorker

DIRECT = Qt.ConnectionType.DirectConnection      # collect results straight from the worker thread


class FakeSource:
    def __init__(self, fail_after=None):
        self.reads, self.released, self.fail_after = 0, False, fail_after

    def read(self):
        time.sleep(0.003)
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            return False, None
        return True, np.zeros((48, 64, 3), np.uint8)

    def release(self):
        self.released = True


class FakeTracker:
    def __init__(self, script=None, explode_at=None):
        self.script, self.i, self.closed, self.explode_at = script or [], 0, False, explode_at

    def process(self, frame):
        if self.explode_at is not None and self.i >= self.explode_at:
            raise RuntimeError("boom")
        hands = self.script[self.i][1] if self.i < len(self.script) else []
        self.i += 1
        return hands

    def close(self):
        self.closed = True


def run_worker(source, tracker, *, mode="send", clock=time.monotonic, until=lambda r: False, wait=5.0):
    result = {"frames": [], "statuses": [], "events": [], "failed": [], "live": 0}
    worker = VisionWorker(GestureConfig(), mode, source_factory=lambda: source,
                          tracker_factory=lambda: tracker, clock=clock)
    worker.frameReady.connect(lambda img: result["frames"].append(img), DIRECT)
    worker.statusReady.connect(lambda s: result["statuses"].append(s), DIRECT)
    worker.gestures.connect(lambda e: result["events"].extend(e), DIRECT)
    worker.failed.connect(lambda m: result["failed"].append(m), DIRECT)
    worker.live.connect(lambda: result.__setitem__("live", result["live"] + 1), DIRECT)
    worker.start()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and not until(result) and worker.isRunning():
        time.sleep(0.01)
    return worker, result


def test_frames_and_status_flow_and_stop_is_clean(qapp):
    source, tracker = FakeSource(), FakeTracker()
    worker, r = run_worker(source, tracker, until=lambda r: len(r["frames"]) >= 5)
    assert r["live"] == 1 and len(r["frames"]) >= 5
    assert r["frames"][0].width() == 64 and r["frames"][0].height() == 48
    assert r["statuses"][-1].state == "no_hand" and r["statuses"][-1].mode == "send"
    worker.stop()
    assert not worker.isRunning()
    assert source.released and tracker.closed                   # camera light goes off


def test_mode_change_reaches_the_engine(qapp):
    worker, r = run_worker(FakeSource(), FakeTracker(), until=lambda r: len(r["statuses"]) >= 2)
    worker.set_mode("receive")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and r["statuses"][-1].mode != "receive":
        time.sleep(0.01)
    assert r["statuses"][-1].mode == "receive"
    worker.stop()


def test_a_scripted_throw_produces_gesture_events(qapp):
    script = render([Seg(0.5, "open"), Seg(0.4, "fist", start=(0.3, 0.5)),
                     Seg(0.25, "open", start=(0.3, 0.5), end=(0.9, 0.5))], fps=30.0)
    times = iter([t for t, _ in script] + [script[-1][0] + 0.2] * 500)
    worker, r = run_worker(FakeSource(), FakeTracker(script), clock=lambda: next(times),
                           until=lambda r: any(e.kind == "throw" for e in r["events"]))
    worker.stop()
    kinds = [e.kind for e in r["events"]]
    assert "grab" in kinds and "throw" in kinds
    assert next(e for e in r["events"] if e.kind == "throw").detail == "right"
    assert r["statuses"] and any(s.hand_present for s in r["statuses"])


def test_missing_camera_is_a_message_not_a_crash(qapp):
    def no_camera():
        raise CameraError("Could not open the camera.")
    worker = VisionWorker(GestureConfig(), "send", source_factory=no_camera)
    failures = []
    worker.failed.connect(failures.append, DIRECT)
    worker.start()
    assert worker.wait(3000)
    assert failures == ["Could not open the camera."]


def test_tracker_failure_releases_the_camera(qapp):
    source = FakeSource()

    def broken():
        raise TrackerError("MediaPipe is not installed.")
    worker = VisionWorker(GestureConfig(), "send", source_factory=lambda: source, tracker_factory=broken)
    failures = []
    worker.failed.connect(failures.append, DIRECT)
    worker.start()
    assert worker.wait(3000)
    assert failures == ["MediaPipe is not installed."] and source.released


def test_camera_that_stops_delivering_frames_is_reported(qapp):
    source, tracker = FakeSource(fail_after=3), FakeTracker()
    worker, r = run_worker(source, tracker, until=lambda r: bool(r["failed"]))
    assert worker.wait(3000)
    assert "stopped delivering" in r["failed"][0]
    assert source.released and tracker.closed


def test_a_processing_error_is_reported_and_cleaned_up(qapp):
    source, tracker = FakeSource(), FakeTracker(explode_at=2)
    worker, r = run_worker(source, tracker, until=lambda r: bool(r["failed"]))
    assert worker.wait(3000)
    assert "processing error" in r["failed"][0].lower()
    assert source.released and tracker.closed
