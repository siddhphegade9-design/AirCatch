"""Host side of pairing: a short-lived, single-use 'pairing window'."""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

PROOF_CONTEXT = b"aircatch-pair-v1"


def compute_proof(token: str, host_fingerprint: str, joiner_fingerprint: str) -> str:
    """Proof that the joiner knows the one-time token, tied to both devices' certificates.

    The token is random and 128 bits long, so this proof cannot be guessed or
    brute-forced offline. It is sent only inside the encrypted TLS connection.
    """
    message = PROOF_CONTEXT + bytes.fromhex(host_fingerprint) + bytes.fromhex(joiner_fingerprint)
    return hmac.new(token.encode("ascii"), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class PairRequest:
    device_name: str
    device_id: str
    peer_address: str


DecidePairing = Callable[[PairRequest], Awaitable[bool]]


@dataclass
class PairingSession:
    token: str
    ttl: float = 120.0
    max_attempts: int = 3
    decide: DecidePairing | None = None
    on_paired: Callable[[object], None] | None = None
    in_progress: bool = False
    _expires_at: float = field(init=False)
    _failures: int = field(default=0, init=False)
    _closed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._expires_at = time.monotonic() + self.ttl

    def is_open(self) -> bool:
        return not self._closed and time.monotonic() < self._expires_at

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.max_attempts:
            self._closed = True

    @property
    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self._failures)

    def close(self) -> None:
        self._closed = True
