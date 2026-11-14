"""The hardest part of the built-in self-test: two devices pair and send a file, inside one program.

Used by `AirCatch.exe --self-test FILE` after packaging. Two complete network services are started
in throw-away folders, paired with a one-time code exactly as two laptops would be, and a file is
sent each way over real TLS on this computer and compared. It proves the packaged program contains
everything pairing and transfer need. It cannot prove that two real PCs can reach each other over
Wi-Fi (firewalls and routers are not involved here).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path


class _Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []
        self._cond = threading.Condition()

    def __call__(self, kind: str, payload: dict) -> None:
        with self._cond:
            self.items.append((kind, payload))
            self._cond.notify_all()

    def wait_for(self, kind: str, match=lambda p: True, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for k, p in self.items:
                    if k == kind and match(p):
                        return p
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"no '{kind}' within {timeout:.0f} s")
                self._cond.wait(left)


def pair_and_transfer(size: int = 3_000_000) -> str:
    """Raises if anything fails; returns a one-line summary."""
    from aircatch.pairing.payload import PairingPayload
    from aircatch.service import NetworkService

    root = Path(tempfile.mkdtemp(prefix="aircatch-selftest-pair-"))
    services, sinks = [], []
    try:
        for name in ("Self-test A", "Self-test B"):
            sink = _Events()
            service = NetworkService(sink, config_dir=root / name / "config", download_dir=root / name / "inbox",
                                     name=name, advertise=False)
            service.start()
            services.append(service)
            sinks.append(sink)
            if not service.ready.wait(30) or service.start_error:
                raise RuntimeError(f"{name} did not start: {service.start_error or 'timed out'}")
        (a, b), (sa, sb) = services, sinks

        shown = PairingPayload.parse(a.open_pairing())                 # A shows a code ...
        b.join_pairing(PairingPayload(host="127.0.0.1", port=shown.port, fingerprint=shown.fingerprint,
                                      token=shown.token, name=shown.name).to_uri())      # ... B types it in
        sa.wait_for("pairing_request")
        a.answer_pairing(True)                                         # ... and A's user approves
        joined = sb.wait_for("join_result")
        if not joined.get("ok"):
            raise RuntimeError(f"pairing failed: {joined.get('text')}")
        sa.wait_for("paired")

        started = time.monotonic()
        for sender, receiver, to_sink, from_sink, label in ((b, a, sa, sb, "B to A"), (a, b, sb, sa, "A to B")):
            source = root / f"{label.replace(' ', '-')}.bin"
            data = os.urandom(size)
            source.write_bytes(data)
            sender.send(receiver.identity.fingerprint, str(source), None, f"127.0.0.1:{receiver.port}")
            offer = to_sink.wait_for("offer", lambda p, n=source.name: p["filename"] == n)
            receiver.answer_offer(offer["transfer_id"], True)
            result = from_sink.wait_for("send_result", lambda p, n=source.name: p.get("filename", n) == n)
            if not result.get("ok"):
                raise RuntimeError(f"{label}: {result.get('text') or result.get('reason')}")
            arrived = Path(receiver._download_dir) / result.get("saved_as", source.name)
            if hashlib.sha256(arrived.read_bytes()).digest() != hashlib.sha256(data).digest():
                raise RuntimeError(f"{label}: the received file differs from the one sent")
        seconds = time.monotonic() - started
        return (f"paired with a one-time code and sent {size // 1_000_000} MB each way over TLS on this "
                f"computer in {seconds:.1f} s; both files arrived identical")
    finally:
        for service in services:
            try:
                service.stop()
            except Exception:                      # the result is what matters; do not hide it
                pass
