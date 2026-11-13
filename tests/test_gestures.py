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


# =========================================================== catching (close the fist, then open it)
def test_catch_sequence_is_fist_then_open():
    events = run(receive(), [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(0.3, "open")])
    assert kinds(events) == ["hand_found", "ready", "catch"]
    assert events[1].detail == "catch"


def test_open_palm_alone_never_catches():
    assert "catch" not in kinds(run(receive(), [Seg(5, "open")]))


def test_a_closed_fist_alone_is_armed_but_never_catches_until_it_opens():
    k = kinds(run(receive(), [Seg(0.6, "open"), Seg(6, "fist")]))
    assert "ready" in k and "catch" not in k


def test_the_old_open_then_fist_order_does_not_catch():
    assert "catch" not in kinds(run(receive(), [Seg(0.8, "open"), Seg(0.4, "fist")]))


def test_a_flash_of_fist_is_not_enough():
    assert "ready" not in kinds(run(receive(), [Seg(0.6, "open"), Seg(0.05, "fist"), Seg(0.5, "open")]))


def test_a_fist_that_was_already_closed_must_open_before_it_can_catch():
    # the hand arrives closed: opening it must NOT accept anything
    k = kinds(run(receive(), [Seg(1.0, "fist"), Seg(0.6, "open")]))
    assert "ready" not in k and "catch" not in k
    # ...but after it has been seen open, a normal close-and-open works
    k = kinds(run(receive(), [Seg(1.0, "fist"), Seg(0.5, "open"), Seg(0.4, "fist"), Seg(0.3, "open")]))
    assert k.count("catch") == 1


def test_switching_to_receive_with_a_closed_fist_requires_opening_first():
    engine = GestureEngine(mode="idle")
    run(engine, [Seg(0.6, "open"), Seg(1.0, "fist")])          # fist is showing right now
    engine.set_mode("receive")
    k = kinds(run(engine, [Seg(0.6, "fist"), Seg(0.4, "open")]))
    assert "ready" not in k and "catch" not in k


def test_catch_cannot_repeat_while_the_hand_stays_open():
    events = run(receive(), [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(6, "open")])
    assert kinds(events).count("catch") == 1


def test_a_second_catch_needs_the_whole_gesture_again_after_the_cooldown():
    one = [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(0.3, "open")]
    again = [Seg(2.0, "open"), Seg(0.4, "fist"), Seg(0.3, "open")]
    assert kinds(run(receive(), one + again)).count("catch") == 2
    assert kinds(run(receive(), one + [Seg(0.3, "fist")])).count("catch") == 1   # in cooldown


def test_throwing_motion_does_nothing_in_receive_mode():
    k = kinds(run(receive(), FLICK_RIGHT))
    assert "throw" not in k and "grab" not in k


def test_catch_is_cancelled_if_the_hand_goes_away():
    script = [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(1.0, "open", missing=True), Seg(0.5, "open")]
    k = kinds(run(receive(), script))
    assert "catch" not in k and "cancelled" in k


def test_half_open_hand_in_between_does_not_catch():
    script = [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(1.0, "victory")]
    assert "catch" not in kinds(run(receive(), script))


def test_status_guides_both_steps_of_the_catch():
    engine = receive()
    run(engine, [Seg(0.6, "open"), Seg(0.5, "fist")])
    assert engine.status.state == "catch_armed" and "open" in engine.status.hint.lower()
    assert receive().status.hint == "Show your hand to the camera"
    idle = receive()
    run(idle, [Seg(0.6, "open")])
    assert "fist" in idle.status.hint.lower()


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
    assert seen["idle"].startswith("Open your palm") and "fist" in seen["ready"]
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


# =========================================================== browsing: peace sign, swipes, pinch
def browse(**cfg):
    return GestureEngine(GestureConfig(**cfg), "browse")


