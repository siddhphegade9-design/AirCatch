"""Network address helpers for discovery."""
from __future__ import annotations

import ipaddress

import ifaddr

_SHARED_SPACE = ipaddress.ip_network("100.64.0.0/10")   # carrier-grade NAT / Tailscale style


def is_acceptable_address(text: str) -> bool:
    """Only local-network IPv4 addresses are ever used as a connection target.

    Discovery messages come from other devices on the network and are not trusted.
    Refusing public addresses means a rogue advertisement can never make us connect out to the internet.
    """
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if ip.version != 4 or ip.is_unspecified or ip.is_multicast:
        return False
    return ip.is_private or ip.is_link_local or ip in _SHARED_SPACE


def lan_ipv4_addresses() -> list[str]:
    """This PC's usable LAN addresses (no loopback, no 169.254.x.x self-assigned ones)."""
    found: set[str] = set()
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            address = ip.ip
            if (isinstance(address, str) and is_acceptable_address(address)
                    and not address.startswith("127.") and not address.startswith("169.254.")):
                found.add(address)
    return sorted(found)
