"""Tunable settings for gesture recognition. Everything has a safe default."""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class GestureConfig:
    # --- telling the hand shapes apart ---
    extended_ratio: float = 1.55      # fingertip-to-wrist / knuckle-to-wrist above this = finger stretched
    curled_ratio: float = 1.35        # below this = finger curled (between the two keeps the old answer)
    open_min_fingers: int = 4         # fingers that must be stretched for an open palm (1-4)
    fist_min_curled: int = 4          # fingers that must be curled for a fist (1-4)
    # --- which hand to follow ---
    min_hand_confidence: float = 0.5
    min_palm_size: float = 0.05       # smaller hands (far away, other people) are ignored; image-height units
    # --- smoothing and timing, all in seconds (so frame rate does not matter) ---
    pose_window: float = 0.12         # a pose must win a vote over this window
    pose_majority: float = 0.6
    velocity_window: float = 0.12
    arm_time: float = 0.15            # open palm held this long = ready
    grab_time: float = 0.12           # fist held this long = grab / catch
    catch_hold: float = 0.35          # no longer used (receive is now fist -> open); kept so old settings files load
    open_confirm: float = 0.06        # open hand held this long after a fist = release
    ready_timeout: float = 1.0        # ready is lost if the palm is not open for this long
    hold_timeout: float = 20.0        # holding a file longer than this drops it
    lost_grace: float = 0.5           # a hand may vanish this long (blur, glitch) without cancelling
    cooldown: float = 1.5             # quiet time after a throw or catch
    # --- browsing the file explorer (peace sign opens/closes, swipes move, pinch selects) ---
    browse_hold: float = 0.5          # peace sign held this long opens / closes the explorer
    browse_block: float = 0.8         # quiet time after opening / closing
    swipe_speed: float = 3.0          # palm-lengths per second a swipe must reach
    swipe_travel: float = 1.0         # palm-lengths the hand must cover
    swipe_window: float = 0.3         # the travel must happen within this time
    swipe_axis_share: float = 0.75    # how straight the swipe must be (share along its main axis)
    swipe_repeat: float = 0.45        # wait before another swipe the same way
    swipe_opposite_block: float = 1.0 # wait before a swipe the opposite way (the hand coming back)
    pinch_on: float = 0.45            # thumb-to-index gap / palm length below this = pinching
    pinch_off: float = 0.70           # above this = released (between the two keeps the old answer)
    pinch_straight_fingers: int = 3   # middle, ring, pinky that must stay straight during a pinch
    pinch_hold: float = 0.3           # pinch held this long = select
    # --- the throw ---
    throw_window: float = 0.4         # look this far back from the release for the fastest movement
    throw_speed: float = 6.0          # palm-lengths per second needed to count as a throw
    require_motion: bool = True       # False: any release after a grab counts (simplest gesture)
    # --- camera ---
    smooth_landmarks: bool = True     # steady the 21 hand points (less jitter, same speed)

    def validate(self) -> None:
        positive = ("min_palm_size", "pose_window", "velocity_window", "arm_time", "grab_time",
                    "catch_hold", "open_confirm", "ready_timeout", "hold_timeout", "lost_grace",
                    "throw_window", "throw_speed", "browse_hold", "swipe_speed", "swipe_travel",
                    "swipe_window", "pinch_on", "pinch_hold")
        for name in positive:
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.cooldown < 0 or self.browse_block < 0 or self.swipe_repeat < 0 or self.swipe_opposite_block < 0:
            raise ValueError("cooldown and block times cannot be negative")
        if not 0 < self.pinch_on < self.pinch_off:
            raise ValueError("pinch_on must be above 0 and below pinch_off")
        if not 1 <= self.pinch_straight_fingers <= 3:
            raise ValueError("pinch_straight_fingers must be between 1 and 3")
        if not 0.5 < self.swipe_axis_share <= 1:
            raise ValueError("swipe_axis_share must be above 0.5 and at most 1")
        if not 0 < self.curled_ratio < self.extended_ratio:
            raise ValueError("curled_ratio must be above 0 and below extended_ratio")
        if not 1 <= self.open_min_fingers <= 4 or not 1 <= self.fist_min_curled <= 4:
            raise ValueError("open_min_fingers and fist_min_curled must be between 1 and 4")
        if not 0.5 < self.pose_majority <= 1:
            raise ValueError("pose_majority must be above 0.5 and at most 1")
        if not 0 <= self.min_hand_confidence <= 1:
            raise ValueError("min_hand_confidence must be between 0 and 1")

    # --- saving and loading ---
    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "GestureConfig":
        known = {f.name: f for f in fields(GestureConfig)}
        unknown = sorted(set(data) - set(known))
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(unknown)}")
        config = GestureConfig()
        for key, value in data.items():
            expected = type(getattr(config, key))
            if expected is bool:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
            setattr(config, key, expected(value))
        config.validate()
        return config

    @staticmethod
    def load(path: str | Path) -> "GestureConfig":
        try:
            data = json.loads(Path(path).read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read gesture settings from {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("gesture settings file must contain a JSON object")
        return GestureConfig.from_dict(data)

    def save(self, path: str | Path) -> None:
        self.validate()
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def suggest_throw_speed(samples: list[float]) -> float:
    """From the speeds of a few deliberate throws, pick a threshold that those throws clear easily
    but that ordinary hand movements do not reach (half the typical throw, kept in a sane range)."""
    if len(samples) < 3:
        raise ValueError("need at least 3 throws to calibrate")
    return round(min(10.0, max(1.5, 0.5 * statistics.median(samples))), 2)
