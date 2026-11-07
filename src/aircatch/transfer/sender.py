"""The sending side: offer a file, wait for permission, stream it, send the hash."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ssl
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from aircatch.security import tls
from aircatch.security.identity import DeviceIdentity, fingerprint_from_der
from aircatch.security.trust import TrustedPeer
from aircatch.transfer.events import EventCallback, EventKind, TransferEvent
from aircatch.transfer.protocol import (CHUNK_SIZE, PROTOCOL_VERSION, STREAM_LIMIT,
                                        ProtocolError, read_message, send_message)

PROGRESS_INTERVAL = 0.05


@dataclass(frozen=True)
class SendResult:
    ok: bool
    transfer_id: str
    filename: str
    size: int
    reason: str = ""          # machine-readable code when ok is False
    receiver_name: str = ""
    seconds: float = 0.0
    sha256: str = ""
    saved_as: str = ""        # name the receiver saved it under (may differ if name was taken)

    @property
    def mb_per_second(self) -> float:
        return (self.size / 1_000_000) / self.seconds if self.seconds > 0 else 0.0


def _read_and_hash(handle, hasher, want: int) -> bytes:
    chunk = handle.read(want)
    hasher.update(chunk)
    return chunk


async def send_file(host: str, port: int, path: str | Path, *, sender_name: str,
                    on_event: EventCallback | None = None, connect_timeout: float = 10.0,
                    accept_timeout: float = 60.0, idle_timeout: float = 30.0,
                    identity: DeviceIdentity | None = None,
                    peer: TrustedPeer | None = None) -> SendResult:
    """Send one file. Expected failures are returned (ok=False), not raised.

    Secure mode: pass both `identity` (this device) and `peer` (the paired receiver).
    The connection is then TLS 1.3 and only the pinned receiver certificate is accepted.
    With neither, the old unencrypted mode is used (testing only).

    Cancelling the task that runs this function aborts the transfer cleanly.
    """
    if (identity is None) != (peer is None):
        raise ValueError("identity and peer must be given together (secure mode)")
    path = Path(path)
    transfer_id = uuid.uuid4().hex
    filename = path.name
    started = time.monotonic()

    def emit(kind: EventKind, *, bytes_done: int = 0, total: int = 0, message: str = "",
             peer: str = "") -> None:
        if on_event is not None:
            with contextlib.suppress(Exception):
                on_event(TransferEvent(kind, "send", transfer_id, filename, peer=peer,
                                       bytes_done=bytes_done, total=total, message=message))

    def failure(reason: str, size: int = 0, receiver: str = "") -> SendResult:
        emit("failed", total=size, message=reason, peer=receiver)
        return SendResult(False, transfer_id, filename, size, reason, receiver,
                          time.monotonic() - started)

    try:
        size = path.stat().st_size
        if not path.is_file():
            raise FileNotFoundError
    except OSError:
        return failure("file_not_found")

    ssl_ctx = tls.build_client_context(identity, peer.cert_pem) if peer is not None else None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=STREAM_LIMIT, ssl=ssl_ctx,
                                    server_hostname="aircatch" if ssl_ctx else None),
            connect_timeout)
    except ssl.SSLCertVerificationError:
        return failure("untrusted_peer", size)
    except ssl.SSLError:
        return failure("tls_error", size)
    except (OSError, asyncio.TimeoutError):
        return failure("could_not_connect", size)

    receiver_name = ""
    try:
        if peer is not None:   # belt and braces: exactly the certificate we paired with
            ssl_object = writer.get_extra_info("ssl_object")
            der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
            if not der or fingerprint_from_der(der) != peer.device_id:
                return failure("untrusted_peer", size)
        await send_message(writer, {"type": "offer", "version": PROTOCOL_VERSION,
                                    "transfer_id": transfer_id, "sender_name": sender_name,
                                    "filename": filename, "size": size})
        emit("offer", total=size)
        reply = await read_message(reader, accept_timeout)
        receiver_name = str(reply.get("receiver_name", ""))[:100]
        if reply.get("type") == "reject":
            reason = str(reply.get("reason", "rejected"))[:50]
            emit("rejected", total=size, message=reason, peer=receiver_name)
            return SendResult(False, transfer_id, filename, size, reason, receiver_name,
                              time.monotonic() - started)
        if reply.get("type") != "accept":
            raise ProtocolError("unexpected reply to offer")
        emit("accepted", total=size, peer=receiver_name)

        hasher = hashlib.sha256()
        sent, last_emit = 0, 0.0
        try:
            handle = await asyncio.to_thread(open, path, "rb")
        except OSError:
            return failure("file_unreadable", size, receiver_name)
        try:
            while sent < size:
                chunk = await asyncio.to_thread(_read_and_hash, handle, hasher,
                                                min(CHUNK_SIZE, size - sent))
                if not chunk:
                    return failure("file_changed", size, receiver_name)  # file shrank
                writer.write(chunk)
                await asyncio.wait_for(writer.drain(), idle_timeout)
                sent += len(chunk)
                now = time.monotonic()
                if now - last_emit >= PROGRESS_INTERVAL or sent == size:
                    last_emit = now
                    emit("progress", bytes_done=sent, total=size, peer=receiver_name)
        finally:
            handle.close()

        digest = hasher.hexdigest()
        await send_message(writer, {"type": "done", "sha256": digest})
        result = await read_message(reader, idle_timeout)
        if result.get("type") == "result" and result.get("ok") is True:
            emit("completed", bytes_done=size, total=size, peer=receiver_name)
            return SendResult(True, transfer_id, filename, size, "", receiver_name,
                              time.monotonic() - started, digest,
                              str(result.get("saved_as", ""))[:255])
        return failure(str(result.get("reason", "failed"))[:50], size, receiver_name)
    except (ProtocolError, ConnectionError, asyncio.TimeoutError, OSError) as exc:
        reason = "timeout" if isinstance(exc, asyncio.TimeoutError) else "connection_lost"
        if isinstance(exc, ssl.SSLError) or isinstance(exc.__cause__, ssl.SSLError):
            reason = "tls_error"
        if isinstance(exc, ProtocolError) and "timed out" not in str(exc):
            reason = "connection_lost"
        return failure(reason, size, receiver_name)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
