"""AirCatch wire protocol, version 1. See docs/protocol.md for the full description.

Control messages are JSON objects, each prefixed by a 4-byte big-endian length.
File bytes are sent raw (no framing) between the 'accept' and 'done' messages.
"""
from __future__ import annotations

import asyncio
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
