"""Gesture engine tests, using simulated hands (no camera, no person needed).

These prove the LOGIC is right. They cannot prove how well MediaPipe sees YOUR hand in YOUR room;
that is what `python gesture_demo.py` is for.
"""
import json
import math
import random
import sys
import time

import pytest

from aircatch.gestures import GestureConfig, GestureEngine, Pose, suggest_throw_speed
from aircatch.gestures.pose import PoseClassifier
from aircatch.gestures.synthetic import Seg, kinds, make_hand, render, run
from aircatch.gestures.tracker import HandTracker, TrackerError, assess_lighting


def send(**cfg):
    return GestureEngine(GestureConfig(**cfg), "send")


def receive(**cfg):
    return GestureEngine(GestureConfig(**cfg), "receive")


def flick(end=(0.8, 0.5), start=(0.5, 0.5)):
    """Fist travelling fast, then the hand opens: a throw."""
    return [Seg(0.15, "fist", start=start, end=end), Seg(0.4, "open", start=end)]


FLICK_RIGHT = [Seg(0.6, "open"), Seg(0.4, "fist")] + flick()


# =========================================================== hand shapes
@pytest.mark.parametrize("name,expected", [
    ("open", Pose.OPEN), ("fist", Pose.FIST), ("point", Pose.OTHER),
    ("victory", Pose.OTHER), ("three", Pose.OTHER), ("rock", Pose.OTHER)])
def test_basic_poses(name, expected):
    assert PoseClassifier(GestureConfig()).classify(make_hand(name)).pose == expected


@pytest.mark.parametrize("with_world", [True, False])
def test_poses_do_not_depend_on_how_the_hand_is_turned(with_world):
    """Rotated in the picture, or turned towards the camera: still the same pose."""
    wrong = []
    for rotation in range(0, 360, 30):
        for tilt in (-1.2, 0.0, 1.2):
            for pitch in (-1.0, 0.0, 1.0):
                for name, expected in (("open", Pose.OPEN), ("fist", Pose.FIST)):
                    hand = make_hand(name, rotation=math.radians(rotation), tilt=tilt, pitch=pitch,
                                     with_world=with_world)
                    if PoseClassifier(GestureConfig()).classify(hand).pose != expected:
                        wrong.append((name, rotation, tilt, pitch))
    assert wrong == []


def test_poses_survive_shaky_tracking_and_any_hand_size():
    rng = random.Random(7)
    wrong = 0
    total = 600
    for _ in range(total):
        name, expected = rng.choice((("open", Pose.OPEN), ("fist", Pose.FIST)))
        hand = make_hand(name, palm_size=rng.uniform(0.08, 0.4), rotation=rng.uniform(0, 6.28),
                         tilt=rng.uniform(-1, 1), pitch=rng.uniform(-1, 1), noise=0.03, rng=rng,
                         center=(rng.random(), rng.random()))
        wrong += PoseClassifier(GestureConfig()).classify(hand).pose != expected
    assert wrong / total < 0.02


def _curl_for_ratio(finger, target):
    """How curled must this finger be so that its tip/knuckle ratio is `target`? (bisection)"""
    lo, hi = 0.0, 1.0                                    # 0 = stretched (high ratio), 1 = curled
    for _ in range(40):
        mid = (lo + hi) / 2
        curl = [0.0] * 4
        curl[finger] = mid
        ratio = PoseClassifier(GestureConfig()).classify(make_hand(curl=curl)).ratios[finger]
        lo, hi = (mid, hi) if ratio > target else (lo, mid)
    return (lo + hi) / 2


def test_a_hand_near_the_borderline_does_not_flicker():
    cfg = GestureConfig()
    middle_of_dead_zone = (cfg.curled_ratio + cfg.extended_ratio) / 2          # between the two limits
    curl = [_curl_for_ratio(i, middle_of_dead_zone) for i in range(4)]
    rng = random.Random(3)
    for start, expected in (("open", Pose.OPEN), ("fist", Pose.FIST)):
        clf = PoseClassifier(cfg)
        assert clf.classify(make_hand(start)).pose == expected
        seen = {clf.classify(make_hand(curl=curl, noise=0.004, rng=rng)).pose for _ in range(80)}
        assert seen == {expected}                         # hysteresis: keeps its earlier answer


# =========================================================== sending: grab and throw
def test_throw_sequence():
    events = run(send(), FLICK_RIGHT)
    assert kinds(events) == ["hand_found", "ready", "grab", "throw"]
    throw = events[-1]
    assert throw.speed >= GestureConfig().throw_speed
    assert throw.detail == "right" and throw.direction[0] > 0.95


