"""Record real hand movements for tuning: the camera window tells you what to do, everything the
hand tracker saw is saved, and you get a score at the end.

  python gesture_record.py                    about 3 minutes of prompted gestures
  python gesture_record.py --camera 1         another camera
  python gesture_record.py --replay FILE      score a recording again with the current settings

The file (aircatch-recording-<date>.json.gz) holds only hand landmark numbers and timings,
no pictures. Send it to whoever is tuning the gestures.
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from aircatch.gestures.config import GestureConfig
from aircatch.gestures.engine import GestureEngine
from aircatch.gestures.smoothing import LandmarkSmoother
from aircatch.gestures.types import HandObservation

DIRECTIONS = ("left", "right", "up", "down")


def script(seed: int = 1) -> list[tuple[str, str, float]]:
    """(label, text on screen, seconds). Labels: swipe:<dir>, pinch, peace, rest, casual, still."""
    r = random.Random(seed)
    swipes = [d for d in DIRECTIONS for _ in range(6)]
    r.shuffle(swipes)
    steps = [("rest", "Hold your open hand in the middle of the picture", 4.0)]
    for d in swipes:
        steps.append((f"swipe:{d}", f"Swipe {d.upper()}  (then bring your hand back)", 2.2))
        steps.append(("rest", "Back to the middle. Rest.", 1.3))
    for _ in range(3):
        steps.append(("pinch", "PINCH and hold (thumb + index, other 3 fingers up)", 2.5))
        steps.append(("rest", "Open hand. Rest.", 1.5))
    for _ in range(2):
        steps.append(("peace", "PEACE SIGN and hold", 2.5))
        steps.append(("rest", "Open hand. Rest.", 1.5))
    steps.append(("casual", "Move your hand around casually (no gestures)", 10.0))
    steps.append(("still", "Hold your hand still", 5.0))
    return steps


def _hand_to_json(h: HandObservation) -> dict:
    return {"lm": [[round(v, 5) for v in p] for p in h.landmarks],
            "w": [[round(v, 5) for v in p] for p in h.world] if h.world else None,
            "a": round(h.aspect, 4), "c": round(h.confidence, 3), "hd": h.handedness}


def _hand_from_json(d: dict) -> HandObservation:
    return HandObservation(landmarks=tuple(tuple(p) for p in d["lm"]),
                           world=tuple(tuple(p) for p in d["w"]) if d["w"] else None,
                           handedness=d.get("hd", ""), confidence=d.get("c", 1.0), aspect=d["a"])


class Scorer:
    """Runs the engine (browse mode, like the file explorer) and scores it against the prompts."""

    def __init__(self, config: GestureConfig, smooth: bool = True) -> None:
        self.engine = GestureEngine(config, "browse")
        self.smoother = LandmarkSmoother() if smooth and config.smooth_landmarks else None
        self.windows: list[tuple[str, list[str]]] = []
        self.label = None

    def feed(self, label: str, hands: list[HandObservation], t: float) -> list[str]:
        if label != self.label:
            if label != "rest" or not self.windows:
                self.windows.append((label, []))       # a rest belongs to the prompt before it
            self.label = label
        if self.smoother is not None:
            hands = self.smoother.apply(hands, t)
        got = []
        for e in self.engine.update(hands, t):
            if e.kind in ("swipe", "select", "browse_close", "browse_open"):
                got.append(f"{e.kind}:{e.detail}" if e.kind == "swipe" else e.kind)
        if self.engine.mode != "browse":
            self.engine.set_mode("browse")             # a peace sign closed it: keep testing
        if got and self.windows:
            self.windows[-1][1].extend(got)
        return got

    def report(self) -> str:
        c: dict[str, Counter] = {}
        details = []
        for label, got in self.windows:
            kind = label.split(":")[0]
            if kind == "swipe":
                want = "swipe:" + label.split(":")[1]
                swipes = [g for g in got if g.startswith("swipe")]
                res = ("ok" if got == [want] else "WRONG direction" if any(g != want for g in swipes)
                       else "missed" if not swipes else "extra")
                if any(not g.startswith("swipe") for g in got):
                    res = "select/peace fired"
            elif kind == "pinch":
                res = "ok" if got == ["select"] else "missed" if not got else "wrong"
            elif kind == "peace":
                res = "ok" if got.count("browse_close") == 1 and len(got) == 1 else "missed" if not got else "wrong"
            elif kind == "rest" and not got:
                continue
            else:
                res = "ok" if not got else "FALSE command"
            key = label if kind == "swipe" else kind
            c.setdefault(key, Counter())[res] += 1
            if res != "ok":
                details.append(f"  {label:12} -> {got}")
        lines = ["Score (each prompt must give exactly its own gesture):"]
        total = ok = 0
        for key in sorted(c):
            n = sum(c[key].values())
            total += n
            ok += c[key]["ok"]
            rest = ", ".join(f"{k} {v}" for k, v in c[key].items() if k != "ok")
            lines.append(f"  {key:12} {c[key]['ok']}/{n} ok   {rest}")
        lines.append(f"  OVERALL      {ok}/{total} ok ({100 * ok / max(total, 1):.0f}%)")
        if details:
            lines.append("Problems:")
            lines += details[:40]
        return "\n".join(lines)


def replay(path: str, config: GestureConfig) -> str:
    scorer = Scorer(config)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = json.loads(f.readline())
        for line in f:
            row = json.loads(line)
            scorer.feed(row["p"], [_hand_from_json(h) for h in row["h"]], row["t"])
    return f"{path}: {header.get('fps', '?')} fps, {header.get('size', '?')}\n" + scorer.report()


def record(args, config: GestureConfig) -> int:
    import cv2
    from aircatch.gestures.demo import camera_help, draw, open_camera
    from aircatch.gestures.tracker import HandTracker, TrackerError
    cap = open_camera(args.camera, args.width, args.height)
    if cap is None:
        print(camera_help(args.camera))
        return 1
    try:
        tracker = HandTracker()
    except TrackerError as exc:
        print(exc)
        return 1
    out_path = Path(f"aircatch-recording-{time.strftime('%Y%m%d-%H%M%S')}.json.gz")
    rows, scorer, steps = [], Scorer(config), script()
    frames, t_start, banner_until, banner = 0, time.monotonic(), 0.0, ""
    size = "?"
    try:
        for label, text, secs in steps:
            end = time.monotonic() + secs
            while time.monotonic() < end:
                ok, frame = cap.read()
                if not ok:
                    print("The camera stopped sending pictures.")
                    return 1
                frame = cv2.flip(frame, 1)
                size = f"{frame.shape[1]}x{frame.shape[0]}"
                now = time.monotonic()
                hands = tracker.process(frame)
                rows.append({"t": round(now - t_start, 4), "p": label, "h": [_hand_to_json(h) for h in hands]})
                got = scorer.feed(label, hands, now - t_start)
                if got:
                    banner, banner_until = " ".join(g.replace("swipe:", "").upper() for g in got), now + 0.8
                frames += 1
                st = scorer.engine.status
                fps = frames / max(now - t_start, 1e-6)
                lines = [(text, (0, 255, 255)),
                         (f"seen: {st.swipe_note or '-'}   ({fps:.0f} fps, {end - now:.1f} s)", (200, 200, 200))]
                draw(frame, st, lines, banner if now < banner_until else "", True)
                cv2.imshow("AirCatch gesture recording (q = stop)", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("Stopped early; saving what was recorded.")
    finally:
        tracker.close()
        cap.release()
        cv2.destroyAllWindows()
    elapsed = max(time.monotonic() - t_start, 1e-6)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"version": 1, "fps": round(frames / elapsed, 1), "size": size}) + "\n")
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(scorer.report())
    print(f"\nSaved {frames} frames ({frames / elapsed:.0f} fps) to {out_path.resolve()}")
    print("Send this file so the gestures can be tuned to your camera.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Record prompted gestures for tuning")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--replay", help="score an existing recording")
    p.add_argument("--settings", help="gesture settings file (default: gesture_config.json if present)")
    args = p.parse_args(argv)
    settings = args.settings or ("gesture_config.json" if Path("gesture_config.json").exists() else None)
    config = GestureConfig.load(settings) if settings else GestureConfig()
    if args.replay:
        print(replay(args.replay, config))
        return 0
    return record(args, config)


if __name__ == "__main__":
    sys.exit(main())
