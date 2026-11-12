"""Telling an open palm from a fist, and measuring where the hand is."""
from __future__ import annotations

import math
from dataclasses import dataclass

from aircatch.gestures.config import GestureConfig
from aircatch.gestures.types import (FINGERS, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, PALM_POINTS,
                                     PINKY_MCP, THUMB_TIP, WRIST, HandObservation, Point, Pose)


def _dist(a: Point, b: Point) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _shape_points(hand: HandObservation) -> list[Point]:
    """Points used to judge the hand shape. Prefer metric world points (turning-proof);
    otherwise use image points in consistent units (x and z scaled by the aspect ratio)."""
    if hand.world is not None:
        return list(hand.world)
    a = hand.aspect
    return [(x * a, y, z * a) for x, y, z in hand.landmarks]


def palm_geometry(hand: HandObservation) -> tuple[float, float, float]:
    """(centre x, centre y, hand scale) in image-height units, from the image landmarks.

    The scale is the palm length, but a hand turned edge-on (or pointing at the camera) squashes
    one of the palm's two directions. So the scale is the larger of the palm length and the
    knuckle-row width (scaled to palm-length units): it stays stable however the hand is turned,
    which keeps swipe speeds and the 'hand too small' test honest."""
    a = hand.aspect
    pts = [hand.landmarks[i] for i in PALM_POINTS]
    cx = sum(p[0] for p in pts) / len(pts) * a
    cy = sum(p[1] for p in pts) / len(pts)
    w, m = hand.landmarks[WRIST], hand.landmarks[MIDDLE_MCP]
    length = math.hypot((w[0] - m[0]) * a, w[1] - m[1])
    i, k = hand.landmarks[INDEX_MCP], hand.landmarks[PINKY_MCP]
    width = math.hypot((i[0] - k[0]) * a, i[1] - k[1])
    return cx, cy, max(length, 1.35 * width)


@dataclass(frozen=True)
class PoseResult:
    pose: Pose
    fingers: tuple[bool, bool, bool, bool]   # stretched? index, middle, ring, pinky
    ratios: tuple[float, float, float, float]
    thumb_out: bool
    pinch: bool = False       # thumb tip touching index tip, middle/ring/pinky straight
    victory: bool = False     # index and middle stretched, ring and pinky curled (peace sign)


class PoseClassifier:
    """Decides the pose of one hand in one frame.

    A finger counts as stretched when its tip is much farther from the wrist than its knuckle,
    and as curled when the tip is close to the palm. Between the two limits (hysteresis) the finger
    keeps its previous answer, so a hand hovering at the borderline does not flicker.
    """

    def __init__(self, config: GestureConfig) -> None:
        self._cfg = config
        self._state: dict[str, bool] = {}
        self._pinching = False

    def reset(self) -> None:
        self._state.clear()
        self._pinching = False

    def classify(self, hand: HandObservation) -> PoseResult:
        pts = _shape_points(hand)
        wrist = pts[WRIST]
        ratios, flags = [], []
        for name, knuckle, tip in FINGERS:
            base = _dist(pts[knuckle], wrist)
            ratio = _dist(pts[tip], wrist) / base if base > 1e-9 else 0.0
            if ratio >= self._cfg.extended_ratio:
                self._state[name] = True
            elif ratio <= self._cfg.curled_ratio:
                self._state[name] = False
            elif name not in self._state:                 # first look: decide by the midpoint
                self._state[name] = ratio >= (self._cfg.extended_ratio + self._cfg.curled_ratio) / 2
            ratios.append(ratio)
            flags.append(self._state[name])
        stretched = sum(flags)
        curled = 4 - stretched
        if stretched >= self._cfg.open_min_fingers:
            pose = Pose.OPEN
        elif curled >= self._cfg.fist_min_curled:
            pose = Pose.FIST
        else:
            pose = Pose.OTHER
        palm = _dist(pts[MIDDLE_MCP], wrist)
        thumb_out = palm > 1e-9 and _dist(pts[THUMB_TIP], pts[PINKY_MCP]) / palm > 0.9
        # pinch: measured in 3D (turning-proof), as a fraction of the palm length, with hysteresis
        pinch_gap = _dist(pts[THUMB_TIP], pts[INDEX_TIP]) / palm if palm > 1e-9 else 9.0
        straight = sum(flags[1:])                       # middle, ring, pinky
        if straight >= self._cfg.pinch_straight_fingers:
            if pinch_gap <= self._cfg.pinch_on:
                self._pinching = True
            elif pinch_gap >= self._cfg.pinch_off:
                self._pinching = False
        else:
            self._pinching = False
        victory = flags[0] and flags[1] and not flags[2] and not flags[3]
        return PoseResult(pose, tuple(flags), tuple(ratios), thumb_out, self._pinching, victory)