@pytest.mark.parametrize("end,name", [((0.2, 0.5), "left"), ((0.5, 0.15), "up"), ((0.5, 0.85), "down")])
def test_throw_direction(end, name):
    events = run(send(), [Seg(0.6, "open"), Seg(0.4, "fist")] + flick(end))
    assert kinds(events)[-1] == "throw" and events[-1].detail == name


def test_gentle_release_is_a_drop_not_a_throw():
    slow = [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(0.5, "fist", end=(0.55, 0.5)),
            Seg(0.4, "open", start=(0.55, 0.5))]
    events = run(send(), slow)
    # after the drop the hand is still open, so the engine simply gets ready again
    assert kinds(events) == ["hand_found", "ready", "grab", "drop", "ready"]
    assert events[3].detail == "released" and "throw" not in kinds(events)


def test_simplest_mode_any_release_counts_as_a_throw():
    slow = [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(0.5, "fist", end=(0.55, 0.5)),
            Seg(0.4, "open", start=(0.55, 0.5))]
    assert kinds(run(send(require_motion=False), slow))[-1] == "throw"


def test_same_flick_is_a_throw_at_any_distance_from_the_camera():
    speeds = []
    for size in (0.1, 0.2, 0.4):
        reach = 0.3 * size / 0.2          # the same movement measured in palm lengths
        script = [Seg(0.6, "open", palm_size=size), Seg(0.4, "fist", palm_size=size),
                  Seg(0.15, "fist", palm_size=size, end=(0.5 + reach, 0.5)),
                  Seg(0.4, "open", palm_size=size, start=(0.5 + reach, 0.5))]
        throws = [e for e in run(send(), script) if e.kind == "throw"]
        assert len(throws) == 1
        speeds.append(throws[0].speed)
    assert max(speeds) / min(speeds) < 1.15


@pytest.mark.parametrize("fps", [15, 30, 60, 120])
def test_result_does_not_depend_on_frame_rate(fps):
    assert kinds(run(send(), FLICK_RIGHT, fps=fps)) == ["hand_found", "ready", "grab", "throw"]


def test_result_survives_uneven_frame_timing_and_jitter():
    engine, rng, events = send(), random.Random(5), []
    t_prev = 0.0
    for t, hands in render(FLICK_RIGHT, 30, noise=0.01):
        t = max(t + rng.uniform(-0.008, 0.008), t_prev + 0.001)
        t_prev = t
        events += engine.update(hands, t)
    assert kinds(events) == ["hand_found", "ready", "grab", "throw"]


# =========================================================== never repeat, never misfire
def test_a_visible_gesture_never_triggers_twice():
    events = run(send(), FLICK_RIGHT + [Seg(6, "open", start=(0.8, 0.5))])
    assert kinds(events).count("throw") == 1 and kinds(events).count("grab") == 1


def test_fist_after_a_throw_does_nothing_until_the_palm_opens_again():
    events = run(send(), FLICK_RIGHT + [Seg(5, "fist", start=(0.8, 0.5))])
    assert kinds(events).count("grab") == 1       # only the original grab


def test_cooldown_blocks_a_quick_second_throw_then_allows_one():
    quick = [Seg(0.3, "open", start=(0.8, 0.5)), Seg(0.3, "fist", start=(0.8, 0.5))] + flick()
    events = run(send(), FLICK_RIGHT + quick)
    assert kinds(events).count("throw") == 1       # the quick repeat is ignored
    events = run(send(), FLICK_RIGHT + [Seg(2.0, "open", start=(0.8, 0.5))] + FLICK_RIGHT[1:])
    assert kinds(events).count("throw") == 2       # after the cooldown it works again


def test_a_hand_that_starts_as_a_fist_does_not_grab():
    assert kinds(run(send(), [Seg(3, "fist")])) == ["hand_found"]


@pytest.mark.parametrize("glitch_frames", [1, 2, 3])
def test_short_glitches_do_not_grab(glitch_frames):
    script = [Seg(1.0, "open"), Seg(glitch_frames / 30, "fist"), Seg(1.0, "open")]
    assert "grab" not in kinds(run(send(), script))


def test_odd_hand_shapes_do_not_grab():
    for shape in ("point", "victory", "three", "rock"):
        assert "grab" not in kinds(run(send(), [Seg(0.8, "open"), Seg(1.0, shape)]))


def test_holding_too_long_drops_the_file():
    events = run(send(hold_timeout=1.0), [Seg(0.6, "open"), Seg(3.0, "fist")])
    assert kinds(events)[-1] == "drop" and events[-1].detail == "timeout"


