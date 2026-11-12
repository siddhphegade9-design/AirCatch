"""Send to a device found by discovery, trying each of its addresses."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from aircatch.discovery.browser import DiscoveredDevice
from aircatch.discovery.net import lan_ipv4_addresses
from aircatch.security.identity import DeviceIdentity
from aircatch.transfer.sender import SendResult, send_file

PER_ADDRESS_TIMEOUT = 5.0     # a dead address (virtual adapter, firewall drop) must not stall for 10 s each


def order_addresses(addresses: tuple[str, ...], mine: list[str] | None = None) -> list[str]:
    """Addresses on the same /24 network as one of ours come first: they are the likely reachable ones."""
    mine = lan_ipv4_addresses() if mine is None else mine
    local = {".".join(a.split(".")[:3]) for a in mine}
    return sorted(addresses, key=lambda a: ".".join(a.split(".")[:3]) not in local)


async def send_to_discovered(device: DiscoveredDevice, path: str | Path, *,
                             identity: DeviceIdentity, port: int | None = None,
                             on_step: Callable[[str], None] | None = None,
                             **kwargs) -> SendResult:
    """A device can have several addresses (Wi-Fi, Ethernet, ...). Try them in turn.

    Only 'could_not_connect' moves on to the next address: any other outcome (declined,
    not paired, wrong certificate, success) is an answer from the device and is final.
    """
    if device.peer is None:
        raise ValueError("can only send to a device we have paired with")
    addresses = order_addresses(device.addresses)
    if len(addresses) > 1:
        kwargs.setdefault("connect_timeout", PER_ADDRESS_TIMEOUT)
    last: SendResult | None = None
    for address in addresses:
        if on_step is not None:
            on_step(f"Connecting to {device.peer.name} at {address}...")
        last = await send_file(address, port or device.port, path, identity=identity,
                               peer=device.peer, **kwargs)
        if last.reason != "could_not_connect":
            return last
    assert last is not None
    return last
