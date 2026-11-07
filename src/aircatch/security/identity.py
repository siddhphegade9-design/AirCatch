"""Device identity: every AirCatch device has its own key pair and self-signed certificate.

The device ID is the SHA-256 fingerprint of the certificate (64 hex characters). It is
what other devices remember when they pair with this one. We use ECDSA P-256 because
every platform (Windows, Android, iOS) supports it for TLS.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import hashlib

DEFAULT_DEVICE_NAME = "AirCatch Device"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class IdentityError(Exception):
    """The identity files are missing, incomplete or damaged."""


def clean_device_name(name: str, default: str = DEFAULT_DEVICE_NAME) -> str:
    """Make a name safe to display: no control characters, no extra spaces, max 60 chars."""
    name = _CONTROL_CHARS.sub("", name or "")
    name = " ".join(name.split())[:60].strip()
    return name or default


def fingerprint_from_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def normalize_cert_pem(pem: str) -> tuple[str, str]:
    """Validate a certificate received from another device.

    Returns (clean PEM text, fingerprint). Raises ValueError if it is not a usable
    AirCatch certificate (must be parseable and use an EC public key).
    """
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("not a valid certificate") from exc
    if not isinstance(cert.public_key(), ec.EllipticCurvePublicKey):
        raise ValueError("unsupported key type")
    der = cert.public_bytes(serialization.Encoding.DER)
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii"), fingerprint_from_der(der)


def pem_from_der(der: bytes) -> str:
    return ssl.DER_cert_to_PEM_cert(der)


def short_fingerprint(fingerprint: str) -> str:
    """Human-friendly start of a fingerprint, e.g. 'A1B2 C3D4 E5F6'."""
    head = fingerprint[:12].upper()
    return " ".join(head[i:i + 4] for i in range(0, 12, 4))


@dataclass(frozen=True)
class DeviceIdentity:
    name: str
    fingerprint: str      # device ID
    cert_pem: str
    cert_path: Path
    key_path: Path


def _write_private(path: Path, data: bytes) -> None:
    """Write a file readable only by the current user (on systems that support it)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def _generate() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AirCatch Device")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage(
            [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                       critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


def load_or_create(config_dir: str | Path, name: str | None = None) -> DeviceIdentity:
    """Load this device's identity, creating it on first run.

    Files live in <config_dir>/identity/. Different config dirs = different devices,
    which is how two instances can run on one PC for testing.
    """
    base = Path(config_dir) / "identity"
    base.mkdir(parents=True, exist_ok=True)
    key_path, cert_path, meta_path = base / "device.key", base / "device.crt", base / "identity.json"

    if key_path.exists() != cert_path.exists():
        raise IdentityError(f"incomplete identity in {base}: need both device.key and device.crt")
    if not key_path.exists():
        key_pem, cert_pem = _generate()
        _write_private(key_path, key_pem)
        _write_private(cert_path, cert_pem)

    try:
        cert_pem_text, fingerprint = normalize_cert_pem(cert_path.read_text("ascii"))
    except (OSError, ValueError) as exc:
        raise IdentityError(f"damaged certificate in {cert_path}") from exc

    stored = ""
    if meta_path.exists():
        try:
            stored = str(json.loads(meta_path.read_text("utf-8")).get("name", ""))
        except (OSError, ValueError):
            stored = ""
    if name:
        chosen = clean_device_name(name)
    else:
        chosen = clean_device_name(stored or socket.gethostname())
    if chosen != stored:
        meta_path.write_text(json.dumps({"name": chosen}), encoding="utf-8")
    return DeviceIdentity(chosen, fingerprint, cert_pem_text, cert_path, key_path)
