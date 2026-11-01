"""WS-Security signature required by chapter 5.2 of the interface description.

Only soap:Body is signed (exclusive C14N, SHA-256, RSA-SHA256), the certificate is attached
as wsse:BinarySecurityToken and no other headers (Timestamp, WS-Addressing) are present.

The structure follows the sample messages published with the specification.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

from lxml import etree

from . import namespaces as ns
from .certificate import Certificate
from .xmlutil import canonicalize, parse_xml

_SIGNED_INFO_PREFIXES = ["soapenv", "v4"]
_BODY_PREFIXES = ["v4"]


def sign_message(envelope: etree._Element, certificate: Certificate) -> etree._Element:
    """Return a copy of the envelope with the signature in soap:Header."""
    header = envelope.find(f"{{{ns.SOAP}}}Header")
    body = envelope.find(f"{{{ns.SOAP}}}Body")
    if header is None or body is None:
        raise ValueError("The envelope must contain soap:Header and soap:Body.")

    suffix = secrets.token_hex(12).upper()
    token_id = f"X509-{suffix}"
    body_id = f"id-{suffix}"

    security = etree.SubElement(header, _name(ns.WSSE, "Security"), nsmap={"wsse": ns.WSSE, "wsu": ns.WSU})
    security.set(_name(ns.SOAP, "mustUnderstand"), "1")

    token = etree.SubElement(security, _name(ns.WSSE, "BinarySecurityToken"))
    token.set("EncodingType", ns.TOKEN_ENCODING)
    token.set("ValueType", ns.TOKEN_TYPE)
    token.set(_name(ns.WSU, "Id"), token_id)
    token.text = certificate.base64

    signature = etree.SubElement(security, _name(ns.DS, "Signature"), nsmap={"ds": ns.DS})
    signature.set("Id", f"SIG-{suffix}")

    signed_info = _ds(signature, "SignedInfo")

    canonicalization = _ds(signed_info, "CanonicalizationMethod", Algorithm=ns.EXC_C14N)
    _inclusive_namespaces(canonicalization, _SIGNED_INFO_PREFIXES)

    _ds(signed_info, "SignatureMethod", Algorithm=ns.ALGO_RSA_SHA256)

    reference = _ds(signed_info, "Reference", URI=f"#{body_id}")
    transforms = _ds(reference, "Transforms")
    transform = _ds(transforms, "Transform", Algorithm=ns.EXC_C14N)
    _inclusive_namespaces(transform, _BODY_PREFIXES)
    _ds(reference, "DigestMethod", Algorithm=ns.ALGO_SHA256)

    # wsu:Id needs its prefix declared on soap:Body itself, the header is not in its scope.
    etree.cleanup_namespaces(body, top_nsmap={"wsu": ns.WSU}, keep_ns_prefixes=["wsu"])
    body.set(_name(ns.WSU, "Id"), body_id)

    _ds(reference, "DigestValue")
    _ds(signature, "SignatureValue")

    key_info = _ds(signature, "KeyInfo", Id=f"KI-{suffix}")

    token_reference = etree.SubElement(key_info, _name(ns.WSSE, "SecurityTokenReference"))
    token_reference.set(_name(ns.WSU, "Id"), f"STR-{suffix}")

    token_link = etree.SubElement(token_reference, _name(ns.WSSE, "Reference"))
    token_link.set("URI", f"#{token_id}")
    token_link.set("ValueType", ns.TOKEN_TYPE)

    # The canonical forms have to be computed from a parsed document. lxml silently drops inclusive prefixes
    # missing in the dictionary of the document, where only a parser puts them. In a document built by hand
    # the result would depend on what had been parsed before and would differ from what the server computes.
    signed = parse_xml(etree.tostring(envelope))

    digest = hashlib.sha256(canonicalize(_find(signed, ns.SOAP, "Body"), _BODY_PREFIXES)).digest()
    _find(signed, ns.DS, "DigestValue").text = base64.b64encode(digest).decode("ascii")

    signature_value = certificate.sign(canonicalize(_find(signed, ns.DS, "SignedInfo"), _SIGNED_INFO_PREFIXES))
    _find(signed, ns.DS, "SignatureValue").text = base64.b64encode(signature_value).decode("ascii")

    return signed


def _name(namespace: str, local_name: str) -> str:
    return f"{{{namespace}}}{local_name}"


def _find(root: etree._Element, namespace: str, local_name: str) -> etree._Element:
    element = root.find(f".//{_name(namespace, local_name)}")
    assert element is not None
    return element


def _ds(parent: etree._Element, local_name: str, **attributes: str) -> etree._Element:
    element = etree.SubElement(parent, _name(ns.DS, local_name))
    for name, value in attributes.items():
        element.set(name, value)
    return element


def _inclusive_namespaces(parent: etree._Element, prefixes: list[str]) -> None:
    element = etree.SubElement(parent, _name(ns.EXC_C14N, "InclusiveNamespaces"), nsmap={"ec": ns.EXC_C14N})
    element.set("PrefixList", " ".join(prefixes))
