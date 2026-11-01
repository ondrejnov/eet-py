import base64
import hashlib
import re
from datetime import datetime, timezone

import pytest
from cryptography import x509
from helpers import CA_BUNDLE, FIXTURES, GFR, create_key_pair
from lxml import etree

from eet import ClientError, ResponseVerificationError, ResponseVerifier
from eet import namespaces as ns
from eet.xmlutil import canonicalize

CONFIRMATION = (FIXTURES / "playground-confirmation.xml").read_bytes()
POK = b"c8e6e4cf-92bf-46ee-80fe-986a62bcad2a-ff"
FORGED_POK = "00000000-92bf-46ee-80fe-986a62bcad2a-ff"
#: The signing certificate of the fixture is valid from 2026-06-08 to 2027-06-08.
WITHIN_VALIDITY = datetime(2026, 11, 1, tzinfo=timezone.utc)

verifier = ResponseVerifier()


def signing_certificate() -> x509.Certificate:
    return verifier.verify_signature(etree.fromstring(CONFIRMATION))


def assert_rejected(xml, reason):
    root = xml if isinstance(xml, etree._Element) else etree.fromstring(xml)
    with pytest.raises(ResponseVerificationError, match=reason):
        verifier.verify_signature(root)


def find(root, namespace, name):
    return root.find(f".//{{{namespace}}}{name}")


def body_of(root):
    return root.find(f"{{{ns.SOAP}}}Body")


@pytest.mark.parametrize("eic", ["CZ00000019", "CZ683555118", "CZ8551015704"])
def test_official_sample_messages(eic):
    verifier.verify_signature(etree.fromstring((FIXTURES / f"{eic}.eet.v4.req.xml").read_bytes()))


def test_real_response_of_the_playground_is_trusted():
    trusted = ResponseVerifier.for_playground()
    trusted.verify_certificate(signing_certificate(), at=WITHIN_VALIDITY)


def test_expired_signing_certificate_is_refused():
    with pytest.raises(ResponseVerificationError, match="untrusted authority"):
        ResponseVerifier.for_playground().verify_certificate(
            signing_certificate(), at=datetime(2027, 6, 9, tzinfo=timezone.utc)
        )


def test_identity_of_the_signing_certificate():
    certificate = signing_certificate()
    ResponseVerifier(None, GFR).verify_certificate(certificate)
    with pytest.raises(ResponseVerificationError, match="unexpected organization"):
        ResponseVerifier(None, "Někdo jiný").verify_certificate(certificate)


def test_certificate_from_an_unknown_authority_with_the_expected_organization():
    _, attacker = create_key_pair("CZ00000019", GFR)
    with pytest.raises(ResponseVerificationError, match="untrusted authority"):
        ResponseVerifier(CA_BUNDLE, GFR).verify_certificate(attacker)


def test_unusable_certificate_bundle(tmp_path):
    with pytest.raises(ClientError, match="Cannot read"):
        ResponseVerifier("/nonexistent")

    intermediate_only = tmp_path / "intermediate.pem"
    intermediate_only.write_bytes(CA_BUNDLE.read_bytes().split(b"-----END CERTIFICATE-----")[0] + b"-----END CERTIFICATE-----\n")
    with pytest.raises(ClientError, match="root certificate"):
        ResponseVerifier(intermediate_only)


def test_changed_content():
    assert_rejected(CONFIRMATION.replace(POK, FORGED_POK.encode()), "digest")
    assert_rejected(CONFIRMATION.replace(b'test="true"', b'test="false"'), "digest")


def test_changed_signature_value():
    assert_rejected(CONFIRMATION.replace(b"<SignatureValue>Ki5g", b"<SignatureValue>AAAA"), "signature")


def test_changed_signed_info():
    root = etree.fromstring(CONFIRMATION)
    body = body_of(root)
    find(body, ns.EET, "Potvrzeni").set("pok", FORGED_POK)
    digest = hashlib.sha256(canonicalize(body, None)).digest()
    find(root, ns.DS, "DigestValue").text = base64.b64encode(digest).decode()
    assert_rejected(root, "signature")


