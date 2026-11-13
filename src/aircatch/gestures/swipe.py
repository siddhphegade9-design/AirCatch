"""Swipe detection for the file explorer, one whole stroke at a time.

A *stroke* is one continuous movement of the hand: it starts when the hand speeds up and ends when the
hand stops, turns back, or leaves the picture. Each stroke is judged once, by where it went in total
(start to end), so the curl at the end of an arm arc cannot turn "down" into "left".

After a swipe the hand has to come back. The detector knows this: until the hand is back near where
the swipe began (or has rested for a while), any stroke going back the way it came is "the return"
and is never a swipe, however fast it is. Losing the hand for a moment (motion blur, leaving the
picture) does not forget this, and a stroke interrupted by a blurred frame or two is joined up again.

Units: movement is divided by the hand size, so everything is in palm lengths and works at any
distance from the camera. Time is in seconds from the caller's clock.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from aircatch.gestures.config import GestureConfig

REST, STROKE = "rest", "stroke"
_EDGE = 0.1          # this close to the picture border, a hand that vanishes probably left the picture


@dataclass
class Swipe:
    direction: str
    time: float
    speed: float          # peak, palm lengths per second
    unit: tuple[float, float]
    distance: float       # palm lengths


def _name(dx: float, dy: float) -> str:
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


class SwipeDetector:
    def __init__(self, config: GestureConfig) -> None:
        self.cfg = config
        self.note = ""
        self.aspect = 16 / 9          # picture width / height (x positions run 0 .. aspect)
        self.reset()

    def reset(self) -> None:
        self._state = REST
        self._hist: deque[tuple[float, float, float]] = deque()    # t, x, y (image units) for the speed
        self._trail: deque[tuple[float, float, float]] = deque()   # t, x, y: the last moments, to find a stroke's start
        self._scales: deque[tuple[float, float]] = deque()         # t, palm size
        self._pts: list[tuple[float, float, float]] = []           # the current stroke (image units)
        self._gaps: set[int] = set()                               # indices in _pts that follow a dropout
        self._scale = 1.0
        self._peak = 0.0
        self._stroke_rested = False
        self._fired = False            # this stroke has already been counted as a swipe (early)
        self._dip: tuple[int, float] | None = None       # slowest moment in the stroke since it was last fast
        self._dir: tuple[float, float] | None = None     # direction the stroke was last going fast
        self._slow_since: float | None = None
        self._rest_since: float | None = None
        self._last_seen: float | None = None
        self._last_xy: tuple[float, float] | None = None
        self._found_at = -1e9
        self._frame_dt = 1 / 30
        self._last_speed = 0.0
        self._vanish = None            # (time, position, scale, was moving) when the hand last went missing
        self._last_fire = -1e9
        # the hand still has to come back from the last swipe: start x, y (image), unit x, y, length (palm)
        self._ret: tuple[float, float, float, float, float] | None = None
        self._ret_since = 0.0
        self._ret_back = 0.0           # how far (palm lengths) the hand has come back so far
        self._ret_moved = False        # ...and whether it has made any move back at all
        self._ret_moved_at = 0.0

    @property
    def in_stroke(self) -> bool:
        return self._state == STROKE

    @property
    def returning(self) -> bool:
        return self._ret is not None

    # ------------------------------------------------------------------ input
    def lost(self, now: float) -> Swipe | None:
        """No hand this frame."""
        if self._last_seen is None:
            return None
        cfg, gap = self.cfg, now - self._last_seen
        out = None
        if self._state == STROKE and gap >= cfg.swipe_rejoin:
            lx, ly = self._last_xy
            at_edge = min(lx, ly, self.aspect - lx, 1 - ly) < _EDGE
            if at_edge or gap >= cfg.swipe_rejoin_long:
                out = self._finish(self._last_seen, vanished=True)   # it never came back: judge what we saw
        if gap >= cfg.swipe_dropout and self._vanish is None:
            # remember where the hand was, and whether it was moving: a fast hand often blurs out of sight
            moving = self._state == STROKE or self._last_speed >= 0.5 * cfg.swipe_start_speed
            self._vanish = (self._last_seen, self._last_xy, self._median_scale(), moving)
        if gap >= cfg.swipe_dropout:
            self._slow_since = self._rest_since = None
        if gap >= cfg.swipe_forget:                              # gone for good: start from scratch
            note = self.note
            self.reset()
            self.note = note
        return out

    def abort(self) -> None:
        """A different gesture (pinch, peace sign) is being made: the current stroke is not a swipe."""
        self._state, self._pts, self._gaps, self._fired = REST, [], set(), False
        self._hist.clear()
        self._trail.clear()

    def update(self, x: float, y: float, size: float, now: float) -> Swipe | None:
        cfg = self.cfg
        reappeared = self._last_seen is None or now - self._last_seen >= cfg.swipe_dropout
        jump = None
        if reappeared:
            self._hist.clear()
            self._trail.clear()
            gap = now - self._last_seen if self._last_seen is not None else 1e9
            vanish, self._vanish = self._vanish, None
            if vanish is None and self._last_seen is not None:      # (gone for only a frame or two)
                vanish = (self._last_seen, self._last_xy, self._median_scale(), True)
            if self._state == STROKE and gap < cfg.swipe_rejoin:
                self._gaps.add(len(self._pts))      # joined up across a dropout
                t1, x1, y1 = self._pts[-1]          # ...and the hand moved fast while unseen (motion blur)
                self._peak = max(self._peak, math.hypot(x - x1, y - y1) / self._scale / max(now - t1, 1e-3))
            else:
                if vanish is not None and gap <= cfg.swipe_rejoin_long:
                    jump = vanish                    # it was moving and blurred out of sight: judge the jump
                elif self._last_seen is not None and vanish is not None:
                    self._after_absence(vanish, x, y)
                if self._state != STROKE:
                    self._found_at = now             # really (re)entered the picture, not just a blurred frame
        if self._last_seen is not None and not reappeared:
            step = now - self._last_seen                # how often frames come (webcams vary: 15-60 fps)
            self._frame_dt = 0.9 * self._frame_dt + 0.1 * min(step, 0.2)
        if jump is not None:
            out = self._judge_jump(jump, x, y, now)
            self._last_seen, self._last_xy = now, (x, y)
            self._hist.append((now, x, y))
            self._trail.append((now, x, y))
            self._found_at = now
            return out
        self._last_seen, self._last_xy = now, (x, y)
        self._scales.append((now, size))
        while len(self._scales) > 1 and now - self._scales[0][0] > 0.5:
            self._scales.popleft()
        for q in (self._hist, self._trail):
            q.append((now, x, y))
        window = max(cfg.swipe_speed_window, 2.2 * self._frame_dt)   # always over at least two frame steps
        while len(self._hist) > 2 and now - self._hist[1][0] >= window:
            self._hist.popleft()
        while len(self._trail) > 2 and now - self._trail[0][0] > 0.4:
            self._trail.popleft()

        scale = self._scale if self._state == STROKE else self._median_scale()
        vel = self._velocity(scale)
        if vel is None:                              # not enough history yet to say anything
            if self._state == STROKE:
                self._pts.append((now, x, y))
            return None
        speed, vx, vy = vel
        self._last_speed = speed
        slow = speed < cfg.swipe_end_speed
        self._rest_since = (self._rest_since if self._rest_since is not None else now) if slow else None
        self._check_returned(x, y, now)

        if self._state == REST:
            if speed >= cfg.swipe_start_speed:
                self._begin(scale, speed, vx, vy)
            return None

        # in a stroke
        self._pts.append((now, x, y))
        self._peak = max(self._peak, speed)
        _, x0, y0 = self._pts[0]
        nx, ny = (x - x0) / scale, (y - y0) / scale
        net = math.hypot(nx, ny)
        # turned back: the stroke ends at its far point, and the way back starts a new stroke
        if net >= 0.4 and speed >= cfg.swipe_start_speed and (vx * nx + vy * ny) < -0.5 * speed * net:
            far = max(range(len(self._pts)),
                      key=lambda i: (self._pts[i][1] - x0) * nx + (self._pts[i][2] - y0) * ny)
            tail = self._pts[far:]
            self._pts = self._pts[:far + 1]
            out = self._finish(self._pts[-1][0])
            self._state, self._scale, self._pts, self._gaps = STROKE, scale, tail, set()
            self._peak, self._slow_since, self._stroke_rested = speed, None, False
            self._dip, self._dir = None, (vx / speed, vy / speed)
            return out
        early = self._try_early(now)
        if early is not None:
            return early
        if slow or speed < cfg.swipe_end_share * self._peak:      # stopped, or nearly (a trembling hand never fully stops)
            if self._dip is None or speed < self._dip[1]:
                self._dip = (len(self._pts) - 1, speed)
            if self._slow_since is None:
                self._slow_since = now
            if now - self._slow_since >= max(cfg.swipe_end_time, 1.5 * self._frame_dt):
                return self._finish(now)
        else:
            self._slow_since = None
            if speed >= cfg.swipe_start_speed:
                deep = self._dip is not None and self._dip[1] < max(cfg.swipe_end_speed,
                                                                     cfg.swipe_resplit_share * self._peak)
                if self._dip is not None and ((deep and speed >= 3 * max(self._dip[1], 0.2)) or (
                        self._dir is not None and (vx * self._dir[0] + vy * self._dir[1]) < 0.5 * speed)):
                    # nearly stopped for an instant, then off again (or off in a new direction): a new stroke
                    i = self._dip[0]
                    tail = self._pts[i:]
                    self._pts = self._pts[:i + 1]
                    out = self._finish(self._pts[-1][0])
                    self._state, self._scale, self._pts, self._gaps = STROKE, scale, tail, set()
                    self._peak, self._slow_since, self._stroke_rested = speed, None, False
                    self._dip, self._dir = None, (vx / speed, vy / speed)
                    return out
                self._dip, self._dir = None, (vx / speed, vy / speed)
        if now - self._pts[0][0] > cfg.swipe_max_time:
            return self._finish(now)
        return None

    # ------------------------------------------------------------------ helpers
    def _judge_jump(self, vanish, x: float, y: float, now: float) -> Swipe | None:
        """The hand vanished while moving (motion blur) and is back: where it went is the swipe."""
        t_v, (vx0, vy0), scale, _ = vanish
        if self._state == STROKE:
            pts = self._pts + [(now, x, y)]
            gaps = self._gaps | {len(self._pts)}
        else:
            pts, gaps, self._scale, self._peak = [(t_v, vx0, vy0), (now, x, y)], {1}, scale, self._last_speed
            self._stroke_rested = False
        self._state, self._pts, self._gaps = STROKE, pts, gaps
        dist = math.hypot(x - pts[0][1], y - pts[0][2]) / self._scale
        self._peak = max(self._peak, dist / max(now - pts[0][0], 1e-3))
        return self._finish(now, jumped=True)

    def _try_early(self, now: float) -> Swipe | None:
        """Fire the moment a stroke has plainly become a swipe, without waiting for the hand to stop
        (that is what makes it feel immediate). Anything doubtful is left for the end of the stroke."""
        cfg = self.cfg
        if self._fired or self._ret is not None or len(self._pts) < 3:
            return None
        pts, scale = self._pts, self._scale
        t0, x0, y0 = pts[0]
        dx, dy = (pts[-1][1] - x0) / scale, (pts[-1][2] - y0) / scale
        dist = math.hypot(dx, dy)
        name = _name(dx, dy)
        vertical = name in ("up", "down")
        travel = cfg.swipe_travel_vertical if vertical else cfg.swipe_travel
        speed_min = cfg.swipe_speed_vertical if vertical else cfg.swipe_speed
        share_min = cfg.swipe_axis_share_vertical if vertical else cfg.swipe_axis_share
        if dist < cfg.swipe_early * travel or self._peak < speed_min or max(abs(dx), abs(dy)) / dist < share_min:
            return None
        # (only a stroke that got this far quickly: a long unhurried move, like lowering the hand, is left
        # for the end of the stroke, where its duration is judged)
        if t0 < self._found_at + 0.1 or now - t0 > 0.75 * cfg.swipe_core_time or now - self._last_fire < cfg.swipe_gap:
            return None
        steps = [math.hypot(b[1] - a[1], b[2] - a[2]) / scale for a, b in zip(pts, pts[1:])]
        if max(steps) > cfg.swipe_max_jump * dist and sum(steps) - max(steps) < 0.2 * dist:
            return None                                     # could be a tracking jump: wait and see
        ux, uy = dx / dist, dy / dist
        self._fired, self._last_fire = True, now
        self._ret, self._ret_since, self._ret_back = (x0, y0, ux, uy, dist), now, 0.0
        self._ret_moved = False
        self.note = f"{name}: ok"
        return Swipe(name, now, self._peak, (ux, uy), dist)

    def _after_absence(self, vanish, x: float, y: float) -> None:
        """Back after a long absence somewhere else: do not let the way back from there count."""
        _, (vx0, vy0), scale, _ = vanish
        dx, dy = (x - vx0) / scale, (y - vy0) / scale
        dist = math.hypot(dx, dy)
        if dist >= 0.6:
            self._ret, self._ret_since, self._ret_back = (vx0, vy0, dx / dist, dy / dist, dist), self._last_seen or 0.0, 0.0
            self._ret_moved = False

    def _begin(self, scale: float, speed: float, vx: float, vy: float) -> None:
        """A stroke starts. Walk back through the last moments to where the hand started moving this way."""
        trail = list(self._trail)
        i = len(trail) - 1
        while i > 0:
            (_, ax, ay), (_, bx, by) = trail[i - 1], trail[i]
            if (bx - ax) * vx + (by - ay) * vy <= 0:
                break
            i -= 1
        self._state, self._scale = STROKE, scale
        self._pts, self._gaps = trail[i:], set()
        self._peak, self._slow_since = speed, None
        self._dip, self._dir = None, (vx / speed, vy / speed)
        self._stroke_rested = self._rest_since_before(trail[i][0])

    def _median_scale(self) -> float:
        s = sorted(v for _, v in self._scales)
        return max(s[len(s) // 2], 1e-6) if s else 1.0

    def _velocity(self, scale: float) -> tuple[float, float, float] | None:
        h = self._hist
        if len(h) < 2:
            return None
        (t0, x0, y0), (t1, x1, y1) = h[0], h[-1]
        dt = t1 - t0
        if dt < 0.03:
            return None
        vx, vy = (x1 - x0) / dt / scale, (y1 - y0) / dt / scale
        return math.hypot(vx, vy), vx, vy

    def _rest_since_before(self, t: float) -> bool:
        return self._rest_since is not None and t - self._rest_since >= self.cfg.swipe_rest_time

    def _check_returned(self, x: float, y: float, now: float) -> None:
        """Is the hand back from the last swipe (so the opposite way is a swipe again)?"""
        if self._ret is None:
            return
        cfg = self.cfg
        sx, sy, ux, uy, length = self._ret
        scale = self._median_scale()
        along = ((x - sx) * ux + (y - sy) * uy) / scale    # how far out along the last swipe the hand still is
        # (never while a stroke is still being followed: it may be the way back itself, about to be judged)
        rested = self._state == REST and self._rest_since is not None and now - self._rest_since >= cfg.swipe_rest_time
        settled = self._ret_moved and now - self._ret_moved_at >= cfg.swipe_return_settle
        if (rested and (along <= cfg.swipe_return_fraction * length or self._ret_moved)) or settled:
            # back near the start, or came (roughly) back and paused: the cooldown is over
            self._ret = None
            self.note = "ready"
        elif self._state == REST and self._rest_since is not None and now - self._rest_since >= cfg.swipe_recentre:
            self._ret = None                                # stayed out there: that is the new start
            self.note = "ready"
        elif now - self._ret_since >= cfg.swipe_return_timeout:
            self._ret = None

    def _finish(self, now: float, vanished: bool = False, jumped: bool = False) -> Swipe | None:
        cfg = self.cfg
        pts, gaps, scale = self._pts, self._gaps, self._scale
        self._state, self._pts, self._gaps = REST, [], set()
        fired, self._fired = self._fired, False
        if len(pts) < 2:
            return None
        t0, x0, y0 = pts[0]
        dx, dy = (pts[-1][1] - x0) / scale, (pts[-1][2] - y0) / scale
        dist = math.hypot(dx, dy)
        if fired:                                           # already counted while it was still moving
            if self._ret is not None and self._ret[:2] == (x0, y0):
                self._ret = self._ret[:4] + (max(dist, self._ret[4]),)   # the way back is this long
                self._ret_since = now
            self._rest_since = None
            return None
        if dist < 0.3:
            return None                                     # a fidget, not worth a note
        name = _name(dx, dy)
        vertical = name in ("up", "down")
        travel = cfg.swipe_travel_vertical if vertical else cfg.swipe_travel
        speed_min = cfg.swipe_speed_vertical if vertical else cfg.swipe_speed
        share_min = cfg.swipe_axis_share_vertical if vertical else cfg.swipe_axis_share
        if jumped:
            speed_min *= 0.4      # the time includes the moments the camera could not see the hand at all
        if vanished and self._last_xy is not None:
            lx, ly = self._last_xy
            if min(lx, ly, self.aspect - lx, 1 - ly) < _EDGE or self._peak >= speed_min:
                travel *= cfg.swipe_exit_ease               # it left the picture: it was going somewhere
        # heading for a side or the top of the picture and gone (or gone and back in at that edge): it
        # swiped right out of view. (Not downwards: lowering the hand to rest leaves the picture that way too.)
        if vanished or jumped:
            _, ex, ey = pts[-1]                             # where it was last seen / came back into view
            room = {"right": self.aspect - ex, "left": ex, "up": ey, "down": 1e9}[name] / scale
            if room <= cfg.swipe_exit_reach:
                travel = min(travel, cfg.swipe_exit_travel)
        ux, uy = dx / dist, dy / dist
        # the way back from the last swipe is never a swipe
        if self._ret is not None:
            _, _, rx, ry, _ = self._ret
            back = -(ux * rx + uy * ry)
            if back > 0.35:
                self.note = f"{name}: ignored (hand returning)"
                self._ret_back += back * dist
                self._ret_moved, self._ret_moved_at = True, now
                if cfg.swipe_return_instant and self._ret_back >= cfg.swipe_return_fraction * self._ret[4]:
                    self._ret = None                        # it has come back: anything goes again
                return None
            if back > -0.7 and not self._stroke_rested and not self._ret_moved:
                # sideways straight after a swipe, before coming back at all: only counts from a pause
                self.note = f"{name}: ignored (pause before a new direction)"
                self._ret_moved, self._ret_moved_at = True, now   # (most likely a very crooked way back)
                if cfg.swipe_side_protect and dist >= travel and self._peak >= speed_min:
                    # ...but if it was a real command given too soon, its own way back must not count either
                    self._ret, self._ret_since, self._ret_back = (x0, y0, ux, uy, dist), now, 0.0
                    self._ret_moved = False
                return None
        if t0 < self._found_at + 0.1 and not jumped:
            return self._reject(f"{name}: ignored (hand just came into view)", pts, ux, uy, dist, speed_min)
        if dist < travel:
            return self._reject(f"{name}: too short ({dist:.1f} of {travel:.1f})", pts, ux, uy, dist, speed_min)
        if self._peak < speed_min:
            return self._reject(f"{name}: too slow ({self._peak:.1f} of {speed_min:.1f})", pts, ux, uy, dist, speed_min)
        if max(abs(dx), abs(dy)) / dist < share_min:
            return self._reject(f"{name}: too diagonal", pts, ux, uy, dist, speed_min)
        core = 0.0 if jumped else self._core_time(pts, x0, y0, ux, uy, dist * scale)
        if core > cfg.swipe_core_time:
            return self._reject(f"{name}: too slow (took {core:.2f} s, max {cfg.swipe_core_time:.2f})",
                                pts, ux, uy, dist, speed_min)
        # a glitch (the tracker re-finding the hand elsewhere) is one huge step; steps across a
        # dropout (missed frames) are real movement and do not count
        dts = [b[0] - a[0] for a, b in zip(pts, pts[1:])]
        normal = min(dts) * 1.6 if dts else 1.0             # longer than a frame: frames were missed
        steps = [math.hypot(b[1] - a[1], b[2] - a[2]) / scale
                 for i, (a, b) in enumerate(zip(pts, pts[1:]), 1) if i not in gaps and b[0] - a[0] <= normal]
        # (a teleport: one huge step and almost no other movement; at 15 fps a real swipe is only a few
        # frames long, so one big step alone proves nothing)
        if steps and max(steps) > cfg.swipe_max_jump * dist and max(steps) > 0.5 \
                and sum(steps) - max(steps) < 0.2 * dist:
            return self._reject(f"{name}: ignored (a tracking jump, not a movement)", pts, ux, uy, dist, 0.0)
        if now - self._last_fire < cfg.swipe_gap:
            self.note = f"{name}: ignored (too soon after the last swipe)"
            return None
        self._last_fire = now
        self._ret, self._ret_since, self._ret_back = (x0, y0, ux, uy, dist), now, 0.0
        self._ret_moved = False
        self._rest_since = None
        self.note = f"{name}: ok"
        return Swipe(name, now, self._peak, (ux, uy), dist)

    @staticmethod
    def _core_time(pts, x0, y0, ux, uy, dist) -> float:
        """Time the hand took for the middle 80% of the stroke (a swipe is quick; moving the hand elsewhere is not)."""
        t10 = t90 = None
        for t, x, y in pts:
            along = (x - x0) * ux + (y - y0) * uy
            if t10 is None and along >= 0.1 * dist:
                t10 = t
            if along >= 0.9 * dist:
                t90 = t
                break
        return (t90 - t10) if t10 is not None and t90 is not None else 0.0

    def _reject(self, note, pts, ux, uy, dist, speed_min) -> None:
        """Not a swipe. But if it was nearly one, the hand will come back from it: that is not a swipe either
        (better a missed swipe than one the wrong way)."""
        self.note = note
        if dist >= 0.4 and self._peak >= 0.5 * speed_min:
            self._ret, self._ret_since, self._ret_back = (pts[0][1], pts[0][2], ux, uy, dist), pts[-1][0], 0.0
            self._ret_moved = False
            self._rest_since = None
        return None
