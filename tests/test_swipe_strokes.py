"""Whole-stroke swipe detection: the real-camera problems reported in testing.

  * a downward arm arc that curls sideways at the end must stay "down" (it was read as "left");
  * the hand coming back after a swipe must never count, even after the hand was lost for a moment;
  * a swipe interrupted by blurred (missing) frames, or leaving the picture, still counts;
  * casually moving the hand to a new spot is not a swipe.
"""
import math
import random

import pytest

from aircatch.gestures import GestureConfig, GestureEngine
from aircatch.gestures.synthetic import make_hand

ASPECT = 16 / 9
DIRS = {"right": (1, 0), "left": (-1, 0), "down": (0, 1), "up": (0, -1)}


class Rig:
    def __init__(self, fps=30, seed=0, palm=0.2):
        self.e = GestureEngine(GestureConfig(), "browse")
        self.t, self.fps, self.r, self.palm = 100.0, fps, random.Random(seed), palm
        self.pos, self.events, self.drop = (0.5, 0.5), [], set()

    def frame(self, c, i=None):
        self.t += 1 / self.fps
        inside = 0.03 < c[0] < 0.97 and 0.03 < c[1] < 0.97
        hands = [make_hand("open", center=c, palm_size=self.palm, noise=0.01, rng=self.r)] \
            if inside and i not in self.drop else []
        self.events += self.e.update(hands, self.t)

    def path(self, fn, dur):
        n = max(2, round(dur * self.fps))
        for i in range(n):
            f = (i + 1) / n
            self.frame(fn(f * f * (3 - 2 * f)), i)
        self.pos = fn(1.0)
        self.drop = set()

    def line(self, b, dur):
        a = self.pos
        self.path(lambda s: (a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s), dur)

    def hold(self, dur):
        for _ in range(round(dur * self.fps)):
            self.frame(self.pos)

    def swipes(self):
        return [e.detail for e in self.events if e.kind == "swipe"]


def target(a, dirn, palms, palm=0.2):
    dx, dy = DIRS[dirn]
    return a[0] + dx * palms * palm / ASPECT, a[1] + dy * palms * palm


def curled(a, dirn, palms, bend, palm=0.2):
    """A stroke that turns by `bend` radians towards its end (an arm swinging round the elbow)."""
    dx, dy = DIRS[dirn]

    def fn(s):
        x = y = 0.0
        for i in range(12):
            u = s * (i + 0.5) / 12
            ang = bend * u * u
            x += (dx * math.cos(ang) - dy * math.sin(ang)) * s / 12
            y += (dx * math.sin(ang) + dy * math.cos(ang)) * s / 12
        return a[0] + x * palms * palm / ASPECT, a[1] + y * palms * palm
    return fn


@pytest.mark.parametrize("fps", [20, 30, 60])
@pytest.mark.parametrize("bend", [-40, -25, 25, 40])
@pytest.mark.parametrize("dirn", list(DIRS))
def test_a_stroke_that_curls_at_the_end_keeps_its_direction(dirn, bend, fps):
    rig = Rig(fps=fps)
    rig.hold(0.5)
    rig.path(curled(rig.pos, dirn, 1.8, math.radians(bend)), 0.3)
    rig.hold(0.3)
    rig.line((0.5, 0.5), 0.5)
    rig.hold(0.6)
    assert rig.swipes() == [dirn]


@pytest.mark.parametrize("pause", [0.0, 0.2, 0.6, 0.9])
@pytest.mark.parametrize("back", [0.15, 0.3, 0.6])
@pytest.mark.parametrize("dirn", list(DIRS))
def test_the_way_back_is_never_a_swipe_however_fast(dirn, back, pause):
    rig = Rig()
    rig.hold(0.5)
    rig.line(target(rig.pos, dirn, 1.8), 0.2)
    rig.hold(pause)
    rig.line((0.5, 0.5), back)                       # as fast as the swipe itself (0.15 s), or slower
    rig.hold(0.8)
    assert rig.swipes() == [dirn]


@pytest.mark.parametrize("dirn", list(DIRS))
def test_the_way_back_does_not_count_after_the_hand_was_lost_for_a_moment(dirn):
    rig = Rig()
    rig.hold(0.5)
    rig.line(target(rig.pos, dirn, 1.8), 0.2)
    for _ in range(9):                               # 0.3 s with no hand (looked away, blur)
        rig.t += 1 / 30
        rig.events += rig.e.update([], rig.t)
    rig.line((0.5, 0.5), 0.2)
    rig.hold(0.8)
    assert rig.swipes() == [dirn]


@pytest.mark.parametrize("dirn", list(DIRS))
@pytest.mark.parametrize("missing", [{2}, {2, 3}, {1, 3}])
def test_a_swipe_with_blurred_frames_still_counts(dirn, missing):
    rig = Rig()
    rig.hold(0.5)
    rig.drop = set(missing)
    rig.line(target(rig.pos, dirn, 1.8), 0.25)
    rig.hold(0.3)
    rig.line((0.5, 0.5), 0.4)
    rig.hold(0.8)
    assert rig.swipes() == [dirn]


@pytest.mark.parametrize("dirn,start", [("right", (0.75, 0.5)), ("left", (0.25, 0.5))])
def test_a_swipe_that_leaves_the_picture_counts(dirn, start):
    rig = Rig()
    rig.pos = start
    rig.hold(0.5)
    rig.line(target(start, dirn, 3.0), 0.25)         # ends outside the picture
    rig.hold(0.3)
    rig.line(start, 0.4)                             # comes back in: not a swipe
    rig.hold(0.8)
    assert rig.swipes() == [dirn]


def test_commands_in_a_row_with_rough_returns():
    r = random.Random(3)
    for seed in range(30):
        rig = Rig(seed=seed)
        rig.hold(0.5)
        want = []
        for _ in range(6):
            dirn = r.choice(list(DIRS))
            want.append(dirn)
            a = rig.pos
            rig.line(target(a, dirn, r.uniform(1.3, 2.2)), r.uniform(0.15, 0.3))
            back = (0.5 + r.uniform(-0.4, 0.4) * 0.2 / ASPECT, 0.5 + r.uniform(-0.4, 0.4) * 0.2)
            rig.line(back, r.uniform(0.25, 0.5))
            rig.hold(r.uniform(0.2, 0.5))
        assert rig.swipes() == want, seed


def test_moving_the_hand_to_a_new_spot_is_not_a_swipe():
    r = random.Random(7)
    rig = Rig()
    for _ in range(30):
        rig.line((r.uniform(0.35, 0.65), r.uniform(0.35, 0.65)), r.uniform(0.8, 1.4))
        rig.hold(r.uniform(0.2, 0.8))
    assert rig.swipes() == []


@pytest.mark.parametrize("fps", [15, 30])
@pytest.mark.parametrize("dirn", list(DIRS))
def test_a_swipe_the_camera_could_not_see_for_motion_blur_still_counts_and_its_return_does_not(dirn, fps):
    """Real webcams lose the hand during the fast part of a swipe. Seen at the start, gone, seen at the end."""
    rig = Rig(fps=fps)
    rig.hold(0.5)
    n = max(3, round(0.25 * fps))
    rig.drop = set(range(1, n - 1))                     # only the first and last frames are seen
    rig.line(target(rig.pos, dirn, 1.8), 0.25)
    rig.hold(0.2)
    n = max(3, round(0.3 * fps))
    rig.drop = set(range(1, n - 1))                     # the way back is blurred too
    rig.line((0.5, 0.5), 0.3)
    rig.hold(0.8)
    assert rig.swipes() == [dirn]
