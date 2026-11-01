"""Shared helpers of the tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from eet import Certificate, HttpResponse, Receipt
from eet import namespaces as ns
from eet.signer import sign_message

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CERT_DIR = ROOT / "_cert"
XSD_FILE = ROOT / "src" / "eet" / "schema" / "EETXMLSchema.xsd"
CA_BUNDLE = ROOT / "src" / "eet" / "resources" / "ica-signing-ca.pem"
GFR = "Generální finanční ředitelství"


@cache
def create_key_pair(common_name: str = "CZ00000019", organization: str = "Test", salt: int = 0):
    """Throw-away self-signed key pair, salt gives another key for the same names."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CZ"),
    ])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def write_key_pair(directory: Path, common_name: str = "CZ00000019", passphrase: bytes | None = None, salt: int = 0):
    """Write the pair to PEM files and return their paths."""
    key, certificate = create_key_pair(common_name, "Test", salt)
    encryption = (
        serialization.BestAvailableEncryption(passphrase) if passphrase else serialization.NoEncryption()
    )
    key_file = directory / f"{common_name}-{salt}-{bool(passphrase)}.key"
    certificate_file = directory / f"{common_name}-{salt}-{bool(passphrase)}.pem"
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption)
    )
    certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_file, certificate_file


def create_test_certificate(common_name: str = "CZ00000019", organization: str = "Test") -> Certificate:
    return Certificate(*create_key_pair(common_name, organization))


def create_receipt() -> Receipt:
    return Receipt(
        eic_popl="CZ00000019",
        id_jednotky=181,
        id_pokl="00/2535/CN58",
        porad_cis="0/2482/IE25",
        dat_trzby=datetime(2026, 11, 1, 12, 30, tzinfo=ZoneInfo("Europe/Prague")),
        celk_trzba=1000,
    )


def create_response_xml(content: str, signer: Certificate | None = None) -> bytes:
    """Envelope with the given content of Odpoved, signed by the given certificate when provided."""
    envelope = etree.fromstring(
        f'<soapenv:Envelope xmlns:soapenv="{ns.SOAP}" xmlns:v4="{ns.EET}"><soapenv:Header/>'
        f"<soapenv:Body><v4:Odpoved>{content}</v4:Odpoved></soapenv:Body></soapenv:Envelope>"
    )
    if signer is not None:
        envelope = sign_message(envelope, signer)
    return etree.tostring(envelope, encoding="UTF-8", xml_declaration=True)


Reply = HttpResponse | Exception | Callable[[bytes], HttpResponse]


@dataclass
class FakeTransport:
    """Transport with a prepared response. The response may depend on the sent message."""

    reply: Reply
    requests: list[dict] = field(default_factory=list)

    def post(self, url: str, body: bytes, soap_action: str) -> HttpResponse:
        self.requests.append({"url": url, "body": body, "action": soap_action})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply if isinstance(self.reply, HttpResponse) else self.reply(body)


def request_attribute(xml: bytes, element: str, attribute: str) -> str | None:
    """Read an attribute of the header or data element from a request."""
    return etree.fromstring(xml).find(f".//{{{ns.EET}}}{element}").get(attribute)
