"""Cash register certificate (pokladni certifikat) with its private key."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from .exceptions import ClientError

StrPath = str | Path


class Certificate:
    """Use the factories from_pkcs12() and from_pem(). The private key never leaves this object."""

    def __init__(self, private_key: object, certificate: x509.Certificate) -> None:
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ClientError("EET requires an RSA private key.")

        if private_key.public_key().public_numbers() != _public_numbers(certificate):
            raise ClientError("The private key does not belong to the certificate.")

        self._private_key = private_key
        self._certificate = certificate

    @classmethod
    def from_pkcs12(cls, path: StrPath, password: str | bytes | None) -> Certificate:
        """Load a .p12 file as issued by CA EET, including the legacy cipher of the playground files."""
        content = _read_file(path)
        try:
            private_key, certificate, _ = pkcs12.load_key_and_certificates(content, _to_bytes(password))
        except (ValueError, UnsupportedAlgorithm) as e:
            raise ClientError(f"Cannot read PKCS#12 file {path}: {e}") from e

        if private_key is None or certificate is None:
            raise ClientError(f"PKCS#12 file {path} does not contain a private key and a certificate.")

        return cls(private_key, certificate)

    @classmethod
    def from_pem(cls, key_path: StrPath, certificate_path: StrPath, passphrase: str | bytes | None = None) -> Certificate:
        """Load a PEM private key and a PEM certificate."""
        key = _read_file(key_path)
        try:
            private_key = serialization.load_pem_private_key(key, _to_bytes(passphrase))
        except (ValueError, TypeError, UnsupportedAlgorithm) as e:
            raise ClientError(f"Cannot read the private key from {key_path}: {e}") from e

        content = _read_file(certificate_path)
        try:
            certificate = x509.load_pem_x509_certificate(content)
        except ValueError as e:
            raise ClientError(f"Cannot read the certificate from {certificate_path}: {e}") from e

        return cls(private_key, certificate)

    def sign(self, data: bytes) -> bytes:
        """Sign data with RSA-SHA256."""
        return self._private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())

    @property
    def base64(self) -> str:
        """DER certificate in base64, the form used by wsse:BinarySecurityToken."""
        der = self._certificate.public_bytes(serialization.Encoding.DER)
        return base64.b64encode(der).decode("ascii")

    @property
    def eic(self) -> str:
        """EIC of the taxpayer, stored as the certificate common name."""
        names = self._certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        return str(names[0].value) if names else ""

    @property
    def valid_to(self) -> datetime:
        return self._certificate.not_valid_after_utc

    def __repr__(self) -> str:
        return f"Certificate(eic={self.eic!r}, valid_to={self.valid_to.isoformat()!r})"


def _public_numbers(certificate: x509.Certificate) -> object:
    public_key = certificate.public_key()
    return public_key.public_numbers() if isinstance(public_key, rsa.RSAPublicKey) else None


def _read_file(path: StrPath) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as e:
        raise ClientError(f"Cannot read file {path}: {e.strerror}") from e


def _to_bytes(password: str | bytes | None) -> bytes | None:
    return password.encode("utf-8") if isinstance(password, str) else password
