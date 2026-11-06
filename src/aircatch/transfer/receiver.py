"""The receiving side: listens for offers, asks permission, streams to disk, verifies."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from aircatch.transfer.events import EventCallback, EventKind, TransferEvent
from aircatch.transfer.filenames import reserve_destination, sanitize_filename
from aircatch.transfer.protocol import (CHUNK_SIZE, PROTOCOL_VERSION, STREAM_LIMIT,
                                        ProtocolError, read_message, send_message)

log = logging.getLogger(__name__)

GIB = 1024 ** 3
DEFAULT_MAX_FILE_SIZE = 10 * GIB
DISK_SAFETY_MARGIN = 50 * 1024 * 1024
OFFER_TIMEOUT = 15.0
PROGRESS_INTERVAL = 0.05
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IncomingOffer:
    transfer_id: str
    sender_name: str
    filename: str            # already sanitized
    original_filename: str   # what the sender claimed
    size: int
    peer: str                # network address of the sender


DecideCallback = Callable[[IncomingOffer], Awaitable[bool]]


class TransferFailed(Exception):
    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _write_and_hash(handle, hasher, chunk: bytes) -> None:
    handle.write(chunk)
    hasher.update(chunk)


def _finalize(tmp_path: Path, dest_dir: Path, filename: str) -> Path:
    destination = reserve_destination(dest_dir, filename)
    os.replace(tmp_path, destination)  # replaces our own empty placeholder only
    return destination


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _classify(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, TransferFailed):
        return exc.reason, str(exc)
    if isinstance(exc, asyncio.IncompleteReadError):
        return "connection_lost", "connection closed before the file finished"
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout", "the sender stopped sending data"
    if isinstance(exc, ConnectionError):
        return "connection_lost", "connection lost"
    if isinstance(exc, ProtocolError):
        return "protocol_error", str(exc)
    if isinstance(exc, OSError):
        return "disk_error", f"could not write the file: {exc}"
    return "error", str(exc)


class Receiver:
    def __init__(self, dest_dir: str | Path, decide: DecideCallback, *,
                 device_name: str = "AirCatch", on_event: EventCallback | None = None,
                 max_file_size: int = DEFAULT_MAX_FILE_SIZE, max_concurrent: int = 2,
                 idle_timeout: float = 30.0, decide_timeout: float = 60.0) -> None:
        self._dest_dir = Path(dest_dir)
        self._tmp_dir = self._dest_dir / ".aircatch-incoming"
        self._decide = decide
        self._device_name = device_name
        self._on_event = on_event
        self._max_file_size = max_file_size
        self._max_concurrent = max_concurrent
        self._idle_timeout = idle_timeout
        self._decide_timeout = decide_timeout
        self._server: asyncio.base_events.Server | None = None
        self._active: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    # ---------- lifecycle ----------
    async def start(self, host: str = "0.0.0.0", port: int = 0) -> int:
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir.mkdir(exist_ok=True)
        self._cleanup_stale_partials()
        self._server = await asyncio.start_server(self._on_connection, host, port,
                                                  limit=STREAM_LIMIT)
        return self.port

    @property
    def port(self) -> int:
        assert self._server is not None, "receiver not started"
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._server = None

    def _cleanup_stale_partials(self) -> None:
        cutoff = time.time() - 24 * 3600
        for part in self._tmp_dir.glob("*.part"):
            with contextlib.suppress(OSError):
                if part.stat().st_mtime < cutoff:
                    part.unlink()

    # ---------- events ----------
    def _emit(self, kind: EventKind, offer: IncomingOffer, *, bytes_done: int = 0,
              message: str = "", path: str | None = None) -> None:
        if self._on_event is None:
            return
        event = TransferEvent(kind=kind, direction="receive", transfer_id=offer.transfer_id,
                              filename=offer.filename, peer=offer.sender_name or offer.peer,
                              bytes_done=bytes_done, total=offer.size, message=message, path=path)
        try:
            self._on_event(event)
        except Exception:  # a buggy listener must never break a transfer
            log.exception("event listener raised")

    # ---------- connection handling ----------
    async def _on_connection(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            await self._handle(reader, writer)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("unexpected error while handling a connection")
        finally:
            self._tasks.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    async def _try_send(writer: asyncio.StreamWriter, message: dict) -> None:
        with contextlib.suppress(Exception):
            await send_message(writer, message)

    def _parse_offer(self, msg: dict, peer: str) -> tuple[IncomingOffer | None, str]:
        if msg.get("type") != "offer":
            return None, "malformed"
        if msg.get("version") != PROTOCOL_VERSION:
            return None, "unsupported_version"
        transfer_id, filename, size = msg.get("transfer_id"), msg.get("filename"), msg.get("size")
        sender = msg.get("sender_name", "Unknown")
        if not (isinstance(transfer_id, str) and _ID_PATTERN.match(transfer_id)):
            return None, "malformed"
        if not (isinstance(filename, str) and 0 < len(filename) <= 1024):
            return None, "malformed"
        if not (isinstance(size, int) and not isinstance(size, bool) and size >= 0):
            return None, "malformed"
        if not isinstance(sender, str):
            return None, "malformed"
        sender = re.sub(r"[\x00-\x1f]", "", sender)[:100] or "Unknown"
        return IncomingOffer(transfer_id, sender, sanitize_filename(filename), filename,
                             size, peer), ""

    def _precheck(self, offer: IncomingOffer) -> str:
        if offer.transfer_id in self._active:
            return "duplicate"
        if len(self._active) >= self._max_concurrent:
            return "busy"
        if offer.size > self._max_file_size:
            return "too_large"
        try:
            if shutil.disk_usage(self._dest_dir).free < offer.size + DISK_SAFETY_MARGIN:
                return "disk_full"
        except OSError:
            pass
        return ""

    async def _reject(self, writer: asyncio.StreamWriter, offer: IncomingOffer,
                      reason: str) -> None:
        await self._try_send(writer, {"type": "reject", "reason": reason,
                                      "receiver_name": self._device_name})
        self._emit("rejected", offer, message=reason)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        peer = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        try:
            msg = await read_message(reader, OFFER_TIMEOUT)
        except ProtocolError as exc:
            log.info("bad connection from %s: %s", peer, exc)
            await self._try_send(writer, {"type": "reject", "reason": "malformed"})
            return
        offer, error = self._parse_offer(msg, peer)
        if offer is None:
            await self._try_send(writer, {"type": "reject", "reason": error})
            return
        reason = self._precheck(offer)
        if reason:
            await self._reject(writer, offer, reason)
            return

        self._active.add(offer.transfer_id)
        tmp_path = self._tmp_dir / f"{offer.transfer_id}.part"
        try:
            self._emit("offer", offer)
            try:
                accepted = await asyncio.wait_for(self._decide(offer), self._decide_timeout)
            except asyncio.TimeoutError:
                accepted = False
            except Exception:
                log.exception("decide callback raised; treating as declined")
                accepted = False
            if not accepted:
                await self._reject(writer, offer, "declined")
                return
            await send_message(writer, {"type": "accept", "receiver_name": self._device_name})
            self._emit("accepted", offer)
            await self._receive_body(reader, writer, offer, tmp_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason, message = _classify(exc)
            log.info("transfer %s failed: %s", offer.transfer_id, message)
            self._emit("failed", offer, message=reason)
            await self._try_send(writer, {"type": "result", "ok": False, "reason": reason})
        finally:
            self._active.discard(offer.transfer_id)
            _unlink_quiet(tmp_path)   # no half-finished file is ever left behind

    async def _receive_body(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                            offer: IncomingOffer, tmp_path: Path) -> None:
        hasher = hashlib.sha256()
        received = 0
        last_emit = 0.0
        handle = open(tmp_path, "wb")
        try:
            while received < offer.size:
                want = min(CHUNK_SIZE, offer.size - received)
                chunk = await asyncio.wait_for(reader.readexactly(want), self._idle_timeout)
                await asyncio.to_thread(_write_and_hash, handle, hasher, chunk)
                received += want
                now = time.monotonic()
                if now - last_emit >= PROGRESS_INTERVAL or received == offer.size:
                    last_emit = now
                    self._emit("progress", offer, bytes_done=received)
        finally:
            handle.close()

        done = await read_message(reader, self._idle_timeout)
        claimed = done.get("sha256")
        if done.get("type") != "done" or not (isinstance(claimed, str)
                                              and _SHA256_PATTERN.match(claimed.lower())):
            raise ProtocolError("expected a 'done' message with a SHA-256 hash")
        self._emit("verifying", offer, bytes_done=received)
        if not hmac.compare_digest(claimed.lower(), hasher.hexdigest()):
            raise TransferFailed("hash_mismatch", "file contents do not match the sender's hash")

        destination = await asyncio.to_thread(_finalize, tmp_path, self._dest_dir, offer.filename)
        await send_message(writer, {"type": "result", "ok": True, "saved_as": destination.name})
        self._emit("completed", offer, bytes_done=received, path=str(destination))
