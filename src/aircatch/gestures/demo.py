"""Live gesture test window: camera + hand skeleton + what the engine understands.

  python gesture_demo.py                       send mode (open palm -> fist -> flick and open)
  python gesture_demo.py --mode receive        catch mode (close your fist, then open it)
  python gesture_demo.py --list-cameras        which camera numbers work (find your phone camera)
  python gesture_demo.py --calibrate           teach it how fast YOUR throws are
  python gesture_demo.py --benchmark 10        speed measurement without a window

Keys:  q / Esc quit   m change mode   t motion required on/off   [ ] throw speed -/+
       c calibrate    s save settings   l landmarks on/off   r reset
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

from aircatch.gestures.config import GestureConfig, suggest_throw_speed
from aircatch.gestures.engine import GestureEngine
from aircatch.gestures.tracker import HandTracker, TrackerError, assess_lighting
from aircatch.gestures.types import MODES, GestureEvent

SKELETON = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
            (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
            (0, 17)]
SETTINGS_FILE = "gesture_config.json"
CALIBRATION_THROWS = 5
BANNERS = {"grab": "GRAB", "throw": "THROW!", "drop": "DROP", "catch": "CATCH!",
           "ready": "READY", "cancelled": "CANCELLED"}


def open_camera(index: int, width: int, height: int):
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        ok, _ = cap.read()
        if ok:
            return cap
        cap.release()
    return None


def list_cameras() -> int:
    import cv2
    print("Looking for cameras (this takes a few seconds) ...")
    found = 0
    for index in range(6):
        cap = open_camera(index, 640, 480)
        if cap is not None:
            ok, frame = cap.read()
            size = f"{frame.shape[1]}x{frame.shape[0]}" if ok else "?"
            print(f"  camera {index}: works ({size})   use:  --camera {index}")
            cap.release()
            found += 1
    if not found:
        print("  no camera found. Check: plugged in / phone-webcam app running, not used by another app,\n"
              "  and Windows Settings > Privacy & security > Camera allows desktop apps.")
    return 0 if found else 1


def camera_help(index: int) -> str:
    return (f"Could not open camera {index}. Check that it is plugged in (or your phone-webcam app is "
            f"running), that no other app is using it, and that Windows allows camera access "
            f"(Settings > Privacy & security > Camera). Try: python gesture_demo.py --list-cameras")


def draw(frame, status, lines, banner, show_landmarks):
    import cv2
    h, w = frame.shape[:2]
    hand = status.hand
    if hand is not None and show_landmarks:
        pts = [(int(x * w), int(y * h)) for x, y, _ in hand.landmarks]
        for a, b in SKELETON:
            cv2.line(frame, pts[a], pts[b], (255, 200, 0), 2)
        for p in pts:
            cv2.circle(frame, p, 3, (0, 255, 255), -1)
    cv2.rectangle(frame, (0, 0), (w, 24 + 20 * len(lines)), (20, 16, 11), -1)
    for i, (text, color) in enumerate(lines):
        cv2.putText(frame, text, (8, 20 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    bar_w = int((w - 16) * status.progress)
    cv2.rectangle(frame, (8, h - 22), (w - 8, h - 8), (60, 50, 40), 1)
    if bar_w > 0:
        cv2.rectangle(frame, (8, h - 22), (8 + bar_w, h - 8), (0, 220, 120), -1)
    if banner:
        size = cv2.getTextSize(banner, cv2.FONT_HERSHEY_SIMPLEX, 2.0, 4)[0]
        cv2.putText(frame, banner, ((w - size[0]) // 2, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 2.0,
                    (0, 255, 255), 4, cv2.LINE_AA)


def run_benchmark(args, config) -> int:
    import cv2
    cap = open_camera(args.camera, args.width, args.height)
    if cap is None:
        print(camera_help(args.camera))
        return 1
    try:
        tracker = HandTracker()
    except TrackerError as exc:
        print(exc)
        return 1
    engine = GestureEngine(config, args.mode)
    cap_ms, trk_ms, eng_ms, hands_seen, frames = [], [], [], 0, 0
    print(f"Measuring for {args.benchmark:g} s. Hold your hand in view for realistic numbers ...")
    end = time.monotonic() + args.benchmark
    while time.monotonic() < end:
        t0 = time.perf_counter()
        ok, frame = cap.read()
        t1 = time.perf_counter()
        if not ok:
            break
        if not args.no_flip:
            frame = cv2.flip(frame, 1)
        hands = tracker.process(frame)
        t2 = time.perf_counter()
        engine.update(hands, time.monotonic())
        t3 = time.perf_counter()
        cap_ms.append((t1 - t0) * 1000)
        trk_ms.append((t2 - t1) * 1000)
        eng_ms.append((t3 - t2) * 1000)
        hands_seen += bool(hands)
        frames += 1
    tracker.close()
    cap.release()
    if not frames:
        print("No frames were captured.")
        return 1

    def line(name, values):
        v = sorted(values)
        print(f"  {name:22} median {statistics.median(v):6.1f} ms   95th percentile {v[int(0.95 * (len(v) - 1))]:6.1f} ms")

    print(f"Frames: {frames} in {args.benchmark:g} s = {frames / args.benchmark:.1f} fps; "
          f"a hand was found in {100 * hands_seen / frames:.0f}% of frames")
    line("camera read", cap_ms)
    line("hand tracking", trk_ms)
    line("gesture engine", eng_ms)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gesture_demo.py", description="Live gesture test window")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--mode", choices=MODES, default="send")
    p.add_argument("--config", help=f"gesture settings file (default: {SETTINGS_FILE} if it exists)")
    p.add_argument("--throw-speed", type=float, help="palm-lengths per second needed for a throw")
    p.add_argument("--no-motion", action="store_true", help="any release after a grab counts as a throw")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--no-flip", action="store_true", help="do not mirror the picture")
    p.add_argument("--list-cameras", action="store_true")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--benchmark", type=float, metavar="SECONDS")
    args = p.parse_args(argv)

    if args.list_cameras:
        return list_cameras()
    settings_path = Path(args.config or SETTINGS_FILE)
    try:
        config = GestureConfig.load(settings_path) if settings_path.exists() else GestureConfig()
        if args.throw_speed is not None:
            config.throw_speed = args.throw_speed
        if args.no_motion:
            config.require_motion = False
        config.validate()
    except ValueError as exc:
        print(f"Settings problem: {exc}")
        return 1
    if args.benchmark:
        return run_benchmark(args, config)

    import cv2
    cap = open_camera(args.camera, args.width, args.height)
    if cap is None:
        print(camera_help(args.camera))
        return 1
    try:
        tracker = HandTracker()
    except TrackerError as exc:
        print(exc)
        cap.release()
        return 1

    engine = GestureEngine(config, args.mode)
    log: list[str] = []
    banner, banner_until = "", 0.0
    show_landmarks = True
    calibrating = args.calibrate
    samples: list[float] = []
    saved_require_motion = config.require_motion
    if calibrating:
        config.require_motion = False
        engine.set_mode("send")
    fps, last = 0.0, time.perf_counter()
    track_ms = 0.0
    window = "AirCatch gesture demo (q = quit)"
    print(__doc__)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("The camera stopped delivering pictures.")
                break
            if not args.no_flip:
                frame = cv2.flip(frame, 1)
            t0 = time.perf_counter()
            hands = tracker.process(frame)
            track_ms = 0.8 * track_ms + 0.2 * (time.perf_counter() - t0) * 1000
            now = time.monotonic()
            events: list[GestureEvent] = engine.update(hands, now)
            for e in events:
                if e.kind in BANNERS:
                    banner, banner_until = BANNERS[e.kind], now + 1.0
                extra = f" speed {e.speed:.1f}" if e.kind in ("throw", "drop") else ""
                log.append(f"{time.strftime('%H:%M:%S')} {e.kind}{extra} {e.detail}".strip())
                log[:] = log[-4:]
                print(log[-1])
                if calibrating and e.kind in ("throw", "drop"):
                    samples.append(e.speed)
                    if len(samples) >= CALIBRATION_THROWS:
                        config.throw_speed = suggest_throw_speed(samples)
                        config.require_motion = saved_require_motion
                        config.save(settings_path)
                        banner, banner_until = f"SAVED {config.throw_speed}", now + 3
                        print(f"Calibrated: throw_speed = {config.throw_speed} (saved to {settings_path})")
                        calibrating = False
            if now > banner_until:
                banner = ""
            tick = time.perf_counter()
            fps = 0.9 * fps + 0.1 / max(tick - last, 1e-6)
            last = tick

            st = engine.status
            light = assess_lighting(frame)
            lines = [
                (f"mode {st.mode.upper()}   state {st.state}   pose {st.pose.value}   "
                 f"fingers {''.join('X' if f else '-' for f in st.fingers)}", (255, 255, 255)),
                (st.hint, (0, 255, 200)),
                (f"speed {st.speed:4.1f}/s  (throw needs {config.throw_speed:g}"
                 f"{'' if config.require_motion else ', motion OFF'})   fps {fps:4.1f}   tracker {track_ms:4.1f} ms",
                 (200, 200, 200)),
            ]
            if calibrating:
                lines.append((f"CALIBRATING: do {CALIBRATION_THROWS} throws ({len(samples)} done)", (0, 200, 255)))
            if light != "ok":
                lines.append((f"Picture is too {light}: improve the lighting", (0, 120, 255)))
            if len(hands) > 1:
                lines.append(("More than one hand: following the closest", (200, 200, 200)))
            lines.extend((text, (170, 170, 170)) for text in log[-3:])
            draw(frame, st, lines, banner, show_landmarks)
            cv2.imshow(window, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord("m"):
                engine.set_mode(MODES[(MODES.index(engine.mode) + 1) % len(MODES)])
            elif key == ord("t"):
                config.require_motion = not config.require_motion
            elif key == ord("["):
                config.throw_speed = max(1.0, config.throw_speed - 0.5)
            elif key == ord("]"):
                config.throw_speed += 0.5
            elif key == ord("l"):
                show_landmarks = not show_landmarks
            elif key == ord("r"):
                engine.set_mode("idle")
                engine.set_mode(args.mode)
            elif key == ord("c"):
                calibrating, samples = True, []
                saved_require_motion, config.require_motion = config.require_motion, False
                engine.set_mode("send")
            elif key == ord("s"):
                config.save(settings_path)
                print(f"Saved settings to {settings_path}")
    finally:
        tracker.close()
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
