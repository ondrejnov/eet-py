import re
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from helpers import CERT_DIR, create_key_pair, write_key_pair

from eet import Certificate, ClientError


def test_from_pem(tmp_path):
    key_file, certificate_file = write_key_pair(tmp_path, "CZ8551015704")
    certificate = Certificate.from_pem(key_file, certificate_file)

    assert certificate.eic == "CZ8551015704"
    assert certificate.valid_to > datetime.now(timezone.utc)
    assert re.fullmatch(r"MII[A-Za-z0-9+/=]+", certificate.base64)


def test_private_key_never_appears_in_a_dump(tmp_path):
    certificate = Certificate.from_pem(*write_key_pair(tmp_path, "CZ8551015704"))
    dump = repr(certificate)

    assert "CZ8551015704" in dump
    assert "PRIVATE" not in dump
    assert "RSAPrivateKey" not in dump


def test_passphrase_protected_key(tmp_path):
    key_file, certificate_file = write_key_pair(tmp_path, passphrase=b"secret")

    assert Certificate.from_pem(key_file, certificate_file, "secret").eic == "CZ00000019"
    with pytest.raises(ClientError):
        Certificate.from_pem(key_file, certificate_file, "wrong")
    with pytest.raises(ClientError):
        Certificate.from_pem(key_file, certificate_file)


def test_key_of_a_different_certificate(tmp_path):
    key_file, _ = write_key_pair(tmp_path, salt=1)
    _, certificate_file = write_key_pair(tmp_path, salt=2)

    with pytest.raises(ClientError, match="does not belong"):
        Certificate.from_pem(key_file, certificate_file)


def test_only_rsa_keys_are_accepted():
    _, certificate = create_key_pair()
    with pytest.raises(ClientError, match="RSA"):
        Certificate(ec.generate_private_key(ec.SECP256R1()), certificate)


def test_missing_files(tmp_path):
    _, certificate_file = write_key_pair(tmp_path)

    with pytest.raises(ClientError, match="Cannot read file"):
        Certificate.from_pem("/nonexistent", certificate_file)
    with pytest.raises(ClientError, match="Cannot read file"):
        Certificate.from_pkcs12("/nonexistent", "x")


def test_pkcs12_with_a_modern_cipher(tmp_path):
    key, certificate = create_key_pair("CZ8551015704")
    p12 = tmp_path / "test.p12"
    p12.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"test", key, certificate, None, serialization.BestAvailableEncryption(b"secret")
        )
    )

    assert Certificate.from_pkcs12(p12, "secret").eic == "CZ8551015704"
    assert Certificate.from_pkcs12(p12, b"secret").eic == "CZ8551015704"
    with pytest.raises(ClientError, match="Cannot read PKCS#12"):
        Certificate.from_pkcs12(p12, "wrong")


@pytest.mark.parametrize("eic", ["CZ00000019", "CZ683555118", "CZ8551015704"])
def test_pkcs12_published_for_the_playground(eic):
    p12 = CERT_DIR / f"CA_EET-Playground-{eic}.p12"
    if not p12.is_file():
        pytest.skip("Run scripts/fetch_playground_certs.py first.")

    password = (CERT_DIR / "password_pokladni_cert_playground.txt").read_text().strip()
    assert Certificate.from_pkcs12(p12, password).eic == eic
