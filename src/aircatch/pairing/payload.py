"""The pairing string that goes into a QR code (or can be copied and pasted).

aircatch://pair?v=1&h=<address>&p=<port>&f=<fingerprint>&t=<one-time token>&n=<name>
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

_FP = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_HOST = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")


class PayloadError(ValueError):
    pass


@dataclass(frozen=True)
class PairingPayload:
    host: str
    port: int
    fingerprint: str   # fingerprint of the host's certificate
    token: str         # one-time secret, shown only on the host's screen
    name: str = ""

    def to_uri(self) -> str:
        query = urlencode({"v": 1, "h": self.host, "p": self.port, "f": self.fingerprint,
                           "t": self.token, "n": self.name})
        return f"aircatch://pair?{query}"

    @staticmethod
    def parse(text: str) -> "PairingPayload":
        try:
            parts = urlsplit(text.strip())
            query = parse_qs(parts.query, strict_parsing=True)
        except ValueError as exc:
            raise PayloadError("not a pairing string") from exc
        if parts.scheme != "aircatch" or parts.netloc != "pair":
            raise PayloadError("not an AirCatch pairing string")

        def one(key: str) -> str:
            values = query.get(key)
            if not values or len(values) != 1:
                raise PayloadError(f"missing or repeated field '{key}'")
            return values[0]

        if one("v") != "1":
            raise PayloadError("unsupported pairing version")
        host, fingerprint, token = one("h"), one("f").lower(), one("t")
        if not _HOST.match(host):
            raise PayloadError("invalid address")
        try:
            port = int(one("p"))
        except ValueError as exc:
            raise PayloadError("invalid port") from exc
        if not 1 <= port <= 65535:
            raise PayloadError("invalid port")
        if not _FP.match(fingerprint):
            raise PayloadError("invalid fingerprint")
        if not _TOKEN.match(token):
            raise PayloadError("invalid token")
        name = query.get("n", [""])[0][:60]
        return PairingPayload(host, port, fingerprint, token, name)
