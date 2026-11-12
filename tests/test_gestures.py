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
    ev = run(e, [Seg(0.4, "open"), Seg(0.8, "victory")])
    assert kinds(ev).count("browse_open") == 1 and e.mode == "browse"
    ev = run(e, [Seg(0.4, "open"), Seg(0.8, "victory")], t0=200.0)
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


@pytest.mark.parametrize("tilt,pitch,end,name", [
    (1.1, 0.0, (0.2, 0.5), "left"),        # palm turned ~60 degrees to the camera, moving left
    (-1.1, 0.0, (0.8, 0.5), "right"),
    (0.0, 1.1, (0.5, 0.8), "down"),        # fingertips pointing at the camera, moving down
    (0.0, -1.1, (0.5, 0.2), "up"),
])
def test_swipes_work_with_the_hand_turned_sideways_or_pointing_at_the_camera(tilt, pitch, end, name):
    e = browse()
    frames = []
    t, rng = 100.0, random.Random(3)
    for pos, dur in (((0.5, 0.5), 0.4), (end, 0.2), (end, 0.4)):
        pass
    start = (0.5, 0.5)
    t = 100.0
    out = []
    for center_a, center_b, dur in ((start, start, 0.4), (start, end, 0.2), (end, end, 0.4)):
        n = round(dur * 30)
        for i in range(n):
            f = (i + 1) / n
            c = (center_a[0] + (center_b[0] - center_a[0]) * f, center_a[1] + (center_b[1] - center_a[1]) * f)
            t += 1 / 30
            out.extend(e.update([make_hand("open", center=c, tilt=tilt, pitch=pitch, noise=0.01, rng=rng)], t))
    assert [x.detail for x in out if x.kind == "swipe"] == [name]


def test_the_hand_coming_back_after_a_swipe_is_not_a_second_swipe():
    movie = [Seg(0.4, "open", start=(0.2, 0.5)), Seg(0.2, "open", start=(0.2, 0.5), end=(0.8, 0.5)),
             Seg(0.25, "open", start=(0.8, 0.5), end=(0.2, 0.5)), Seg(0.5, "open", start=(0.2, 0.5))]
    swipes = [e.detail for e in run(browse(), movie) if e.kind == "swipe"]
    assert swipes == ["right"]


def test_slow_drifting_and_diagonal_movement_do_not_swipe():
    assert not [e for e in run(browse(), swipe_movie((0.8, 0.5), dur=1.5)) if e.kind == "swipe"]
    assert not [e for e in run(browse(), swipe_movie((0.64, 0.75))) if e.kind == "swipe"]
    assert not [e for e in run(browse(), swipe_movie((0.8, 0.5), pose="fist")) if e.kind == "swipe"]


def test_pinch_selects_once_and_must_be_released_before_selecting_again():
    e = browse()
    ev = run(e, [Seg(0.4, "open"), Seg(1.5, "pinch")])
    assert kinds(ev).count("pinch") == 1
    ev = run(e, [Seg(0.4, "open"), Seg(0.2, "pinch"), Seg(0.4, "open")], t0=200.0)        # too short
    assert "pinch" not in kinds(ev)
    ev = run(e, [Seg(0.4, "open"), Seg(0.6, "pinch")], t0=300.0)
    assert kinds(ev).count("pinch") == 1


def test_moving_while_pinching_never_swipes():
    ev = run(browse(), [Seg(0.4, "open"), Seg(0.2, "pinch", start=(0.5, 0.5), end=(0.9, 0.5)),
                        Seg(0.5, "pinch", start=(0.9, 0.5))])
    assert "swipe" not in kinds(ev)


def test_swipes_and_pinches_do_nothing_outside_browse_mode():
    for e in (send(), receive()):
        ev = run(e, swipe_movie((0.8, 0.5)) + [Seg(0.8, "pinch")])
        assert not {"swipe", "pinch"} & set(kinds(ev))


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
