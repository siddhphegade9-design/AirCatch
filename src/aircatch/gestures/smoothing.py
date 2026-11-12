"""Steadying the 21 hand points between camera frames.

MediaPipe's points jitter a little from frame to frame, which makes a pinch flicker and a swipe
speed wobble. A "One Euro" filter removes that jitter when the hand is still and steps out of the
way when it moves fast, so a quick swipe or throw is not slowed down or flattened.
Timestamps are used (not frame counts), so the result does not depend on the camera's frame rate.
"""
from __future__ import annotations

import math

from aircatch.gestures.types import HandObservation, Point


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class _OneEuro:
    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x: float | None = None
        self.dx = 0.0

    def __call__(self, value: float, dt: float) -> float:
        if self.x is None or dt <= 0:
            self.x, self.dx = value, 0.0
            return value
        d = (value - self.x) / dt
        self.dx += _alpha(self.d_cutoff, dt) * (d - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += _alpha(cutoff, dt) * (value - self.x)
        return self.x


class _HandFilter:
    def __init__(self, min_cutoff: float, beta: float) -> None:
        self.land = [[_OneEuro(min_cutoff, beta, 1.0) for _ in range(3)] for _ in range(21)]
        self.world = [[_OneEuro(min_cutoff, beta, 1.0) for _ in range(3)] for _ in range(21)]
        self.last_time = -1e9
        self.wrist = (0.0, 0.0)


class LandmarkSmoother:
    """Smooths each followed hand separately. Hands are matched frame to frame by wrist position."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 6.0, reset_after: float = 0.4,
                 match_distance: float = 0.25) -> None:
        self._min_cutoff, self._beta = min_cutoff, beta
        self._reset_after, self._match = reset_after, match_distance
        self._filters: list[_HandFilter] = []

    def reset(self) -> None:
        self._filters.clear()

    def apply(self, hands: list[HandObservation], now: float) -> list[HandObservation]:
        self._filters = [f for f in self._filters if now - f.last_time <= self._reset_after]
        free, out = list(self._filters), []
        for hand in hands:
            wrist = (hand.landmarks[0][0], hand.landmarks[0][1])
            best = min(free, key=lambda f: math.dist(f.wrist, wrist), default=None)
            if best is not None and math.dist(best.wrist, wrist) <= self._match:
                free.remove(best)
                filt = best
            else:
                filt = _HandFilter(self._min_cutoff, self._beta)
                self._filters.append(filt)
            dt = now - filt.last_time if filt.last_time > -1e8 else 0.0
            filt.last_time, filt.wrist = now, wrist
            out.append(self._smooth(hand, filt, dt))
        return out

    @staticmethod
    def _smooth(hand: HandObservation, filt: _HandFilter, dt: float) -> HandObservation:
        def run(points, filters) -> tuple[Point, ...]:
            return tuple(tuple(f(v, dt) for f, v in zip(fs, p)) for p, fs in zip(points, filters))
        return HandObservation(run(hand.landmarks, filt.land),
                               run(hand.world, filt.world) if hand.world is not None else None,
                               hand.handedness, hand.confidence, hand.aspect)
