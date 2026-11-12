"""Plain data types for the gesture engine. Nothing here knows about cameras, UIs or networks."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

# MediaPipe hand landmark numbers (21 points per hand)
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

# (name, knuckle, tip) for the four fingers used to decide open palm / fist
FINGERS = (("index", INDEX_MCP, INDEX_TIP), ("middle", MIDDLE_MCP, MIDDLE_TIP),
           ("ring", RING_MCP, RING_TIP), ("pinky", PINKY_MCP, PINKY_TIP))
PALM_POINTS = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)

Mode = Literal["idle", "send", "receive", "browse"]
MODES: tuple[str, ...] = ("idle", "send", "receive", "browse")

Point = tuple[float, float, float]


class Pose(str, Enum):
    NONE = "none"      # no hand
    OPEN = "open"      # open palm: all four fingers stretched out
    FIST = "fist"      # all four fingers curled
    OTHER = "other"    # anything in between (pointing, victory sign, half-open ...)


@dataclass(frozen=True)
class HandObservation:
    """One hand in one camera frame, as reported by a hand tracker.

    landmarks: 21 points, x and y normalised to the image (0..1), z roughly in x units.
    world:     optional 21 points in metres, centred on the hand. Turning the hand does not
               change distances in these, so they are best for telling a fist from an open palm.
    aspect:    image width / height, needed so that horizontal and vertical speed are comparable.
    """
    landmarks: tuple[Point, ...]
    world: tuple[Point, ...] | None = None
    handedness: str = ""
    confidence: float = 1.0
    aspect: float = 16 / 9

    def __post_init__(self) -> None:
        if len(self.landmarks) != 21:
            raise ValueError("a hand needs exactly 21 landmarks")
        if self.world is not None and len(self.world) != 21:
            raise ValueError("world landmarks must also be 21 points")


EventKind = Literal[
    "hand_found",   # a hand appeared
    "hand_lost",    # the hand has been gone longer than the grace period
    "ready",        # open palm held steady: the engine is armed
    "grab",         # (send mode) fist closed after an open palm: the file is 'in hand'
    "drop",         # (send mode) hand opened without a throw, or timed out: nothing is sent
    "throw",        # (send mode) hand opened with a fast flick: SEND
    "catch",        # (receive mode) open palm closed into a fist: ACCEPT
    "cancelled",    # an armed gesture was abandoned (for example the hand left the view)
    "browse_open",  # peace sign held in send mode: open the file explorer
    "browse_close", # peace sign held while browsing: close it
    "swipe",        # (browse) quick hand movement; detail = up / down / left / right
    "pinch",        # (browse) thumb and index pinched and held: SELECT
]


@dataclass(frozen=True)
class GestureEvent:
    kind: EventKind
    time: float
    speed: float = 0.0                       # hand speed in palm-lengths per second
    direction: tuple[float, float] = (0.0, 0.0)   # unit vector, x right, y down (as displayed)
    detail: str = ""


@dataclass(frozen=True)
class EngineStatus:
    """Everything a screen needs to show clear feedback about what the engine sees."""
    mode: str
    state: str
    pose: Pose
    fingers: tuple[bool, bool, bool, bool]   # index, middle, ring, pinky extended?
    hand_present: bool
    progress: float                          # 0..1 towards the next step (hold timers, cooldown)
    cooldown_left: float
    speed: float
    palm_center: tuple[float, float] | None  # normalised image position
    hint: str
    hand: HandObservation | None = None
    pinch: bool = False                      # is the hand pinching right now?
