"""Joining side of pairing: connect to a device that showed a pairing string."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import ssl
from dataclasses import dataclass

from aircatch.pairing.payload import PairingPayload
from aircatch.pairing.session import compute_proof
from aircatch.security.identity import (DeviceIdentity, clean_device_name, fingerprint_from_der,
                                        pem_from_der)
from aircatch.security.tls import build_client_context
from aircatch.security.trust import TrustedPeer, TrustStore
from aircatch.transfer.protocol import STREAM_LIMIT, ProtocolError, read_message, send_message


@dataclass(frozen=True)
class PairResult:
    ok: bool
    reason: str = ""
    peer: TrustedPeer | None = None


async def pair_with(payload: PairingPayload, identity: DeviceIdentity, trust: TrustStore, *,
                    connect_timeout: float = 10.0, approval_timeout: float = 120.0) -> PairResult:
    """Pair with the device that produced `payload`. On success both sides trust each other."""
    ctx = build_client_context(identity=None, server_cert_pem=None)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(payload.host, payload.port, ssl=ctx, server_hostname="aircatch",
                                    limit=STREAM_LIMIT), connect_timeout)
    except ssl.SSLError:
        return PairResult(False, "tls_error")
    except (OSError, asyncio.TimeoutError):
        return PairResult(False, "could_not_connect")

    try:
        ssl_object = writer.get_extra_info("ssl_object")
        der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        if not der:
            return PairResult(False, "tls_error")
        host_fp = fingerprint_from_der(der)
        # The QR code is the trusted channel: the host must prove it owns that certificate.
        if not hmac.compare_digest(host_fp, payload.fingerprint):
            return PairResult(False, "fingerprint_mismatch")

        proof = compute_proof(payload.token, host_fp, identity.fingerprint)
        await send_message(writer, {"type": "pair_request", "version": 1,
                                    "device_name": identity.name, "cert": identity.cert_pem,
                                    "proof": proof})
        reply = await read_message(reader, approval_timeout)
        if reply.get("type") == "pair_reject":
            return PairResult(False, str(reply.get("reason", "rejected"))[:50])
        if reply.get("type") != "pair_accept":
            return PairResult(False, "protocol_error")
        name = clean_device_name(str(reply.get("device_name", "")), payload.name or "AirCatch Device")
        peer = TrustedPeer(host_fp, name, pem_from_der(der), "")
        trust.add(peer)
        return PairResult(True, "", trust.get(host_fp))
    except ProtocolError as exc:
        return PairResult(False, "timeout" if "timed out" in str(exc) else "connection_lost")
    except ssl.SSLError:
        return PairResult(False, "tls_error")
    except (OSError, asyncio.TimeoutError):
        return PairResult(False, "connection_lost")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
