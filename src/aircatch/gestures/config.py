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
    browse_hold: float = 0.7          # peace sign held this long opens / closes the explorer
    victory_curled_ratio: float = 1.15  # ring and pinky must be curled at least this much (a fist is about 0.8)
    victory_spread: float = 0.30      # index and middle fingertips this far apart (palm lengths) = a V, not two fingers together
    browse_block: float = 0.8         # quiet time after opening / closing
    # --- swipes (file explorer). Distances are in palm lengths, speeds in palm lengths per second.
    # Each stroke of the hand (speed up -> stop / turn back / leave the picture) is judged once, as a whole.
    swipe_speed: float = 3.0          # a sideways stroke must reach this speed at its fastest
    swipe_travel: float = 1.0         # ...and cover this distance, start to end
    swipe_speed_vertical: float = 2.6 # up / down strokes are smaller and slower (a wrist flick)
    swipe_travel_vertical: float = 0.8
    swipe_axis_share: float = 0.8     # how straight: share of the movement along its axis (0.8 = within about 37 degrees)
    swipe_axis_share_vertical: float = 0.8
    swipe_start_speed: float = 1.2    # the hand moving faster than this starts a stroke
    swipe_end_speed: float = 0.8      # ...slower than this ends it (and counts as resting)
    swipe_end_share: float = 0.35     # ...or slower than this share of the stroke's top speed
    swipe_resplit_share: float = 0.35 # a dip below this share of the top speed, then fast again the same way: two strokes
    swipe_return_instant: bool = True # the cooldown ends as soon as the hand has come back far enough (else: after a pause)
    swipe_side_protect: bool = True   # a command given too soon after a swipe is ignored, and so is its way back
    swipe_end_time: float = 0.08      # ...for this long
    swipe_core_time: float = 0.33     # the middle 80% of a swipe must take at most this long (moving the hand elsewhere is slower)
    swipe_max_time: float = 1.0       # a stroke longer than this is judged anyway (a slow wave is not a swipe)
    swipe_speed_window: float = 0.07  # speed is measured over this much time
    swipe_max_jump: float = 0.7       # no single camera frame may account for more than this share of a swipe (tracking glitch)
    swipe_gap: float = 0.30           # least time between two swipes, whatever the direction
    # after a swipe the hand comes back; that stroke is never a swipe. The opposite way works again once
    swipe_return_fraction: float = 0.5  # ...the hand is back at least this far towards where the swipe began, and rests
    swipe_rest_time: float = 0.15       #    for this long,
    swipe_return_settle: float = 0.35   # ...or it moved back at all and this long has passed since,
    swipe_recentre: float = 2.5         # ...or it rested this long anywhere (it stayed out: that is the new start),
    swipe_return_timeout: float = 3.0   # ...or this long has passed
    swipe_dropout: float = 0.15       # the hand missing this long (blur) is a dropout ...
    swipe_rejoin: float = 0.3         # ...a stroke is joined up again if the hand is back within this long
    swipe_rejoin_long: float = 0.8    # ...a moving hand that blurred out of sight and is back elsewhere within this long: that move is the swipe
    swipe_forget: float = 2.0         # hand gone this long: forget everything
    swipe_early: float = 1.2          # a stroke already this many times the needed distance counts at once, while still moving
    swipe_exit_reach: float = 1.5     # a stroke that vanishes within this many palms of the side/top edge it was heading for ...
    swipe_exit_travel: float = 0.4    # ...needs only this much distance: it swiped out of the picture
    swipe_exit_ease: float = 0.6      # a fast stroke that leaves the picture needs only this share of the distance
    # old settings, no longer used (kept so old settings files still load)
    swipe_right_ease: float = 1.0
    swipe_window: float = 0.3
    swipe_cross_limit: float = 0.5
    swipe_repeat: float = 0.45
    swipe_opposite_block: float = 0.7
    swipe_return_hard: float = 1.2
    swipe_return_window: float = 2.5
    swipe_return_ratio: float = 1.0
    swipe_neutral_radius: float = 1.5
    swipe_rest_speed: float = 1.8
    gesture_still_speed: float = 1.6  # pinch / peace sign only count while the hand moves slower than this (palm/s) ...
    gesture_still_time: float = 0.2   # ...and has done for this long (a moving hand is swiping)
    gesture_facing_min: float = 0.4   # ...and the palm faces the camera: knuckle width at least this share of palm length
    select_hold: float = 0.4         # pinch held still this long = select
    thumb_extend: float = 0.35        # thumb tip to index knuckle / palm length above this = thumb off the fist
    thumb_straight: float = 0.75      # thumb end-to-end / its bone lengths above this = thumb straight (not tucked)
    thumb_up_share: float = 0.4       # how much of the thumb's direction must point up (1 = straight up)
    thumb_min_curled: int = 3         # fingers (of 4) that must be curled for a thumbs up
    select_with_pinch: bool = True    # a pinch (thumb + index touching, other three fingers straight) selects
    select_with_thumbs_up: bool = False  # also let a thumbs up select (off by default)
    pinch_on: float = 0.50            # thumb-to-index gap / palm length below this = pinching
    pinch_off: float = 0.85           # above this = released (between the two keeps the old answer)
    pinch_straight_fingers: int = 2   # how many of middle, ring, pinky must be straight to START a pinch
    pinch_keep_fingers: int = 1       # ...and to keep it (the tracker often misreads one finger for a moment)
    select_drift: float = 0.5         # ...and the palm must stay within this many palm lengths of where the pinch began
    select_release: float = 0.5       # after a select the pinch must be let go this long before it can select again
    select_grace: float = 0.25        # the pinch may flicker off for this long without the hold starting again
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
                    "swipe_window", "pinch_on", "victory_spread", "victory_curled_ratio", "pinch_hold", "swipe_speed_vertical",
                    "swipe_travel_vertical", "select_hold", "thumb_extend", "thumb_straight",
                    "swipe_rest_speed", "swipe_rest_time", "swipe_return_timeout", "swipe_gap",
                    "swipe_neutral_radius", "swipe_recentre", "swipe_right_ease", "swipe_return_hard",
                    "swipe_return_window", "swipe_return_ratio", "swipe_cross_limit", "swipe_max_jump",
                    "swipe_start_speed", "swipe_end_speed", "swipe_end_time", "swipe_max_time",
                    "swipe_speed_window", "swipe_dropout", "swipe_rejoin", "swipe_forget",
                    "swipe_exit_ease", "swipe_return_fraction", "swipe_core_time", "swipe_end_share", "swipe_return_settle",
                    "gesture_still_speed", "gesture_still_time", "gesture_facing_min", "swipe_rejoin_long",
                    "swipe_early", "swipe_exit_reach", "swipe_exit_travel")
        for name in positive:
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be greater than 0")
        if self.cooldown < 0 or self.browse_block < 0 or self.swipe_repeat < 0 or self.swipe_opposite_block < 0:
            raise ValueError("cooldown and block times cannot be negative")
        if not 0 < self.pinch_on < self.pinch_off:
            raise ValueError("pinch_on must be above 0 and below pinch_off")
        if not 1 <= self.pinch_straight_fingers <= 3 or not 0 <= self.pinch_keep_fingers <= self.pinch_straight_fingers:
            raise ValueError("pinch_straight_fingers must be 1..3 and pinch_keep_fingers at most that")
        if self.select_grace < 0:
            raise ValueError("select_grace cannot be negative")
        if not 0.5 < self.swipe_axis_share <= 1 or not 0.5 < self.swipe_axis_share_vertical <= 1:
            raise ValueError("swipe_axis_share settings must be above 0.5 and at most 1")
        if self.swipe_end_speed >= self.swipe_start_speed:
            raise ValueError("swipe_end_speed must be below swipe_start_speed")
        if not 0 < self.swipe_exit_ease <= 1 or not 0 <= self.swipe_return_fraction <= 1:
            raise ValueError("swipe_exit_ease must be in (0, 1] and swipe_return_fraction in [0, 1]")
        if self.swipe_rejoin < self.swipe_dropout or self.swipe_forget < self.swipe_rejoin:
            raise ValueError("need swipe_dropout <= swipe_rejoin <= swipe_forget")
        if not 0 < self.thumb_up_share <= 1 or not 1 <= self.thumb_min_curled <= 4:
            raise ValueError("thumb_up_share must be in (0, 1] and thumb_min_curled between 1 and 4")
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