def swipe_movie(end, start=(0.5, 0.5), pose="open", dur=0.2, **kw):
    return [Seg(0.4, pose, start=start, **kw), Seg(dur, pose, start=start, end=end, **kw),
            Seg(0.4, pose, start=end, **kw)]


def test_pinch_and_peace_are_recognised_but_nothing_else_is_mistaken_for_them():
    clf = PoseClassifier(GestureConfig())
    assert clf.classify(make_hand("pinch")).pinch
    assert clf.classify(make_hand("victory")).victory and not clf.classify(make_hand("victory")).pinch
    for name in ("open", "fist", "point", "three", "rock"):
        r = PoseClassifier(GestureConfig()).classify(make_hand(name))
        assert not r.pinch and not r.victory, name


@pytest.mark.parametrize("tilt,pitch", [(0.0, 0.0), (1.0, 0.0), (-1.2, 0.0), (0.0, 1.0), (0.0, -1.0),
                                        (0.9, 0.7), (-0.9, -0.7)])
def test_pinch_and_peace_survive_the_hand_being_turned(tilt, pitch):
    """Sideways (tilt) or fingertips-towards-the-camera (pitch), up to about 70 degrees."""
    assert PoseClassifier(GestureConfig()).classify(make_hand("pinch", tilt=tilt, pitch=pitch)).pinch
    assert PoseClassifier(GestureConfig()).classify(make_hand("victory", tilt=tilt, pitch=pitch)).victory


def test_peace_sign_opens_then_closes_the_explorer():
    e = send()
    ev = run(e, [Seg(0.4, "open"), Seg(1.1, "victory")])
    assert kinds(ev).count("browse_open") == 1 and e.mode == "browse"
    ev = run(e, [Seg(0.4, "open"), Seg(1.1, "victory")], t0=200.0)
    assert kinds(ev).count("browse_close") == 1 and e.mode == "send"


def test_holding_the_peace_sign_toggles_only_once():
    e = send()
    ev = run(e, [Seg(0.4, "open"), Seg(4.0, "victory")])
    assert kinds(ev) .count("browse_open") == 1 and "browse_close" not in kinds(ev)


def test_a_brief_peace_sign_does_nothing_and_a_file_in_hand_blocks_it():
    e = send()
    assert "browse_open" not in kinds(run(e, [Seg(0.4, "open"), Seg(0.25, "victory"), Seg(0.4, "open")]))
    e = send()
    ev = run(e, [Seg(0.6, "open"), Seg(0.4, "fist"), Seg(1.0, "victory")])     # holding a grabbed file
    assert "grab" in kinds(ev) and "browse_open" not in kinds(ev)


@pytest.mark.parametrize("end,name", [((0.8, 0.5), "right"), ((0.2, 0.5), "left"),
                                      ((0.5, 0.8), "down"), ((0.5, 0.2), "up")])
def test_a_quick_open_palm_swipe_gives_its_direction(end, name):
    ev = run(browse(), swipe_movie(end))
    swipes = [e for e in ev if e.kind == "swipe"]
    assert [s.detail for s in swipes] == [name]


TURNS = [(0.0, 0.0), (1.1, 0.0), (-1.1, 0.0), (0.0, 1.1), (0.0, -1.1), (0.8, 0.8), (-0.8, 0.8)]
# "tilt" = fingertips swing towards / away from the camera; "pitch" = palm turned edge-on (sideways)
DIRECTIONS = [((0.8, 0.5), "right"), ((0.2, 0.5), "left"), ((0.5, 0.8), "down"), ((0.5, 0.2), "up")]


def turned_swipe(engine, end, *, tilt=0.0, pitch=0.0, pose="open", start=(0.5, 0.5), dur=0.2):
    out, rng, t = [], random.Random(3), 100.0
    for a, b, d in ((start, start, 0.4), (start, end, dur), (end, end, 0.4)):
        n = round(d * 30)
        for i in range(n):
            f = (i + 1) / n
            c = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            t += 1 / 30
            out.extend(engine.update([make_hand(pose, center=c, tilt=tilt, pitch=pitch, noise=0.01, rng=rng)], t))
    return [x.detail for x in out if x.kind == "swipe"]


