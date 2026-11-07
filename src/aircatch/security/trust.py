"""The list of devices this device has paired with (its 'address book of trusted devices')."""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from aircatch.security.identity import (DeviceIdentity, clean_device_name, normalize_cert_pem)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrustedPeer:
    device_id: str      # SHA-256 fingerprint of the peer's certificate
    name: str
    cert_pem: str
    paired_at: str = ""

    @staticmethod
    def from_identity(identity: DeviceIdentity) -> "TrustedPeer":
        return TrustedPeer(identity.fingerprint, identity.name, identity.cert_pem, _now())


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class TrustStore:
    """Persistent set of trusted devices, stored as JSON.

    On load, every entry's certificate is re-checked against its device ID, so a
    hand-edited or damaged entry is ignored rather than trusted.

    The file is re-read whenever it changes on disk, so unpairing a device from another
    program (or command) takes effect in a running receiver immediately.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._peers: dict[str, TrustedPeer] = {}
        self._stamp_cache: tuple[int, int] | None = None
        self._load()

    def _stamp(self) -> tuple[int, int] | None:
        try:
            info = self._path.stat()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def _refresh(self) -> None:
        """Reload if the file was changed or deleted behind our back (call with lock held)."""
        if self._stamp() != self._stamp_cache:
            self._load()

    def _load(self) -> None:
        self._peers = {}
        self._stamp_cache = self._stamp()
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text("utf-8"))
            entries = data["peers"]
        except (OSError, ValueError, KeyError, TypeError):
            backup = self._path.with_suffix(".corrupt")
            log.warning("trust store unreadable; moving it to %s", backup)
            try:
                os.replace(self._path, backup)
            except OSError:
                pass
            self._stamp_cache = self._stamp()
            return
        for entry in entries:
            try:
                pem, fingerprint = normalize_cert_pem(entry["cert_pem"])
                if fingerprint != entry["device_id"]:
                    raise ValueError("fingerprint does not match certificate")
                self._peers[fingerprint] = TrustedPeer(
                    fingerprint, clean_device_name(str(entry.get("name", ""))), pem,
                    str(entry.get("paired_at", "")))
            except (KeyError, ValueError, TypeError):
                log.warning("ignoring invalid trust-store entry")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "peers": [asdict(p) for p in self._peers.values()]}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        self._stamp_cache = self._stamp()

    def peers(self) -> list[TrustedPeer]:
        with self._lock:
            self._refresh()
            return sorted(self._peers.values(), key=lambda p: p.name.lower())

    def get(self, device_id: str) -> TrustedPeer | None:
        with self._lock:
            self._refresh()
            return self._peers.get(device_id.lower())

    def find(self, query: str) -> list[TrustedPeer]:
        """Match by name (any case) or by the start of the device ID."""
        q = query.strip().lower().replace(" ", "")
        with self._lock:
            self._refresh()
            return [p for p in self._peers.values()
                    if p.name.lower() == query.strip().lower() or (q and p.device_id.startswith(q))]

    def add(self, peer: TrustedPeer) -> None:
        pem, fingerprint = normalize_cert_pem(peer.cert_pem)
        if fingerprint != peer.device_id:
            raise ValueError("device ID does not match the certificate")
        with self._lock:
            self._refresh()
            self._peers[fingerprint] = TrustedPeer(
                fingerprint, clean_device_name(peer.name), pem, peer.paired_at or _now())
            self._save()

    def remove(self, device_id: str) -> bool:
        with self._lock:
            self._refresh()
            existed = self._peers.pop(device_id.lower(), None) is not None
            if existed:
                self._save()
            return existed
