"""Simulated hands, so the gesture engine can be tested without a camera or a person.

Builds 21 MediaPipe-style landmarks from a simple hand model, in any pose, position, size,
rotation (also turned towards the camera) and with optional jitter. Also runs scripted
'movies' (open for 0.5 s, fist for 0.3 s, flick ...) through an engine.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Sequence

from aircatch.gestures.engine import GestureEngine
from aircatch.gestures.types import GestureEvent, HandObservation

# hand model in palm-lengths: (forward, sideways). Wrist = origin, middle knuckle = (1, 0).
_OPEN = {
    "thumb": [(0.20, -0.30), (0.45, -0.55), (0.70, -0.75), (0.90, -0.90)],
    "index": [(0.95, -0.30), (1.35, -0.32), (1.60, -0.33), (1.82, -0.34)],
    "middle": [(1.00, 0.00), (1.45, 0.00), (1.72, 0.00), (1.95, 0.00)],
    "ring": [(0.95, 0.22), (1.37, 0.24), (1.63, 0.25), (1.85, 0.26)],
    "pinky": [(0.88, 0.43), (1.22, 0.46), (1.42, 0.47), (1.60, 0.48)],
}
_CURLED = {
    "thumb": [(0.20, -0.30), (0.40, -0.45), (0.60, -0.30), (0.78, -0.10)],
    "index": [(0.95, -0.30), (1.15, -0.30), (0.95, -0.28), (0.72, -0.26)],
    "middle": [(1.00, 0.00), (1.20, 0.00), (1.00, 0.00), (0.74, 0.00)],
    "ring": [(0.95, 0.22), (1.15, 0.23), (0.97, 0.24), (0.72, 0.25)],
    "pinky": [(0.88, 0.43), (1.05, 0.44), (0.90, 0.45), (0.68, 0.46)],
}
_ORDER = ("thumb", "index", "middle", "ring", "pinky")
_POSES = {  # which of (index, middle, ring, pinky) are stretched
    "open": (1, 1, 1, 1), "fist": (0, 0, 0, 0), "point": (1, 0, 0, 0),
    "victory": (1, 1, 0, 0), "three": (1, 1, 1, 0), "rock": (1, 0, 0, 1),
    "pinch": (0, 1, 1, 1),     # index bent down to meet the thumb, other three straight
}
# thumb reaching round to touch the bent index fingertip (see "pinch")
_PINCH_THUMB = [(0.20, -0.30), (0.50, -0.50), (0.85, -0.52), (1.20, -0.36)]


def _mix(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def make_hand(pose: str = "open", *, center: tuple[float, float] = (0.5, 0.5),
              palm_size: float = 0.2, rotation: float = 0.0, tilt: float = 0.0, pitch: float = 0.0,
              aspect: float = 16 / 9, noise: float = 0.0, rng: random.Random | None = None,
              confidence: float = 1.0, curl: Sequence[float] | None = None,
              with_world: bool = True) -> HandObservation:
    """A simulated hand.

    pose:     'open', 'fist', 'point', 'victory', 'three', 'rock'
    center:   palm centre in the image (0..1, 0..1)
    palm_size: wrist-to-knuckle length as a fraction of the image height
    rotation: turning in the image plane, radians (0 = fingers up)
    tilt, pitch: turning the hand towards/away from the camera, radians
    curl:     optional per-finger (index..pinky) curl 0 (stretched) .. 1 (curled), overrides pose
    noise:    random jitter, as a fraction of the palm size
    """
    if curl is None:
        flags = _POSES[pose]
        curl = [0.0 if f else 1.0 for f in flags]
        if pose == "pinch":
            curl[0] = 0.55
    rng = rng or random.Random(0)
    local: list[tuple[float, float]] = []
    for name in _ORDER:
        if name == "thumb":
            t = 0.0 if pose in ("open", "victory", "rock", "three") else 1.0
        else:
            t = curl[_ORDER.index(name) - 1]
        pts = [_mix(o, c, t) for o, c in zip(_OPEN[name], _CURLED[name])]
        if name == "thumb" and pose == "pinch":
            pts = list(_PINCH_THUMB)
        local.extend(pts)
    local.insert(0, (0.0, 0.0))                                   # wrist
    # shift so the palm centre (wrist + 4 knuckles) sits at the origin
    palm_idx = (0, 5, 9, 13, 17)
    mu = sum(local[i][0] for i in palm_idx) / 5
    mv = sum(local[i][1] for i in palm_idx) / 5
    pts3 = [(u - mu, v - mv, 0.0) for u, v in local]

    def rot(p, axis, ang):
        x, y, z = p
        c, s = math.cos(ang), math.sin(ang)
        if axis == "x":
            return (x, y * c - z * s, y * s + z * c)
        if axis == "y":
            return (x * c + z * s, y, -x * s + z * c)
        return (x * c - y * s, x * s + y * c, z)

    # model axes: x = forward (u), y = sideways (v), z = towards camera
    pts3 = [rot(rot(p, "y", tilt), "x", pitch) for p in pts3]
    landmarks, world = [], []
    for u, v, w in pts3:
        u += rng.gauss(0, noise)
        v += rng.gauss(0, noise)
        # fingers up at rotation 0: forward -> image up (-y), sideways -> image right (+x)
        dx = v * math.cos(rotation) + u * math.sin(rotation)
        dy = -u * math.cos(rotation) + v * math.sin(rotation)
        landmarks.append((center[0] + dx * palm_size / aspect, center[1] + dy * palm_size,
                          w * palm_size / aspect))
        world.append((dx * 0.09, dy * 0.09, w * 0.09))
    return HandObservation(tuple(landmarks), tuple(world) if with_world else None,
                           "Right", confidence, aspect)


@dataclass
class Seg:
    """One part of a scripted movie."""
    duration: float
    pose: str = "open"
    start: tuple[float, float] = (0.5, 0.5)
    end: tuple[float, float] | None = None       # moves linearly from start to end
    palm_size: float = 0.2
    missing: bool = False                        # no hand visible
    extra: Callable[[float], list[HandObservation]] | None = None   # extra hands


def render(segments: Sequence[Seg], fps: float = 30.0, t0: float = 100.0, *, noise: float = 0.0,
           seed: int = 1) -> list[tuple[float, list[HandObservation]]]:
    """Turn segments into (timestamp, hands) frames."""
    rng = random.Random(seed)
    frames, t = [], t0
    dt = 1.0 / fps
    for seg in segments:
        n = max(1, round(seg.duration * fps))
        end = seg.end or seg.start
        for i in range(n):
            f = (i + 1) / n
            c = (seg.start[0] + (end[0] - seg.start[0]) * f, seg.start[1] + (end[1] - seg.start[1]) * f)
            hands: list[HandObservation] = []
            if not seg.missing:
                hands.append(make_hand(seg.pose, center=c, palm_size=seg.palm_size, noise=noise, rng=rng))
            if seg.extra is not None:
                hands.extend(seg.extra(t - t0))
            t += dt
            frames.append((t, hands))
    return frames


def run(engine: GestureEngine, segments: Sequence[Seg], fps: float = 30.0, *, noise: float = 0.0,
        seed: int = 1, t0: float = 100.0) -> list[GestureEvent]:
    """Play a scripted movie through the engine and return every event that came out."""
    events: list[GestureEvent] = []
    for t, hands in render(segments, fps, t0, noise=noise, seed=seed):
        events.extend(engine.update(hands, t))
    return events


def kinds(events: Sequence[GestureEvent]) -> list[str]:
    return [e.kind for e in events]
