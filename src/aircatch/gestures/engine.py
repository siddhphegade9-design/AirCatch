"""The gesture state machine.

Feed it what the tracker saw in each frame (`update`); it returns the gesture events that happened.
It knows nothing about cameras, windows or the network, so the tracker, the screen and the
transfer code can each be replaced without touching it.

SEND mode      open palm (held) -> fist (held) = GRAB -> hand opens with a fast flick = THROW
RECEIVE mode   fist (held) -> hand opens = CATCH (close your fist, then release it)
IDLE mode      tracking and feedback only; no gesture events
BROWSE mode    (file explorer open) quick swipes up/down/left/right move around; a pinch (thumb + index touching, the other three fingers
               straight) selects. The peace sign (index +
               middle finger up) opens the explorer from send mode and closes it again.

Why this does not misfire:
  * a pose must win a vote over a short time window before it counts (no single-frame glitches);
  * a grab only starts from a held open palm, and a catch only starts from a fist that was formed after
    the hand was seen open (a hand that merely appears as a fist does nothing);
  * after a throw or catch there is a cooldown, and the full open-fist-open cycle must be repeated,
    so holding a gesture on screen can never trigger twice;
  * if the hand disappears for longer than a short grace period, the armed gesture is cancelled.
All timing uses timestamps, so results do not depend on the camera's frame rate.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Sequence

from aircatch.gestures.config import GestureConfig
from aircatch.gestures.pose import PoseClassifier, PoseResult, hand_centroid, palm_geometry
from aircatch.gestures.swipe import SwipeDetector
from aircatch.gestures.types import (INDEX_MCP, MIDDLE_MCP, MODES, PINKY_MCP, WRIST, EngineStatus,
                                     EventKind, GestureEvent, HandObservation, Pose)

NO_HAND, IDLE, READY, HOLDING, CATCH_ARMED, COOLDOWN = (
    "no_hand", "idle", "ready", "holding", "catch_armed", "cooldown")

_HINTS = {
    (NO_HAND, "send"): "Show your hand to the camera",
    (NO_HAND, "receive"): "Show your hand to the camera",
    (IDLE, "send"): "Open your palm (or show a peace sign to browse files)",
    (IDLE, "receive"): "Close your fist, then open it to receive",
    (READY, "send"): "Make a fist to grab the file",
    (HOLDING, "send"): "Flick your hand and open it to throw",
    (CATCH_ARMED, "receive"): "Now open your hand to receive",
    (NO_HAND, "browse"): "Show your hand to the camera",
    (IDLE, "browse"): "Swipe to move, pinch to select, peace sign to close",
}


def _direction_name(ux: float, uy: float) -> str:
    if abs(ux) >= abs(uy):
        return "right" if ux > 0 else "left"
    return "down" if uy > 0 else "up"


class GestureEngine:
    def __init__(self, config: GestureConfig | None = None, mode: str = "idle") -> None:
        self.cfg = config or GestureConfig()
        self.cfg.validate()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._mode = mode
        self._return_mode = "send"          # where the peace sign takes you back to
        self._classifier = PoseClassifier(self.cfg)
        self._swiper = SwipeDetector(self.cfg)   # survives a hand briefly lost (it must remember the return)
        self._full_reset()

    # ------------------------------------------------------------------ setup
    def _full_reset(self) -> None:
        self._state = NO_HAND
        self._hand_visible = False
        self._lost_since: float | None = None
        self._last_center: tuple[float, float] | None = None
        self._last_size = 0.0
        self._last_seen = -1e9
        self._pose_hist: deque[tuple[float, Pose]] = deque()
        self._smoothed = Pose.NONE
        self._pose_since = 0.0
        self._seen_non_fist = False      # receive: the fist must be formed AFTER the hand was seen open
        self._motion: deque[tuple[float, float, float, float]] = deque()    # t, x, y, palm size
        self._speeds: deque[tuple[float, float, float, float]] = deque()    # t, speed, ux, uy
        self._speed = 0.0
        self._ready_deadline = 0.0
        self._hold_start = 0.0
        self._cooldown_until = 0.0
        self._hand: HandObservation | None = None
        self._result: PoseResult | None = None
        self._cooldown_total = 0.0
        self._classifier.reset()
        # browsing
        self._flag_hist: deque[tuple[float, bool, bool, bool]] = deque()   # t, pinch, victory, thumbs up
        self._pinch_now = self._victory_now = self._thumb_now = False
        self._pinch_since = self._victory_since = self._thumb_since = 0.0
        self._select_armed = False       # a select must be released before the next one counts
        self._moved_at = -1e9            # when the hand last moved (pinch / peace sign need it still)
        self._victory_armed = False      # the peace sign must be dropped before the next toggle
        self._toggle_until = 0.0

    def _hand_gone(self) -> None:
        keep_cooldown = self._state == COOLDOWN
        until, total = self._cooldown_until, self._cooldown_total
        self._full_reset()
        if keep_cooldown:
            self._state, self._cooldown_until, self._cooldown_total = COOLDOWN, until, total

    @property
    def swipe_note(self) -> str:
        """Why the last swipe attempt was (not) accepted, for tuning."""
        return self._swiper.note

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch between send / receive / idle. Any half-finished gesture is forgotten."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode == self._mode:
            return
        self._switch(mode)

    def _switch(self, mode: str) -> None:
        self._mode = mode
        self._cooldown_until = 0.0
        self._state = IDLE if self._hand_visible else NO_HAND
        # a hand that is already a fist when the mode changes must open before it can catch
        self._seen_non_fist = self._smoothed not in (Pose.FIST, Pose.NONE)
        self._swiper.reset()
        self._select_armed = False       # a select held across the switch does not count

    # ------------------------------------------------------------------ main entry point
    def update(self, observations: Sequence[HandObservation], now: float) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        if self._state == COOLDOWN and now >= self._cooldown_until:
            self._state = IDLE if self._hand_visible else NO_HAND

        hand = self._select_hand(observations, now)
        if hand is None:
            self._hand = None
            self._result = None
            self._speed = 0.0
            if self._mode == "browse":
                self._emit_swipe(self._swiper.lost(now), events)
            if self._hand_visible:
                if self._lost_since is None:
                    self._lost_since = now
                if now - self._lost_since >= self.cfg.lost_grace:
                    events.append(GestureEvent("hand_lost", now))
                    if self._state in (READY, HOLDING, CATCH_ARMED):
                        events.append(GestureEvent("cancelled", now, detail="hand_lost"))
                    self._hand_gone()
            return events

        self._lost_since = None
        if not self._hand_visible:
            self._hand_visible = True
            events.append(GestureEvent("hand_found", now))
            if self._state == NO_HAND:
                self._state = IDLE

        self._hand = hand
        self._result = self._classifier.classify(hand)
        cx, cy, size = palm_geometry(hand)
        self._last_center, self._last_size, self._last_seen = (cx, cy), size, now
        self._update_pose(self._result.pose, now)
        self._update_motion(cx, cy, size, now)
        steady = self._steady(hand, now)
        r = self._result
        self._update_flags(r.pinch and steady, r.victory and steady, r.thumbs_up and steady, now)
        if self._smoothed not in (Pose.FIST, Pose.NONE):
            self._seen_non_fist = True
        self._step(now, events)
        return events

    def _steady(self, hand: HandObservation, now: float) -> bool:
        """Pinch and peace sign only count from a hand held still with the palm towards the camera.
        A hand that is moving (a swipe) or turned edge-on has its fingers overlapping in the picture,
        and MediaPipe then often 'sees' a pinch or a peace sign that is not there."""
        cfg = self.cfg
        if self._speed > cfg.gesture_still_speed:          # palm centre: curling fingers do not move it
            self._moved_at = now
        if now - self._moved_at < cfg.gesture_still_time:
            return False
        a, lm = hand.aspect, hand.landmarks
        w, m, i, k = lm[WRIST], lm[MIDDLE_MCP], lm[INDEX_MCP], lm[PINKY_MCP]
        length = math.hypot((w[0] - m[0]) * a, w[1] - m[1])
        width = math.hypot((i[0] - k[0]) * a, i[1] - k[1])
        return width >= cfg.gesture_facing_min * max(length, 1e-6)

    # ------------------------------------------------------------------ hand selection
    def _select_hand(self, observations: Sequence[HandObservation], now: float) -> HandObservation | None:
        candidates = []
        for hand in observations:
            if hand.confidence < self.cfg.min_hand_confidence:
                continue
            cx, cy, size = palm_geometry(hand)
            if size >= self.cfg.min_palm_size:
                candidates.append((hand, cx, cy, size))
        if not candidates:
            return None
        if self._last_center is not None and now - self._last_seen < 1.0:
            px, py = self._last_center
            best = min(candidates, key=lambda c: math.hypot(c[1] - px, c[2] - py))
            if math.hypot(best[1] - px, best[2] - py) < 2.5 * max(self._last_size, 0.05):
                return best[0]                       # keep following the same hand
        return max(candidates, key=lambda c: c[3])[0]    # otherwise the closest (largest) hand

    # ------------------------------------------------------------------ smoothing
    def _update_pose(self, raw: Pose, now: float) -> None:
        hist = self._pose_hist
        hist.append((now, raw))
        while hist and now - hist[0][0] > self.cfg.pose_window:
            hist.popleft()
        counts: dict[Pose, int] = {}
        for _, pose in hist:
            counts[pose] = counts.get(pose, 0) + 1
        top = max(counts, key=lambda p: counts[p])
        if self._smoothed == Pose.NONE:
            self._smoothed, self._pose_since = top, now
        elif top != self._smoothed and counts[top] / len(hist) >= self.cfg.pose_majority:
            self._smoothed, self._pose_since = top, now

    def _update_flags(self, pinch: bool, victory: bool, thumb: bool, now: float) -> None:
        """Vote over the same short window as the pose, so a single glitchy frame changes nothing."""
        hist = self._flag_hist
        hist.append((now, pinch, victory, thumb))
        while hist and now - hist[0][0] > self.cfg.pose_window:
            hist.popleft()
        n = len(hist)
        pinch_now = sum(1 for _, p, _v, _t in hist if p) / n >= self.cfg.pose_majority
        victory_now = sum(1 for _, _p, v, _t in hist if v) / n >= self.cfg.pose_majority
        thumb_now = sum(1 for *_, t in hist if t) / n >= self.cfg.pose_majority
        if thumb_now != self._thumb_now:
            self._thumb_now, self._thumb_since = thumb_now, now
        if pinch_now != self._pinch_now:
            self._pinch_now, self._pinch_since = pinch_now, now
        if victory_now != self._victory_now:
            self._victory_now, self._victory_since = victory_now, now

    def _update_motion(self, cx: float, cy: float, size: float, now: float) -> None:
        m = self._motion
        m.append((now, cx, cy, size))
        while len(m) > 2 and now - m[0][0] > self.cfg.velocity_window:
            m.popleft()
        t0, x0, y0, _ = m[0]
        dt = now - t0
        if len(m) < 2 or dt < 1e-3:
            self._speed = 0.0
            ux = uy = 0.0
        else:
            vx, vy = (cx - x0) / dt, (cy - y0) / dt
            mean_size = sum(s for *_, s in m) / len(m)
            mag = math.hypot(vx, vy)
            self._speed = mag / mean_size if mean_size > 1e-9 else 0.0
            ux, uy = (vx / mag, vy / mag) if mag > 1e-9 else (0.0, 0.0)
        self._speeds.append((now, self._speed, ux, uy))
        horizon = max(2 * self.cfg.throw_window, 2.0)
        while self._speeds and now - self._speeds[0][0] > horizon:
            self._speeds.popleft()

    # ------------------------------------------------------------------ the state machine
    def _step(self, now: float, events: list[GestureEvent]) -> None:
        if self._mode == "idle" or self._state in (NO_HAND, COOLDOWN):
            return
        cfg, pose = self.cfg, self._smoothed
        stable = now - self._pose_since
        open_now = pose == Pose.OPEN

        if self._mode in ("send", "browse") and self._browse_toggle(now, events):
            return
        if self._mode == "browse":
            self._browse_step(now, events)
        elif self._mode == "send":
            if self._state == IDLE:
                if open_now and stable >= cfg.arm_time:
                    self._state, self._ready_deadline = READY, now + cfg.ready_timeout
                    events.append(GestureEvent("ready", now, detail="send"))
            elif self._state == READY:
                if open_now:
                    self._ready_deadline = now + cfg.ready_timeout
                elif pose == Pose.FIST and stable >= cfg.grab_time:
                    self._state, self._hold_start = HOLDING, now
                    events.append(GestureEvent("grab", now))
                elif now > self._ready_deadline:
                    self._state = IDLE
            elif self._state == HOLDING:
                if open_now and stable >= cfg.open_confirm:
                    self._release(now, events)
                elif now - self._hold_start > cfg.hold_timeout:
                    self._state = IDLE
                    events.append(GestureEvent("drop", now, detail="timeout"))
        else:  # receive
            if self._state == IDLE:                      # 1. close the fist
                if pose == Pose.FIST and stable >= cfg.grab_time and self._seen_non_fist:
                    self._state, self._hold_start = CATCH_ARMED, now
                    events.append(GestureEvent("ready", now, detail="catch"))
            elif self._state == CATCH_ARMED:             # 2. open it again = catch
                if open_now and stable >= cfg.open_confirm:
                    events.append(GestureEvent("catch", now))
                    self._start_cooldown(now)
                elif now - self._hold_start > cfg.hold_timeout:
                    self._state = IDLE
                    events.append(GestureEvent("cancelled", now, detail="timeout"))

    # ------------------------------------------------------------------ browsing
    def _browse_toggle(self, now: float, events: list[GestureEvent]) -> bool:
        """Peace sign held: open the explorer (from send mode) or close it again."""
        if not self._victory_now:
            self._victory_armed = True
            return False
        if (not self._victory_armed or now < self._toggle_until
                or now - self._victory_since < self.cfg.browse_hold):
            return False
        if self._mode == "send":
            if self._state not in (IDLE, READY):          # never while a file is in hand
                return False
            self._return_mode = "send"
            self._switch("browse")
            events.append(GestureEvent("browse_open", now))
        else:
            self._switch(self._return_mode)
            events.append(GestureEvent("browse_close", now))
        self._victory_armed = False
        self._toggle_until = now + self.cfg.browse_block
        return True

    def _selecting(self) -> tuple[bool, float]:
        """Is a select gesture (pinch, and a thumbs up if enabled) shown, and since when?"""
        if self.cfg.select_with_pinch and self._pinch_now:
            return True, self._pinch_since
        if self.cfg.select_with_thumbs_up and self._thumb_now:
            return True, self._thumb_since
        return False, 0.0

    def _select_forming(self) -> bool:
        raw, cfg = self._result, self.cfg
        if self._last_seen - self._moved_at < cfg.gesture_still_time:
            return False                 # a hand on the move is swiping, whatever shape it seems to have
        return raw is not None and ((cfg.select_with_pinch and raw.pinch)
                                    or (cfg.select_with_thumbs_up and raw.thumbs_up))

    def _browse_step(self, now: float, events: list[GestureEvent]) -> None:
        cfg = self.cfg
        selecting, since = self._selecting()
        if selecting:                                      # a select: never also a swipe
            self._swiper.abort()
            if self._select_armed and now - since >= cfg.select_hold:
                events.append(GestureEvent("select", now))
                self._select_armed = False
            return
        self._select_armed = True
        if self._select_forming() or self._victory_now:    # another gesture is forming: not a swipe
            self._swiper.abort()
            return
        hx, hy = hand_centroid(self._hand)
        self._swiper.aspect = self._hand.aspect
        self._emit_swipe(self._swiper.update(hx, hy, self._last_size, now), events)

    def _emit_swipe(self, swipe, events: list[GestureEvent]) -> None:
        if swipe is not None:
            events.append(GestureEvent("swipe", swipe.time, speed=swipe.speed, direction=swipe.unit,
                                       detail=swipe.direction))

    def _release(self, now: float, events: list[GestureEvent]) -> None:
        """The hand opened while holding: was it a throw or a gentle drop?"""
        start = self._pose_since - self.cfg.throw_window
        window = [s for s in self._speeds if start <= s[0] <= now]
        peak = max(window, key=lambda s: s[1]) if window else (now, 0.0, 0.0, 0.0)
        _, speed, ux, uy = peak
        if speed >= self.cfg.throw_speed or not self.cfg.require_motion:
            events.append(GestureEvent("throw", now, speed=speed, direction=(ux, uy),
                                       detail=_direction_name(ux, uy)))
            self._start_cooldown(now)
        else:
            events.append(GestureEvent("drop", now, speed=speed, detail="released"))
            self._state = IDLE

    def _start_cooldown(self, now: float) -> None:
        self._state = COOLDOWN
        self._cooldown_total = self.cfg.cooldown
        self._cooldown_until = now + self.cfg.cooldown

    # ------------------------------------------------------------------ feedback for screens
    @property
    def status(self) -> EngineStatus:
        cfg, now_pose = self.cfg, self._smoothed
        result = self._result
        stable = self._last_seen - self._pose_since if self._hand is not None else 0.0
        progress, left = 0.0, 0.0
        if self._state == COOLDOWN:
            left = max(0.0, self._cooldown_until - self._last_seen) if self._last_seen > 0 else self.cfg.cooldown
            progress = 1 - left / self._cooldown_total if self._cooldown_total else 1.0
        elif self._hand is not None and self._mode == "browse":
            selecting, since = self._selecting()
            if selecting:
                progress = min(1.0, (self._last_seen - since) / cfg.select_hold)
            elif self._victory_now:
                progress = min(1.0, (self._last_seen - self._victory_since) / cfg.browse_hold)
        elif self._hand is not None and self._mode != "idle":
            if self._state == IDLE and self._mode == "send" and self._victory_now:
                progress = min(1.0, (self._last_seen - self._victory_since) / cfg.browse_hold)
            elif self._state == IDLE:
                if self._mode == "send":
                    progress = min(1.0, stable / cfg.arm_time) if now_pose == Pose.OPEN else 0.0
                else:      # receive: the fist is the first step
                    progress = (min(1.0, stable / cfg.grab_time)
                                if now_pose == Pose.FIST and self._seen_non_fist else 0.0)
            elif self._state == READY:
                progress = min(1.0, stable / cfg.grab_time) if now_pose == Pose.FIST else 0.0
            elif self._state == CATCH_ARMED:       # receive: the release is the second step
                progress = min(1.0, stable / cfg.open_confirm) if now_pose == Pose.OPEN else 0.0
            elif self._state == HOLDING:
                progress = min(1.0, self._speed / cfg.throw_speed)
        hint = _HINTS.get((self._state, self._mode), "")
        if self._mode == "idle":
            hint = "Gestures are paused (tracking only)"
        elif self._state == COOLDOWN:
            hint = "Done. Wait a moment ..."
        return EngineStatus(
            mode=self._mode, state=self._state,
            pose=now_pose if self._hand is not None else Pose.NONE,
            fingers=result.fingers if result else (False, False, False, False),
            hand_present=self._hand is not None, progress=progress, cooldown_left=left,
            speed=self._speed, palm_center=self._last_center if self._hand is not None else None,
            hint=hint, hand=self._hand, pinch=self._pinch_now and self._hand is not None,
            thumbs_up=self._thumb_now and self._hand is not None, swipe_note=self.swipe_note)