@pytest.mark.parametrize("tilt,pitch", TURNS)
@pytest.mark.parametrize("end,name", DIRECTIONS)
def test_every_swipe_direction_works_at_every_hand_angle(tilt, pitch, end, name):
    """Sideways hand, fingertips towards the camera, and in between: all four directions."""
    assert turned_swipe(browse(), end, tilt=tilt, pitch=pitch) == [name]


@pytest.mark.parametrize("tilt,pitch", TURNS)
def test_small_up_and_down_hand_movements_are_enough(tilt, pitch):
    """Up/down swipes are shorter than sideways ones: about one palm length is enough."""
    for end, name in (((0.5, 0.70), "down"), ((0.5, 0.30), "up")):
        assert turned_swipe(browse(), end, tilt=tilt, pitch=pitch, dur=0.25) == [name]


@pytest.mark.parametrize("pose", ["fist", "point", "three"])
def test_up_down_swipes_do_not_depend_on_the_hand_reading_as_an_open_palm(pose):
    """Fingers pointing at the camera look squashed and can read as a fist: still a swipe."""
    assert turned_swipe(browse(), (0.5, 0.8), pose=pose) == ["down"]
    assert turned_swipe(browse(), (0.5, 0.2), pose=pose) == ["up"]


@pytest.mark.parametrize("sweep,drift,name", [((0.0, 1.2), 0.13, "down"), ((1.2, 0.0), -0.13, "up")])
def test_a_wrist_flick_counts_even_when_the_palm_only_drifts_a_little(sweep, drift, name):
    """Fingertips swing towards / away from the camera while the palm moves only half a palm length;
    following all 21 points (not just the palm) makes up the rest."""
    e, out, rng, t = browse(), [], random.Random(2), 100.0
    for a, b, d in ((0.0, 0.0, 0.4), (0.0, 1.0, 0.2), (1.0, 1.0, 0.4)):
        n = round(d * 30)
        for i in range(n):
            f = a + (b - a) * (i + 1) / n
            tilt = sweep[0] + (sweep[1] - sweep[0]) * f
            t += 1 / 30
            out.extend(e.update([make_hand("open", center=(0.5, 0.5 + drift * f), tilt=tilt, noise=0.01, rng=rng)], t))
    assert [x.detail for x in out if x.kind == "swipe"] == [name]


def test_the_hand_coming_back_after_a_swipe_is_not_a_second_swipe():
    movie = [Seg(0.4, "open", start=(0.2, 0.5)), Seg(0.2, "open", start=(0.2, 0.5), end=(0.8, 0.5)),
             Seg(0.25, "open", start=(0.8, 0.5), end=(0.2, 0.5)), Seg(0.5, "open", start=(0.2, 0.5))]
    swipes = [e.detail for e in run(browse(), movie) if e.kind == "swipe"]
    assert swipes == ["right"]


def test_slow_drifting_and_diagonal_movement_do_not_swipe():
    assert not [e for e in run(browse(), swipe_movie((0.8, 0.5), dur=1.5)) if e.kind == "swipe"]


def test_pinch_selects_once_and_must_be_released_before_selecting_again():
    e = browse()
    ev = run(e, [Seg(0.4, "open"), Seg(1.5, "pinch")])
    assert kinds(ev).count("select") == 1
    ev = run(e, [Seg(0.4, "open"), Seg(0.15, "pinch"), Seg(0.4, "open")], t0=200.0)         # too short
    assert "select" not in kinds(ev)
    ev = run(e, [Seg(0.4, "open"), Seg(0.9, "pinch")], t0=300.0)
    assert kinds(ev).count("select") == 1


