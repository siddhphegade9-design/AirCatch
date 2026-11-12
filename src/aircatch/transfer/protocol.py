"""AirCatch wire protocol, version 1. See docs/protocol.md for the full description.

Control messages are JSON objects, each prefixed by a 4-byte big-endian length.
File bytes are sent raw (no framing) between the 'accept' and 'done' messages.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import struct

PROTOCOL_VERSION = 1
CHUNK_SIZE = 1024 * 1024            # 1 MiB per read/write: fast and low memory
STREAM_LIMIT = 2 * CHUNK_SIZE       # asyncio buffer size
MAX_CONTROL_MESSAGE = 64 * 1024     # control messages are tiny; refuse anything bigger


class ProtocolError(Exception):
    """The other side sent something invalid, or the connection broke."""


async def send_message(writer: asyncio.StreamWriter, message: dict) -> None:
    data = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_CONTROL_MESSAGE:
        raise ProtocolError("control message too large")
    writer.write(struct.pack(">I", len(data)) + data)
    await writer.drain()


async def read_message(reader: asyncio.StreamReader, timeout: float) -> dict:
    try:
        header = await asyncio.wait_for(reader.readexactly(4), timeout)
        (length,) = struct.unpack(">I", header)
        if length == 0 or length > MAX_CONTROL_MESSAGE:
            raise ProtocolError("invalid message length")
        body = await asyncio.wait_for(reader.readexactly(length), timeout)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("connection closed") from exc
    except asyncio.TimeoutError as exc:
        raise ProtocolError("timed out waiting for the other device") from exc
    except OSError as exc:   # connection reset, TLS errors, ...
        raise ProtocolError("connection lost") from exc
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("malformed message") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("malformed message")
    return message


# ---- optional preview image carried inside the offer (protocol v1, backward compatible) ----
MAX_THUMBNAIL_BYTES = 20 * 1024                      # raw JPEG/PNG bytes
MAX_THUMBNAIL_B64 = 4 * ((MAX_THUMBNAIL_BYTES + 2) // 3)
_JPEG_MAGIC, _PNG_MAGIC = b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n"


def encode_thumbnail(data: bytes | None) -> str | None:
    """Base64 text for the offer, or None when there is nothing usable to send."""
    if not data or len(data) > MAX_THUMBNAIL_BYTES:
        return None
    if not (data.startswith(_JPEG_MAGIC) or data.startswith(_PNG_MAGIC)):
        return None
    return base64.b64encode(data).decode("ascii")


def decode_thumbnail(value: object) -> bytes:
    """Validate a thumbnail received from the network. Anything doubtful becomes b'' (no preview).

    A bad thumbnail never rejects the transfer; it is simply ignored. The bytes are still
    untrusted: the UI must decode them with a bounded image reader.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_THUMBNAIL_B64:
        return b""
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return b""
    if not raw or len(raw) > MAX_THUMBNAIL_BYTES:
        return b""
    if not (raw.startswith(_JPEG_MAGIC) or raw.startswith(_PNG_MAGIC)):
        return b""
    return raw
