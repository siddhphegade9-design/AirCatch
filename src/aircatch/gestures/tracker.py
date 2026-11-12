"""Finding hands in camera pictures (MediaPipe). The only file that knows about MediaPipe."""
from __future__ import annotations

from dataclasses import dataclass

from aircatch.gestures.types import HandObservation


class TrackerError(RuntimeError):
    """The hand tracker could not start. The message says what to do about it."""


@dataclass
class TrackerConfig:
    max_hands: int = 2
    model_complexity: int = 1              # 0 = fastest, 1 = more accurate (better with turned hands)
    min_detection_confidence: float = 0.6
    min_tracking_confidence: float = 0.5


class HandTracker:
    """Turns a camera picture (BGR, as OpenCV delivers it) into a list of HandObservation."""

    def __init__(self, config: TrackerConfig | None = None) -> None:
        cfg = config or TrackerConfig()
        try:
            import mediapipe as mp
            import numpy as np
        except ImportError as exc:
            raise TrackerError("MediaPipe is not installed. Run:  pip install -r requirements.txt") from exc
        if not hasattr(mp, "solutions"):
            raise TrackerError("This MediaPipe version has no hand 'solutions'. "
                               "Run:  pip install mediapipe==0.10.14")
        self._np = np
        try:
            self._hands = mp.solutions.hands.Hands(
                static_image_mode=False, max_num_hands=cfg.max_hands,
                model_complexity=cfg.model_complexity,
                min_detection_confidence=cfg.min_detection_confidence,
                min_tracking_confidence=cfg.min_tracking_confidence)
        except Exception as exc:
            raise TrackerError(f"MediaPipe could not start: {exc}") from exc

    def process(self, frame_bgr) -> list[HandObservation]:
        np = self._np
        height, width = frame_bgr.shape[:2]
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        rgb.flags.writeable = False
        result = self._hands.process(rgb)
        if not result.multi_hand_landmarks:
            return []
        worlds = result.multi_hand_world_landmarks or [None] * len(result.multi_hand_landmarks)
        labels = result.multi_handedness or [None] * len(result.multi_hand_landmarks)
        hands = []
        for lm, world, label in zip(result.multi_hand_landmarks, worlds, labels):
            category = label.classification[0] if label is not None else None
            hands.append(HandObservation(
                landmarks=tuple((p.x, p.y, p.z) for p in lm.landmark),
                world=tuple((p.x, p.y, p.z) for p in world.landmark) if world is not None else None,
                handedness=category.label if category else "",
                confidence=float(category.score) if category else 1.0,
                aspect=width / height))
        return hands

    def close(self) -> None:
        self._hands.close()

    def __enter__(self) -> "HandTracker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def assess_lighting(frame_bgr) -> str:
    """'dark', 'bright' or 'ok': a cheap check, so the screen can say 'turn on a light'."""
    mean = float(frame_bgr[::8, ::8].mean())
    if mean < 45:
        return "dark"
    if mean > 215:
        return "bright"
    return "ok"