def test_invalid_base64():
    assert_rejected(
        re.sub(rb"<DigestValue>[^<]*</DigestValue>", b"<DigestValue>***</DigestValue>", CONFIRMATION), "digest"
    )
    assert_rejected(
        re.sub(rb"<SignatureValue>[^<]*</SignatureValue>", b"<SignatureValue>***</SignatureValue>", CONFIRMATION),
        "signature",
    )


def test_relative_namespace_in_the_body_cannot_be_canonicalized():
    tampered = CONFIRMATION.replace(b"</eet:Odpoved>", b'<r:x xmlns:r="rel/ns"/></eet:Odpoved>')
    assert tampered != CONFIRMATION
    assert_rejected(tampered, "canonicalized")


def test_relative_namespace_in_signed_info_cannot_be_canonicalized():
    root = etree.fromstring(CONFIRMATION)
    etree.SubElement(find(root, ns.DS, "SignedInfo"), "{rel/ns}x", nsmap={"r": "rel/ns"})
    assert_rejected(root, "canonicalized")


def test_certificate_with_an_unknown_key_algorithm():
    rsa_encryption = bytes.fromhex("06092A864886F70D010101")
    unknown_algorithm = bytes.fromhex("06092A864886F70D01017F")
    root = etree.fromstring(CONFIRMATION)
    token = find(root, ns.WSSE, "BinarySecurityToken")
    der = base64.b64decode(token.text)
    assert rsa_encryption in der
    token.text = base64.b64encode(der.replace(rsa_encryption, unknown_algorithm)).decode()

    assert_rejected(root, "public key")


def test_unsigned_message():
    assert_rejected((FIXTURES / "playground-verification.xml").read_bytes(), "exactly one Signature")


def test_weak_algorithms():
    assert_rejected(
        CONFIRMATION.replace(ns.ALGO_RSA_SHA256.encode(), b"http://www.w3.org/2000/09/xmldsig#rsa-sha1"),
        "Unsupported algorithm",
    )
    assert_rejected(
        CONFIRMATION.replace(ns.ALGO_SHA256.encode(), b"http://www.w3.org/2000/09/xmldsig#sha1"),
        "Unsupported algorithm",
    )


def test_signature_wrapping_valid_body_hidden_in_the_header():
    root = etree.fromstring(CONFIRMATION)
    signed_body = body_of(root)
    forged = etree.fromstring(etree.tostring(signed_body))
    del forged.attrib[f"{{{ns.WSU}}}Id"]
    find(forged, ns.EET, "Potvrzeni").set("pok", FORGED_POK)
    root.replace(signed_body, forged)
    root.find(f"{{{ns.SOAP}}}Header").append(signed_body)
    assert_rejected(root, "does not cover soap:Body")


def test_signature_wrapping_second_body_with_the_same_identifier():
    root = etree.fromstring(CONFIRMATION)
    root.append(etree.fromstring(etree.tostring(body_of(root))))
    assert_rejected(root, "exactly one Body")


def test_signature_wrapping_other_element_with_the_identifier_of_the_body():
    root = etree.fromstring(CONFIRMATION)
    decoy = etree.SubElement(root.find(f"{{{ns.SOAP}}}Header"), f"{{{ns.EET}}}Decoy")
    decoy.set(f"{{{ns.WSU}}}Id", body_of(root).get(f"{{{ns.WSU}}}Id"))
    assert_rejected(root, "not unique")


def test_second_signature():
    root = etree.fromstring(CONFIRMATION)
    signature = find(root, ns.DS, "Signature")
    signature.getparent().append(etree.fromstring(etree.tostring(signature)))
    assert_rejected(root, "exactly one Signature")


def test_certificate_reference_pointing_outside_of_the_message():
    assert_rejected(
        CONFIRMATION.replace(b'<wsse:Reference URI="#SecurityToken', b'<wsse:Reference URI="http://evil/SecurityToken'),
        "embedded certificate",
    )
    assert_rejected(
        CONFIRMATION.replace(b'<wsse:Reference URI="#SecurityToken', b'<wsse:Reference URI="#Missing'),
        "not found",
    )