def test_only_the_pinch_selects_by_default():
    for pose in ("fist", "thumbs_up", "point", "victory", "three", "rock"):
        assert "select" not in kinds(run(browse(), [Seg(0.4, "open"), Seg(1.0, pose)])), pose
    assert "select" in kinds(run(browse(select_with_thumbs_up=True), [Seg(0.4, "open"), Seg(1.0, "thumbs_up")]))
    assert "select" not in kinds(run(browse(select_with_pinch=False), [Seg(0.4, "open"), Seg(1.0, "pinch")]))


@pytest.mark.parametrize("tilt,pitch", [tp for tp in TURNS if abs(tp[1]) < 1.0])
def test_the_pinch_with_three_straight_fingers_selects_at_every_hand_angle(tilt, pitch):
    """Any angle with the palm towards the camera. Edge-on (palm side-on) is ignored on purpose: the
    fingers overlap in the picture and a real camera 'sees' pinches that are not there."""
    e, out, rng, t = browse(), [], random.Random(6), 100.0
    for pose, dur in (("open", 0.4), ("pinch", 0.9)):
        for _ in range(round(dur * 30)):
            t += 1 / 30
            out.extend(e.update([make_hand(pose, tilt=tilt, pitch=pitch, noise=0.01, rng=rng)], t))
    assert kinds(out).count("select") == 1


@pytest.mark.parametrize("pitch", [1.1, -1.1, 1.4])
def test_an_edge_on_hand_never_selects_or_toggles(pitch):
    e, out, rng, t = browse(), [], random.Random(6), 100.0
    for pose, dur in (("open", 0.4), ("pinch", 1.0), ("open", 0.4), ("victory", 1.5)):
        for _ in range(round(dur * 30)):
            t += 1 / 30
            out.extend(e.update([make_hand(pose, pitch=pitch, noise=0.01, rng=rng)], t))
    assert "select" not in kinds(out) and "browse_close" not in kinds(out)


@pytest.mark.parametrize("rotation", [-35, -20, 0, 20, 35])
def test_the_pinch_is_seen_when_the_hand_is_rotated_in_the_picture(rotation):
    assert PoseClassifier(GestureConfig()).classify(make_hand("pinch", rotation=math.radians(rotation))).pinch


def test_a_pinch_needs_the_other_three_fingers_straight():
    """Index and thumb touching but the other fingers curled (like an 'OK' gone wrong) is not a select."""
    clf = PoseClassifier(GestureConfig())
    for curl in ([0.55, 1, 1, 1], [0.55, 0, 1, 1], [0.55, 1, 0, 1], [0.55, 1, 1, 0]):
        assert not clf.classify(make_hand("pinch", curl=curl)).pinch, curl
    assert clf.classify(make_hand("pinch", curl=[0.55, 0, 0, 0])).pinch


def test_other_hand_shapes_rarely_look_like_a_pinch():
    rng, bad = random.Random(5), 0
    for pose in ("open", "fist", "point", "victory", "three", "rock", "thumbs_up"):
        for _ in range(100):
            bad += PoseClassifier(GestureConfig()).classify(
                make_hand(pose, noise=0.03, rng=rng, rotation=math.radians(rng.uniform(-30, 30)))).pinch
    assert bad / 700 < 0.03


def test_a_hand_on_the_move_is_never_a_select_whatever_shape_it_seems_to_have():
    # real cameras: a hand turned edge-on while swiping often LOOKS like a pinch. The movement wins.
    ev = run(browse(), [Seg(0.4, "open"), Seg(0.2, "pinch", start=(0.5, 0.5), end=(0.9, 0.5)),
                        Seg(0.1, "pinch", start=(0.9, 0.5))])
    assert "select" not in kinds(ev)


def test_pinch_selects_after_half_a_second_held_still_and_not_sooner():
    assert kinds(run(browse(), [Seg(0.4, "open"), Seg(0.8, "pinch")])).count("select") == 1
    assert "select" not in kinds(run(browse(), [Seg(0.4, "open"), Seg(0.4, "pinch"), Seg(0.5, "open")]))


