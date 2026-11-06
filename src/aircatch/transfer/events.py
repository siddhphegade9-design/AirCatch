"""Events the transfer engine reports. The UI and animations will listen to these."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

EventKind = Literal[
    "offer",      # receiver: a sender is asking to send a file
    "accepted",   # both: the receiver agreed
    "rejected",   # both: the receiver (or its limits) said no
    "progress",   # both: bytes moved
    "verifying",  # receiver: all bytes arrived, checking SHA-256
    "completed",  # both: file saved AND hash verified
    "failed",     # both: something went wrong
]


@dataclass(frozen=True)
class TransferEvent:
    kind: EventKind
    direction: Literal["send", "receive"]
    transfer_id: str
    filename: str
    peer: str = ""
    bytes_done: int = 0
    total: int = 0
    message: str = ""        # reason code / human text for rejected and failed
    path: str | None = None  # final saved path (receiver, on completed)


EventCallback = Callable[[TransferEvent], None]
