"""Verification of the WS-Security signature of a SOAP message with exactly one signed element, soap:Body.

Only the algorithms prescribed by the EET interface are accepted and the signature must point
to the same soap:Body the application reads from, which rules out signature wrapping.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509 import verification
from cryptography.x509.oid import NameOID
from lxml import etree

from . import namespaces as ns
from .exceptions import ClientError, ResponseVerificationError
from .xmlutil import canonicalize

_ID_MASK = re.compile(r"[A-Za-z0-9_.-]+")
_PLAYGROUND_ORGANIZATION = "Generální finanční ředitelství"


class ResponseVerifier:
    def __init__(self, ca_file: str | Path | None = None, expected_organization: str | None = None) -> None:
        """Set the trust requirements of the signing certificate.

        ca_file is a PEM bundle with the issuers of the signing certificate including the root,
        None skips the trust check. expected_organization is the required O of the signing
        certificate, None skips the check.
        """
        self._expected_organization = expected_organization
        self._store: verification.Store | None = None
        self._intermediates: list[x509.Certificate] = []

        if ca_file is not None:
            self._load_bundle(ca_file)

    @classmethod
    def for_playground(cls) -> ResponseVerifier:
        """Verifier for responses of the playground environment."""
        bundle = resources.files("eet").joinpath("resources/ica-signing-ca.pem")
        with resources.as_file(bundle) as path:
            return cls(path, _PLAYGROUND_ORGANIZATION)

    def verify(self, envelope: etree._Element) -> None:
        """Raise ResponseVerificationError unless the message is signed by a trusted certificate."""
        self.verify_certificate(self.verify_signature(envelope))

    def verify_signature(self, envelope: etree._Element) -> x509.Certificate:
        """Check the signature and return the certificate which created it."""
        signature = _single(envelope, "/s:Envelope/s:Header/wsse:Security/ds:Signature", "Signature")
        signed_info = _single(signature, "ds:SignedInfo", "SignedInfo")

        _assert_algorithm(signed_info, "ds:CanonicalizationMethod", ns.EXC_C14N)
        _assert_algorithm(signed_info, "ds:SignatureMethod", ns.ALGO_RSA_SHA256)

        reference = _single(signed_info, "ds:Reference", "Reference")
        _assert_algorithm(reference, "ds:Transforms/ds:Transform", ns.EXC_C14N)
        _assert_algorithm(reference, "ds:DigestMethod", ns.ALGO_SHA256)

        body = _single(envelope, "/s:Envelope/s:Body", "Body")
        _assert_references_body(reference, body)

        digest = hashlib.sha256(_canonicalize(body, _prefixes(reference, "ds:Transforms/ds:Transform"))).digest()
        expected_digest = _decode_base64(_single(reference, "ds:DigestValue", "DigestValue"))
        if expected_digest is None or not hmac.compare_digest(expected_digest, digest):
            raise ResponseVerificationError("The digest of soap:Body does not match the signature.")

        certificate = _read_certificate(signature)

        signature_value = _decode_base64(_single(signature, "ds:SignatureValue", "SignatureValue"))
        signed_data = _canonicalize(signed_info, _prefixes(signed_info, "ds:CanonicalizationMethod"))
        try:
            public_key = certificate.public_key()
        except (UnsupportedAlgorithm, ValueError) as e:
            raise ResponseVerificationError("The embedded certificate has an unusable public key.") from e
        if signature_value is None or not isinstance(public_key, rsa.RSAPublicKey):
            raise ResponseVerificationError("The signature of the message is not valid.")

        try:
            public_key.verify(signature_value, signed_data, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as e:
            raise ResponseVerificationError("The signature of the message is not valid.") from e

        return certificate

    def verify_certificate(self, certificate: x509.Certificate, at: datetime | None = None) -> None:
        """Check the chain, validity at the given moment (now by default) and owner of the certificate."""
        if self._store is not None:
            moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
            # The signing certificate is neither a TLS server nor a TLS client, so only the rules
            # for the certificate authorities stay in force. Its key usage is up to the issuer.
            verifier = (
                verification.PolicyBuilder()
                .store(self._store)
                .time(moment)
                .extension_policies(
                    ca_policy=verification.ExtensionPolicy.webpki_defaults_ca(),
                    ee_policy=verification.ExtensionPolicy.permit_all(),
                )
                .build_client_verifier()
            )
            try:
                verifier.verify(certificate, self._intermediates)
            except (verification.VerificationError, ValueError) as e:
                raise ResponseVerificationError(
                    "The signing certificate is expired or issued by an untrusted authority."
                ) from e

        if self._expected_organization is not None:
            organizations = [
                attribute.value for attribute in certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
            ]
            if organizations != [self._expected_organization]:
                raise ResponseVerificationError(
                    f"The signing certificate belongs to an unexpected organization: {organizations!r}"
                )

    def _load_bundle(self, ca_file: str | Path) -> None:
        try:
            certificates = x509.load_pem_x509_certificates(Path(ca_file).read_bytes())
        except (OSError, ValueError) as e:
            raise ClientError(f"Cannot read the certificate bundle {ca_file}: {e}") from e

        roots = [c for c in certificates if c.subject == c.issuer]
        if not roots:
            raise ClientError(f"The certificate bundle {ca_file} does not contain a root certificate.")

        self._store = verification.Store(roots)
        self._intermediates = [c for c in certificates if c.subject != c.issuer]


def _canonicalize(element: etree._Element, inclusive_prefixes: list[str] | None) -> bytes:
    try:
        return canonicalize(element, inclusive_prefixes)
    except etree.LxmlError as e:
        raise ResponseVerificationError("The signed content cannot be canonicalized.") from e


def _assert_references_body(reference: etree._Element, body: etree._Element) -> None:
    body_id = body.get(f"{{{ns.WSU}}}Id", "")
    if not _ID_MASK.fullmatch(body_id) or reference.get("URI") != f"#{body_id}":
        raise ResponseVerificationError("The signature does not cover soap:Body.")

    holders = body.xpath("//@wsu:Id[. = $id] | //@Id[. = $id]", namespaces=ns.XPATH_PREFIXES, id=body_id)
    if len(holders) != 1:
        raise ResponseVerificationError("The identifier of soap:Body is not unique.")


def _read_certificate(signature: etree._Element) -> x509.Certificate:
    token_link = _single(signature, "ds:KeyInfo/wsse:SecurityTokenReference/wsse:Reference", "SecurityTokenReference")
    uri = token_link.get("URI", "")
    if not uri.startswith("#"):
        raise ResponseVerificationError("The signature does not reference an embedded certificate.")

    tokens = signature.xpath(
        "/s:Envelope/s:Header/wsse:Security/wsse:BinarySecurityToken", namespaces=ns.XPATH_PREFIXES
    )
    for token in tokens:
        if token.get(f"{{{ns.WSU}}}Id") == uri[1:]:
            der = _decode_base64(token)
            try:
                if der is None:
                    raise ValueError("invalid base64")
                return x509.load_der_x509_certificate(der)
            except ValueError as e:
                raise ResponseVerificationError("The embedded certificate cannot be read.") from e

    raise ResponseVerificationError("The certificate referenced by the signature was not found.")


def _assert_algorithm(context: etree._Element, query: str, expected: str) -> None:
    element = _single(context, query, query)
    algorithm = element.get("Algorithm")
    if algorithm != expected:
        raise ResponseVerificationError(f"Unsupported algorithm in {query}: {algorithm}")


def _prefixes(context: etree._Element, query: str) -> list[str] | None:
    lists = context.xpath(f"{query}/ec:InclusiveNamespaces/@PrefixList", namespaces=ns.XPATH_PREFIXES)
    return (str(lists[0]).split() or None) if lists else None


def _single(context: etree._Element, query: str, name: str) -> etree._Element:
    nodes = context.xpath(query, namespaces=ns.XPATH_PREFIXES)
    if len(nodes) != 1:
        raise ResponseVerificationError(f"The message must contain exactly one {name}.")
    return nodes[0]


def _decode_base64(element: etree._Element) -> bytes | None:
    text = re.sub(r"\s+", "", str(element.xpath("string()")))
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
