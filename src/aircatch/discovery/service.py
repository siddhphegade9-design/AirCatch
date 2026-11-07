"""Advertising this device on the local network (mDNS / Bonjour / Android NSD compatible).

Service type: _aircatch._tcp.local.   TXT records: v=1, h=<discovery hint>, n=<device name>

Privacy: we do NOT broadcast the device ID. We broadcast a *hint*, a one-way hash of it.
A paired device can compute the hint for each of its paired peers and recognise them;
a stranger learns only the hint and the display name.
Security: discovery only tells us WHERE a device might be. Who it really is gets checked
by TLS certificate pinning when we connect, so a fake advertisement cannot impersonate anyone.
"""
from __future__ import annotations

import hashlib
import re
import socket
from dataclasses import dataclass

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from aircatch.discovery.net import is_acceptable_address, lan_ipv4_addresses
from aircatch.security.identity import DeviceIdentity, clean_device_name

SERVICE_TYPE = "_aircatch._tcp.local."
ADVERT_VERSION = "1"
_HINT = re.compile(r"^[0-9a-f]{16}$")


class DiscoveryError(Exception):
    pass


def discovery_hint(device_id: str) -> str:
    """16 hex characters derived from a device ID; cannot be turned back into the ID."""
    return hashlib.sha256(b"aircatch-discovery-v1" + bytes.fromhex(device_id)).hexdigest()[:16]


@dataclass(frozen=True)
class Advertisement:
    service_name: str
    name: str
    hint: str
    addresses: tuple[str, ...]
    port: int


def parse_advertisement(service_name: str, properties: dict, addresses: list[str],
                        port: int | None) -> Advertisement | None:
    """Turn raw network data into an Advertisement, or None if anything looks wrong."""

    def text(key: bytes, limit: int) -> str | None:
        value = properties.get(key)
        if not isinstance(value, bytes) or len(value) > limit:
            return None
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None

    if text(b"v", 8) != ADVERT_VERSION:
        return None
    hint, raw_name = text(b"h", 16), text(b"n", 240)
    if hint is None or not _HINT.match(hint) or raw_name is None:
        return None
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    good = tuple(a for a in addresses if is_acceptable_address(a))
    if not good:
        return None
    return Advertisement(service_name[:200], clean_device_name(raw_name), hint, good, port)


class Advertiser:
    """Makes this device visible to others on the same network while the receiver runs."""

    def __init__(self, identity: DeviceIdentity, port: int, *, addresses: list[str] | None = None,
                 zeroconf: AsyncZeroconf | None = None) -> None:
        self._identity = identity
        self._port = port
        self._addresses = addresses
        self._azc = zeroconf
        self._owns_zeroconf = zeroconf is None
        self._info: ServiceInfo | None = None

    async def start(self) -> None:
        addresses = self._addresses if self._addresses is not None else lan_ipv4_addresses()
        if not addresses:
            raise DiscoveryError("no local network address found to advertise")
        hint = discovery_hint(self._identity.fingerprint)
        if self._azc is None:
            self._azc = AsyncZeroconf()
        self._info = ServiceInfo(
            SERVICE_TYPE, f"AirCatch-{hint[:8]}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(a) for a in addresses], port=self._port,
            properties={"v": ADVERT_VERSION, "h": hint, "n": self._identity.name},
            server=f"aircatch-{hint[:8]}.local.")
        await self._azc.async_register_service(self._info, allow_name_change=True)

    async def stop(self) -> None:
        if self._azc is None:
            return
        try:
            if self._info is not None:
                await self._azc.async_unregister_service(self._info)
        finally:
            if self._owns_zeroconf:
                await self._azc.async_close()
            self._azc = None
            self._info = None
