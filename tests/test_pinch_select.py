"""The select pinch on a real camera: finger readings flicker and the hand wobbles, but it must still be
picked up, once, and never from a hand that is being moved about."""
import math
import random

import pytest

from aircatch.gestures import GestureConfig, GestureEngine
from aircatch.gestures.synthetic import make_hand


def selects(frames, fps=30):
    e, t, n = GestureEngine(GestureConfig(), "browse"), 100.0, 0
    for hand in frames:
        t += 1 / fps
        n += sum(1 for x in e.update([hand], t) if x.kind == "select")
    return n


def hands(pose, secs, fps=30, rng=None, **kw):
    rng = rng or random.Random(1)
    return [make_hand(pose, noise=0.008, rng=rng, **kw) for _ in range(round(secs * fps))]


@pytest.mark.parametrize("fps", [30, 45, 60])
def test_a_pinch_held_for_about_half_a_second_selects_once(fps):
    assert selects(hands("open", 0.5, fps) + hands("pinch", 0.7, fps) + hands("open", 0.5, fps), fps) == 1


def test_holding_the_pinch_for_a_long_time_still_selects_only_once():
    assert selects(hands("open", 0.5) + hands("pinch", 3.0) + hands("open", 0.5)) == 1


@pytest.mark.parametrize("bent", [1, 2, 3])
def test_one_of_the_other_fingers_reading_as_bent_does_not_stop_the_pinch(bent):
    curl = [0.55, 0.0, 0.0, 0.0]
    curl[bent] = 1.0                                   # middle, ring or pinky reads as curled the whole time
    r = random.Random(2)
    frames = hands("open", 0.5) + [make_hand("pinch", curl=curl, noise=0.008, rng=r) for _ in range(24)] + hands("open", 0.5)
    assert selects(frames) == 1


@pytest.mark.parametrize("seed", range(8))
def test_a_flickering_pinch_is_still_picked_up_once(seed):
    """Every few frames the tracker misreads a finger, or reads the pinch as open for a frame."""
    r = random.Random(seed)
    frames = hands("open", 0.5, rng=r)
    for i in range(30):                                # one second
        curl = [0.55, 0.0, 0.0, 0.0]
        if i % 7 == 3:
            curl[r.choice([1, 2, 3])] = 0.9
        if i % 9 == 5:
            curl[0] = 0.1                              # reads as an open hand for one frame
        frames.append(make_hand("pinch", curl=curl, noise=0.008, rng=r))
    frames += hands("open", 0.5, rng=r)
    assert selects(frames) == 1


def test_a_pinch_released_too_soon_does_not_select():
    assert selects(hands("open", 0.5) + hands("pinch", 0.25) + hands("open", 0.8)) == 0


def test_two_separate_pinches_select_twice():
    one = hands("pinch", 0.8) + hands("open", 0.8)
    assert selects(hands("open", 0.5) + one + one) == 2


@pytest.mark.parametrize("seed", range(6))
def test_a_pinch_shaped_hand_that_is_being_moved_about_never_selects(seed):
    r = random.Random(seed)
    frames, p = hands("open", 0.4, rng=r), (0.5, 0.5)
    for _ in range(10):
        while True:
            q = (0.5 + r.uniform(-0.2, 0.2), 0.5 + r.uniform(-0.2, 0.2))
            if math.hypot((q[0] - p[0]) * 16 / 9, q[1] - p[1]) >= 0.22:
                break
        n = round(r.uniform(0.35, 0.7) * 30)
        frames += [make_hand("pinch", center=(p[0] + (q[0] - p[0]) * (i + 1) / n, p[1] + (q[1] - p[1]) * (i + 1) / n),
                             noise=0.008, rng=r) for i in range(n)]
        p = q
    assert selects(frames) == 0


@pytest.mark.parametrize("pose", ["open", "fist", "point", "three", "rock", "victory"])
def test_other_hand_shapes_never_select(pose):
    assert selects(hands("open", 0.4) + hands(pose, 2.0)) == 0
