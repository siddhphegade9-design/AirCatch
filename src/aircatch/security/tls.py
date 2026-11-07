"""TLS settings. We use TLS 1.3 with certificates in both directions (mutual TLS)."""
from __future__ import annotations

import ssl

from aircatch.security.identity import DeviceIdentity


def _harden(ctx: ssl.SSLContext) -> None:
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    if hasattr(ssl, "VERIFY_X509_STRICT"):   # newer Pythons are stricter about self-signed certs
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT


def build_server_context(identity: DeviceIdentity, trusted_cert_pems: list[str]) -> ssl.SSLContext:
    """Receiver side.

    CERT_OPTIONAL: a client may show no certificate (that is how a *new* device asks to
    pair), but a client that shows a certificate must be one we already trust.
    Whether a peer is allowed to send files is decided again by the application,
    using the live trust list.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    _harden(ctx)
    ctx.load_cert_chain(str(identity.cert_path), str(identity.key_path))
    ctx.verify_mode = ssl.CERT_OPTIONAL
    for pem in trusted_cert_pems:
        ctx.load_verify_locations(cadata=pem)
    return ctx


def build_client_context(identity: DeviceIdentity | None,
                         server_cert_pem: str | None) -> ssl.SSLContext:
    """Sender side.

    server_cert_pem given: the server must present exactly that (pinned) certificate.
    server_cert_pem None: used only while pairing, where the server's fingerprint is
    checked by hand against the one in the QR code / pairing string.
    identity None: present no certificate (pairing).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _harden(ctx)
    ctx.check_hostname = False   # we pin certificates; we do not use host names
    if server_cert_pem is not None:
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=server_cert_pem)
    else:
        ctx.verify_mode = ssl.CERT_NONE
    if identity is not None:
        ctx.load_cert_chain(str(identity.cert_path), str(identity.key_path))
    return ctx