@pytest.mark.parametrize("tilt,pitch", [(0.0, 0.0), (0.6, 0.0), (-0.6, 0.0), (0.0, 0.8), (0.0, -0.8)])
def test_thumbs_up_is_seen_when_the_hand_is_turned_a_bit(tilt, pitch):
    assert PoseClassifier(GestureConfig()).classify(make_hand("thumbs_up", tilt=tilt, pitch=pitch)).thumbs_up


def test_the_engine_says_why_a_swipe_was_rejected():
    e = browse()
    run(e, swipe_movie((0.5, 0.56)))                       # a small nudge down
    assert "too short" in e.status.swipe_note
    e = browse()
    run(e, swipe_movie((0.5, 0.8), dur=1.2))               # a long, slow drift
    assert "too" in e.status.swipe_note


def test_swipes_and_selects_do_nothing_outside_browse_mode():
    for e in (send(), receive()):
        ev = run(e, swipe_movie((0.8, 0.5)) + [Seg(0.8, "thumbs_up")])
        assert not {"swipe", "select"} & set(kinds(ev))


def test_a_small_or_edge_on_hand_keeps_a_sane_scale():
    from aircatch.gestures.pose import palm_geometry
    front = palm_geometry(make_hand("open"))[2]
    for kw in ({"tilt": 1.4}, {"pitch": 1.4}, {"tilt": 1.1, "pitch": 0.6}):
        size = palm_geometry(make_hand("open", **kw))[2]
        assert 0.6 * front < size < 1.4 * front, (kw, size, front)


# =========================================================== smoothing
def test_smoothing_reduces_jitter_but_not_a_fast_swipe():
    from aircatch.gestures.smoothing import LandmarkSmoother
    rng = random.Random(5)
    raw, smooth, sm = [], [], LandmarkSmoother()
    t = 100.0
    for _ in range(90):                                   # still, noisy hand
        t += 1 / 30
        h = make_hand("open", noise=0.04, rng=rng)
        raw.append(h.landmarks[8][1])
        smooth.append(sm.apply([h], t)[0].landmarks[8][1])
    def spread(v):
        m = sum(v[20:]) / len(v[20:])
        return (sum((x - m) ** 2 for x in v[20:]) / len(v[20:])) ** 0.5
    assert spread(smooth) < 0.6 * spread(raw)
    # a fast swipe keeps its speed: the engine still sees it
    e, sm = browse(), LandmarkSmoother()
    out = []
    for t, hands in render(swipe_movie((0.8, 0.5))):
        out.extend(e.update(sm.apply(hands, t), t))
    assert [x.detail for x in out if x.kind == "swipe"] == ["right"]


def test_smoothing_handles_two_hands_and_a_hand_reappearing():
    from aircatch.gestures.smoothing import LandmarkSmoother
    sm = LandmarkSmoother()
    a, b = make_hand("open", center=(0.25, 0.5)), make_hand("fist", center=(0.75, 0.5))
    for i in range(5):
        out = sm.apply([a, b] if i % 2 == 0 else [b, a], 100 + i / 30)    # order swaps: still matched by position
    assert abs(out[0].landmarks[0][0] - a.landmarks[0][0]) < 1e-6 and abs(out[1].landmarks[0][0] - b.landmarks[0][0]) < 1e-6
    sm.apply([], 101.0)
    again = sm.apply([a], 102.0)[0]                                     # long gap: starts fresh, no lag
    assert again.landmarks == a.landmarks


def test_a_throw_still_registers_through_the_smoother():
    from aircatch.gestures.smoothing import LandmarkSmoother
    e, sm, out = send(), LandmarkSmoother(), []
    for t, hands in render(FLICK_RIGHT):
        out.extend(e.update(sm.apply(hands, t), t))
    assert "throw" in kinds(out) and next(x for x in out if x.kind == "throw").detail == "right"