# =========================================================== losing the hand
def test_a_brief_loss_of_tracking_is_tolerated():
    script = [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(0.2, missing=True),
              Seg(0.15, "fist", end=(0.8, 0.5)), Seg(0.4, "open", start=(0.8, 0.5))]
    k = kinds(run(send(), script))
    assert "hand_lost" not in k and k[-1] == "throw"


def test_a_long_loss_cancels_the_gesture():
    script = [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(1.0, missing=True)] + flick()
    events = run(send(), script)
    k = kinds(events)
    assert k[:3] == ["hand_found", "ready", "grab"] and "hand_lost" in k and "cancelled" in k
    assert "throw" not in k                      # the old grab is gone; a new one is needed
    assert [e.detail for e in events if e.kind == "cancelled"] == ["hand_lost"]


def test_hand_found_again_after_loss_starts_fresh():
    script = [Seg(0.6, "open"), Seg(1.0, missing=True), Seg(0.6, "open"), Seg(0.4, "fist")] + flick()
    k = kinds(run(send(), script))
    assert k.count("hand_found") == 2 and k[-1] == "throw"


# =========================================================== catching
def test_catch_sequence():
    events = run(receive(), [Seg(0.8, "open"), Seg(0.4, "fist")])
    assert kinds(events) == ["hand_found", "ready", "catch"]
    assert events[1].detail == "catch"


def test_open_palm_alone_never_catches():
    assert "catch" not in kinds(run(receive(), [Seg(5, "open")]))


def test_a_fist_too_soon_after_the_palm_appears_does_not_catch():
    assert "catch" not in kinds(run(receive(), [Seg(0.2, "open"), Seg(1.0, "fist")]))


def test_catch_cannot_repeat_while_the_hand_stays_closed():
    events = run(receive(), [Seg(0.8, "open"), Seg(6, "fist")])
    assert kinds(events).count("catch") == 1


def test_throwing_motion_does_nothing_in_receive_mode():
    k = kinds(run(receive(), FLICK_RIGHT))
    assert "throw" not in k and "grab" not in k


def test_catch_is_forgotten_if_the_palm_goes_away():
    script = [Seg(0.8, "open"), Seg(1.5, "victory"), Seg(0.5, "fist")]
    assert "catch" not in kinds(run(receive(), script))


# =========================================================== modes
def test_idle_mode_tracks_but_never_triggers_gestures():
    events = run(GestureEngine(mode="idle"), FLICK_RIGHT)
    assert kinds(events) == ["hand_found"]


def test_switching_mode_forgets_half_finished_gestures():
    engine = send()
    run(engine, [Seg(0.6, "open"), Seg(0.4, "fist")])
    assert engine.status.state == "holding"
    engine.set_mode("receive")
    assert engine.status.state == "idle" and engine.mode == "receive"
    with pytest.raises(ValueError):
        engine.set_mode("fly")


# =========================================================== several hands, bad data
def test_follows_the_closest_hand_and_ignores_tiny_background_hands():
    def background(t):
        shape = "open" if int(t * 3) % 2 else "fist"
        return [make_hand(shape, center=(0.85, 0.2), palm_size=0.02),     # far away, tiny
                make_hand("fist", center=(0.15, 0.8), palm_size=0.1)]     # someone behind you
    script = [Seg(d, p, start=s, end=e, extra=background)
              for d, p, s, e in ((0.6, "open", (0.5, 0.5), None), (0.4, "fist", (0.5, 0.5), None),
                                 (0.15, "fist", (0.5, 0.5), (0.8, 0.5)), (0.4, "open", (0.8, 0.5), None))]
    for seg in script:
        seg.palm_size = 0.25
    assert kinds(run(send(), script)) == ["hand_found", "ready", "grab", "throw"]


def test_keeps_following_the_same_hand_when_a_bigger_one_appears():
    def intruder(t):
        return [make_hand("fist", center=(0.85, 0.3), palm_size=0.3)] if t > 0.5 else []
    script = [Seg(0.6, "open", palm_size=0.2, extra=intruder),
              Seg(0.4, "fist", palm_size=0.2, extra=intruder),
              Seg(0.15, "fist", palm_size=0.2, end=(0.7, 0.5), extra=intruder),
              Seg(0.4, "open", palm_size=0.2, start=(0.7, 0.5), extra=intruder)]
    assert kinds(run(send(), script))[-1] == "throw"


def test_unsure_detections_are_ignored():
    engine = send()
    ghost = make_hand("open", confidence=0.3)
    assert engine.update([ghost], 1.0) == [] and engine.status.state == "no_hand"


