"""The gesture recorder's file format and scoring (no camera needed)."""
import gzip
import json
import random

from aircatch.gestures import GestureConfig
from aircatch.gestures.record import Scorer, _hand_to_json, replay, script
from aircatch.gestures.synthetic import make_hand


def fake_session(path):
    rng, t, rows = random.Random(2), 0.0, []
    pos = (0.5, 0.5)
    targets = {"left": (0.3, 0.5), "right": (0.7, 0.5), "up": (0.5, 0.25), "down": (0.5, 0.75)}
    for label, _text, secs in script()[:9]:
        n = round(secs * 30)
        for i in range(n):
            t += 1 / 30
            c, pose = (0.5, 0.5), "open"
            if label.startswith("swipe"):
                tx, ty = targets[label.split(":")[1]]
                f = min(1.0, i / 6)                            # 0.2 s out, then stay there
                c = (0.5 + (tx - 0.5) * f, 0.5 + (ty - 0.5) * f)
            elif label == "rest" and rows and rows[-1]["p"].startswith("swipe"):
                pos = rows[-1]["c"]
            if label == "rest" and i < 12 and rows:
                start = pos
                f = (i + 1) / 12
                c = (start[0] + (0.5 - start[0]) * f, start[1] + (0.5 - start[1]) * f)
            h = make_hand(pose, center=c, noise=0.01, rng=rng)
            rows.append({"t": round(t, 4), "p": label, "h": [_hand_to_json(h)], "c": c})
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"version": 1, "fps": 30, "size": "640x480"}) + "\n")
        for r in rows:
            f.write(json.dumps({k: v for k, v in r.items() if k != "c"}) + "\n")


def test_script_covers_every_direction_equally():
    labels = [s[0] for s in script()]
    for d in ("left", "right", "up", "down"):
        assert labels.count(f"swipe:{d}") == 6
    assert "pinch" in labels and "peace" in labels and "casual" in labels


def test_a_recording_replays_and_is_scored(tmp_path):
    path = tmp_path / "rec.json.gz"
    fake_session(path)
    text = replay(str(path), GestureConfig())
    assert "OVERALL" in text and "4/4 ok" in text, text


def test_scorer_counts_a_wrong_direction():
    s = Scorer(GestureConfig())
    s.windows = [("swipe:left", ["swipe:right"]), ("swipe:up", ["swipe:up"])]
    text = s.report()
    assert "WRONG direction 1" in text and "swipe:up     1/1 ok" in text