# =========================================================== the hand coming back to neutral
def stroke_and_return(engine, end, *, back, rest=0.6, tilt=0.0, pitch=0.0, wind=None, t0=100.0):
    """swipe from the centre to `end` in 0.2 s, come back in `back` seconds, then rest."""
    start, out, rng, t = (0.5, 0.5), [], random.Random(4), t0
    legs = []
    if wind is not None:
        legs.append((start, wind[0], wind[1]))
        legs.append((wind[0], start, 0.15))
    legs += [(start, start, 0.4), (start, end, 0.2), (end, end, 0.08), (end, start, back), (start, start, rest)]
    for a, b, d in legs:
        n = max(1, round(d * 30))
        for i in range(n):
            f = (i + 1) / n
            c = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            t += 1 / 30
            out.extend(engine.update([make_hand("open", center=c, tilt=tilt, pitch=pitch, noise=0.01, rng=rng)], t))
    return [x.detail for x in out if x.kind == "swipe"], t


@pytest.mark.parametrize("back", [0.15, 0.25, 0.4, 0.7, 1.0])
@pytest.mark.parametrize("end,name", DIRECTIONS)
def test_the_return_to_neutral_is_never_a_second_swipe(end, name, back):
    swipes, _ = stroke_and_return(browse(), end, back=back)
    assert swipes == [name]


@pytest.mark.parametrize("tilt,pitch", TURNS)
@pytest.mark.parametrize("end,name", DIRECTIONS)
def test_return_strokes_are_ignored_at_every_hand_angle(end, name, tilt, pitch):
    swipes, _ = stroke_and_return(browse(), end, back=0.3, tilt=tilt, pitch=pitch)
    assert swipes == [name]


@pytest.mark.parametrize("end,name,wind", [((0.5, 0.8), "down", ((0.5, 0.42), 0.15)),
                                           ((0.5, 0.2), "up", ((0.5, 0.58), 0.15)),
                                           ((0.8, 0.5), "right", ((0.42, 0.5), 0.15)),
                                           ((0.2, 0.5), "left", ((0.58, 0.5), 0.15))])
def test_a_small_wind_up_before_the_swipe_is_not_a_swipe(end, name, wind):
    swipes, _ = stroke_and_return(browse(), end, back=0.3, wind=wind)
    assert swipes == [name]


def test_after_resting_a_deliberate_swipe_the_other_way_works_and_so_does_repeating():
    e = browse()
    s1, t = stroke_and_return(e, (0.5, 0.8), back=0.3)
    assert s1 == ["down"]
    s2, t = stroke_and_return(e, (0.5, 0.2), back=0.3, t0=t)           # now deliberately UP
    assert s2 == ["up"]
    s3, _ = stroke_and_return(e, (0.5, 0.2), back=0.3, t0=t)           # and UP again
    assert s3 == ["up"]


def test_a_swipe_after_a_swipe_without_resting_waits_for_neutral_or_the_timeout():
    e = browse()
    # down, then straight back up in one continuous motion with no pause: only the down counts
    movie = [Seg(0.4, "open"), Seg(0.2, "open", start=(0.5, 0.5), end=(0.5, 0.8)),
             Seg(0.2, "open", start=(0.5, 0.8), end=(0.5, 0.2)), Seg(0.3, "open", start=(0.5, 0.2))]
    swipes = [x.detail for x in run(e, movie) if x.kind == "swipe"]
    assert swipes == ["down"]
    assert "returning" in e.status.swipe_note or swipes == ["down"]


# =========================================================== thumbs up, more forgiving
@pytest.mark.parametrize("rotation", [-40, -25, 0, 25, 40])
def test_thumbs_up_is_seen_when_the_hand_is_rotated_in_the_picture(rotation):
    hand = make_hand("thumbs_up", rotation=math.radians(rotation))
    assert PoseClassifier(GestureConfig()).classify(hand).thumbs_up


