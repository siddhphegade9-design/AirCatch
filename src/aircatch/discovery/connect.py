"""Send to a device found by discovery, trying each of its addresses."""
from __future__ import annotations

from pathlib import Path

from aircatch.discovery.browser import DiscoveredDevice
from aircatch.security.identity import DeviceIdentity
from aircatch.transfer.sender import SendResult, send_file


async def send_to_discovered(device: DiscoveredDevice, path: str | Path, *,
                             identity: DeviceIdentity, port: int | None = None,
                             **kwargs) -> SendResult:
    """A device can have several addresses (Wi-Fi, Ethernet, ...). Try them in turn.

    Only 'could_not_connect' moves on to the next address: any other outcome (declined,
    not paired, wrong certificate, success) is an answer from the device and is final.
    """
    if device.peer is None:
        raise ValueError("can only send to a device we have paired with")
    last: SendResult | None = None
    for address in device.addresses:
        last = await send_file(address, port or device.port, path, identity=identity,
                               peer=device.peer, **kwargs)
        if last.reason != "could_not_connect":
            return last
    assert last is not None
    return last
