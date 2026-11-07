"""Finding other AirCatch devices on the local network."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Callable

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from aircatch.discovery.service import (SERVICE_TYPE, Advertisement, discovery_hint,
                                        parse_advertisement)
from aircatch.security.identity import DeviceIdentity
from aircatch.security.trust import TrustedPeer, TrustStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredDevice:
    name: str                     # display name as advertised (not verified!)
    addresses: tuple[str, ...]
    port: int
    service_name: str
    peer: TrustedPeer | None      # set when this is a device we have paired with
    hint: str = ""                # the advertised discovery hint

    @property
    def paired(self) -> bool:
        return self.peer is not None


class Discovery:
    """Watches the network for AirCatch devices.

    Whether a found device is 'paired' is worked out from the live trusted list every time
    you ask, so pairing and unpairing show up immediately. The advertised name of an
    unpaired device is only a label; nothing is trusted because of it.
    """

    def __init__(self, trust: TrustStore, *, own_identity: DeviceIdentity | None = None,
                 zeroconf: AsyncZeroconf | None = None, max_entries: int = 100,
                 on_change: Callable[[], None] | None = None) -> None:
        self._trust = trust
        self._own_hint = discovery_hint(own_identity.fingerprint) if own_identity else None
        self._azc = zeroconf
        self._owns_zeroconf = zeroconf is None
        self._max_entries = max_entries
        self._on_change = on_change
        self._found: dict[str, Advertisement] = {}
        self._browser: AsyncServiceBrowser | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---------- lifecycle ----------
    async def start(self) -> None:
        if self._azc is None:
            self._azc = AsyncZeroconf()
        self._browser = AsyncServiceBrowser(self._azc.zeroconf, SERVICE_TYPE,
                                            handlers=[self._on_state_change])

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
        if self._azc is not None and self._owns_zeroconf:
            await self._azc.async_close()
        self._azc = None

    # ---------- network events ----------
    def _on_state_change(self, zeroconf, service_type, name, state_change) -> None:
        if state_change is ServiceStateChange.Removed:
            self.remove(name)
            return
        task = asyncio.ensure_future(self._resolve(name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resolve(self, name: str) -> None:
        if self._azc is None:
            return
        info = AsyncServiceInfo(SERVICE_TYPE, name)
        try:
            if not await info.async_request(self._azc.zeroconf, 3000):
                return
        except Exception:
            log.debug("could not resolve %s", name, exc_info=True)
            return
        advert = parse_advertisement(name, info.properties, info.parsed_addresses(IPVersion.V4Only),
                                     info.port)
        if advert is not None:
            self.ingest(advert)

    # ---------- state ----------
    def ingest(self, advert: Advertisement) -> None:
        """Record (or update) a device. Also the entry point used by tests."""
        if advert.hint == self._own_hint:
            return                                    # that is us
        if advert.service_name not in self._found and len(self._found) >= self._max_entries:
            return                                    # flood protection
        self._found[advert.service_name] = advert
        self._notify()

    def remove(self, service_name: str) -> None:
        if self._found.pop(service_name, None) is not None:
            self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change()

    def devices(self) -> list[DiscoveredDevice]:
        by_hint = {discovery_hint(p.device_id): p for p in self._trust.peers()}
        result = [DiscoveredDevice(a.name, a.addresses, a.port, a.service_name, by_hint.get(a.hint),
                                    a.hint)
                  for a in self._found.values()]
        return sorted(result, key=lambda d: (not d.paired, d.name.lower()))

    async def find(self, peer: TrustedPeer, timeout: float = 5.0) -> DiscoveredDevice | None:
        """Wait up to `timeout` seconds for a paired device to show up."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for device in self.devices():
                if device.peer is not None and device.peer.device_id == peer.device_id:
                    return device
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(0.1)