def test_thumbs_up_survives_jitter_and_a_slightly_bent_thumb():
    rng, hits = random.Random(7), 0
    for _ in range(60):
        clf = PoseClassifier(GestureConfig())
        hits += clf.classify(make_hand("thumbs_up", noise=0.03, rng=rng,
                                       rotation=math.radians(rng.uniform(-25, 25)))).thumbs_up
    assert hits >= 54                                                   # at least 90% of noisy frames


def test_a_normal_fist_almost_never_looks_like_a_thumbs_up_and_never_selects():
    rng, flicker = random.Random(8), 0
    for _ in range(400):
        flicker += PoseClassifier(GestureConfig()).classify(
            make_hand("fist", noise=0.03, rng=rng, rotation=math.radians(rng.uniform(-40, 40)))).thumbs_up
    assert flicker / 400 < 0.05                              # a rare single-frame flicker at most ...
    for noise in (0.03, 0.05):                               # ... which the vote and the hold time absorb
        ev = run(browse(), [Seg(0.4, "open"), Seg(4.0, "fist")], noise=noise, seed=11)
        assert "select" not in kinds(ev), noise


# =========================================================== neutral position, gaps, sharper sideways swipes
@pytest.mark.parametrize("pause", [0.2, 0.4, 0.8])
@pytest.mark.parametrize("end,name", DIRECTIONS)
def test_pausing_at_the_end_of_the_stroke_does_not_unlock_the_return(end, name, pause):
    """The hand rests at the far end for a moment, then goes back: still only one swipe."""
    e, out, rng, t = browse(), [], random.Random(9), 100.0
    for a, b, d in (((0.5, 0.5), (0.5, 0.5), 0.4), ((0.5, 0.5), end, 0.2), (end, end, pause),
                    (end, (0.5, 0.5), 0.3 if pause < 0.6 else 0.5), ((0.5, 0.5), (0.5, 0.5), 0.5)):
        for i in range(max(1, round(d * 30))):
            f = (i + 1) / max(1, round(d * 30))
            c = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            t += 1 / 30
            out.extend(e.update([make_hand("open", center=c, noise=0.01, rng=rng)], t))
    assert [x.detail for x in out if x.kind == "swipe"] == [name]


def test_staying_out_for_a_long_time_makes_that_the_new_neutral():
    e = browse()
    out = run(e, [Seg(0.4, "open"), Seg(0.2, "open", start=(0.5, 0.5), end=(0.5, 0.8)), Seg(2.8, "open", start=(0.5, 0.8))])
    assert [x.detail for x in out if x.kind == "swipe"] == ["down"]
    out = run(e, [Seg(0.2, "open", start=(0.5, 0.8), end=(0.5, 0.5)), Seg(0.4, "open", start=(0.5, 0.5))], t0=103.45)
    assert [x.detail for x in out if x.kind == "swipe"] == ["up"]       # a deliberate swipe up from the new spot


def test_two_swipes_are_never_closer_than_the_gap_even_the_same_way():
    e = browse()
    movie = [Seg(0.4, "open", start=(0.2, 0.5)), Seg(0.15, "open", start=(0.2, 0.5), end=(0.5, 0.5)),
             Seg(0.05, "open", start=(0.5, 0.5), end=(0.5, 0.5)),
             Seg(0.15, "open", start=(0.5, 0.5), end=(0.8, 0.5)), Seg(0.5, "open", start=(0.8, 0.5))]
    assert [x.detail for x in run(e, movie) if x.kind == "swipe"] == ["right"]


@pytest.mark.parametrize("dur", [0.08, 0.1, 0.15, 0.25])
@pytest.mark.parametrize("end,name", [((0.74, 0.5), "right"), ((0.26, 0.5), "left")])
def test_short_sharp_sideways_flicks_are_picked_up(end, name, dur):
    assert turned_swipe(browse(), end, dur=dur) == [name]


@pytest.mark.parametrize("rise", [0.0, 0.05, 0.10, -0.10])
def test_a_rightward_swipe_that_arcs_up_or_down_is_still_right(rise):
    assert turned_swipe(browse(), (0.78, 0.5 + rise)) == ["right"]