# =========================================================== feedback for the screen
def test_status_gives_clear_feedback_at_every_step():
    engine = send()
    assert engine.status.hint.startswith("Show your hand") and engine.status.state == "no_hand"
    frames = render([Seg(0.6, "open"), Seg(0.4, "fist")], 30)
    seen = {}
    for t, hands in frames:
        engine.update(hands, t)
        seen.setdefault(engine.status.state, engine.status.hint)
    assert seen["idle"] == "Open your palm" and "fist" in seen["ready"]
    assert engine.status.state == "holding" and "Flick" in engine.status.hint
    assert engine.status.pose == Pose.FIST and engine.status.hand_present
    assert engine.status.fingers == (False, False, False, False)


def test_status_progress_bars_move():
    engine = send()
    values = []
    for t, hands in render([Seg(0.6, "open")], 30):
        engine.update(hands, t)
        values.append(engine.status.progress)
    assert values[0] == 0 or values[0] < 1 and max(values) == 1.0


def test_cooldown_progress_runs_from_0_to_1():
    engine = send()
    run(engine, FLICK_RIGHT)
    assert engine.status.state == "cooldown" and engine.status.progress < 0.5
    last = None
    for t, hands in render([Seg(2.0, "open", start=(0.8, 0.5))], 30, t0=102.0):
        engine.update(hands, t)
        last = engine.status
    assert last.state != "cooldown"


# =========================================================== settings
def test_default_settings_are_valid_and_roundtrip(tmp_path):
    cfg = GestureConfig()
    cfg.throw_speed, cfg.require_motion = 4.5, False
    path = tmp_path / "g.json"
    cfg.save(path)
    assert GestureConfig.load(path) == cfg


@pytest.mark.parametrize("bad", [
    {"throw_speed": 0}, {"cooldown": -1}, {"curled_ratio": 2.0}, {"open_min_fingers": 5},
    {"pose_majority": 0.4}, {"min_hand_confidence": 2}, {"arm_time": -0.1}])
def test_nonsense_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        GestureConfig(**bad).validate()
    with pytest.raises(ValueError):
        GestureEngine(GestureConfig(**bad))


def test_settings_file_problems_give_clear_errors(tmp_path):
    for content in ("not json", "[1, 2]", json.dumps({"throw_spedd": 5}),
                    json.dumps({"throw_speed": "fast"}), json.dumps({"require_motion": 1})):
        path = tmp_path / "bad.json"
        path.write_text(content)
        with pytest.raises(ValueError):
            GestureConfig.load(path)
    with pytest.raises(ValueError):
        GestureConfig.load(tmp_path / "missing.json")


def test_calibration_picks_a_threshold_below_typical_throws():
    assert suggest_throw_speed([12, 14, 16, 18, 20]) == 8.0
    assert suggest_throw_speed([2, 2, 2]) == 1.5          # never absurdly low
    assert suggest_throw_speed([100, 100, 100]) == 10.0   # never absurdly high
    with pytest.raises(ValueError):
        suggest_throw_speed([10, 12])


def test_a_calibrated_threshold_still_throws():
    cfg = GestureConfig(throw_speed=suggest_throw_speed([10, 11, 12]))
    assert kinds(run(GestureEngine(cfg, "send"), FLICK_RIGHT))[-1] == "throw"


# =========================================================== tracker and camera helpers
def test_missing_mediapipe_gives_an_instruction(monkeypatch):
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    with pytest.raises(TrackerError, match="pip install"):
        HandTracker()


def test_lighting_check():
    np = pytest.importorskip("numpy")
    assert assess_lighting(np.zeros((480, 640, 3), np.uint8)) == "dark"
    assert assess_lighting(np.full((480, 640, 3), 255, np.uint8)) == "bright"
    assert assess_lighting(np.full((480, 640, 3), 120, np.uint8)) == "ok"


def test_real_mediapipe_runs_on_pictures_without_hands():
    np = pytest.importorskip("numpy")
    pytest.importorskip("mediapipe")
    with HandTracker() as tracker:
        assert tracker.process(np.zeros((480, 640, 3), np.uint8)) == []
        noise = np.random.RandomState(0).randint(0, 255, (480, 640, 3)).astype(np.uint8)
        assert isinstance(tracker.process(noise), list)


def test_engine_is_fast_enough():
    engine = send()
    frames = [(100 + i / 30, [make_hand("open"), make_hand("fist", center=(0.2, 0.2))])
              for i in range(300)]
    start = time.perf_counter()
    for t, hands in frames:
        engine.update(hands, t)
    per_frame_ms = (time.perf_counter() - start) / len(frames) * 1000
    assert per_frame_ms < 2.0
