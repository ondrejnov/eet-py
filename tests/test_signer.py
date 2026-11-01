import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from helpers import FIXTURES, XSD_FILE, create_receipt, create_test_certificate, request_attribute, write_key_pair
from lxml import etree

from eet import Dispatcher, ResponseVerifier
from eet import namespaces as ns


def skeleton(element, path=""):
    """Names of elements and attributes without their values, used to compare the message structure."""
    path += "/" + etree.QName(element).localname
    attributes = sorted(etree.QName(name).localname for name in element.attrib)
    lines = [f"{path}[{' '.join(attributes)}]"]
    for child in element:
        lines.extend(skeleton(child, path))
    return lines


def soap_header(xml):
    return etree.fromstring(xml).find(f"{{{ns.SOAP}}}Header")


def without_must_understand(lines):
    return [line.replace("[mustUnderstand", "[").replace(" mustUnderstand", "") for line in lines]


@pytest.fixture(scope="module")
def certificate():
    return create_test_certificate()


@pytest.fixture(scope="module")
def dispatcher(certificate):
    return Dispatcher(Dispatcher.PLAYGROUND_URL, certificate)


def full_receipt():
    receipt = create_receipt()
    receipt.uuid_zpravy = "b3a09b52-7c87-4014-a496-4c7a53cf9125"
    receipt.eic_poverujiciho = "CZ8551015704"
    receipt.povereni_vice_popl = False
    receipt.prvni_zaslani = False
    receipt.celk_trzba = -0.5
    receipt.urceno_cerp_zuct = 0
    receipt.cerp_zuct = 99999999.99
    return receipt


def test_prescribed_xml_declaration(dispatcher):
    xml = dispatcher.get_signed_message(create_receipt())
    assert xml.split(b"\n", 1)[0] == b'<?xml version="1.0" encoding="UTF-8"?>'


def test_signature_verifies_and_attached_certificate_is_the_signing_one(dispatcher, certificate):
    xml = dispatcher.get_signed_message(create_receipt())
    signer = ResponseVerifier().verify_signature(etree.fromstring(xml))
    assert signer.public_key().public_numbers() == certificate._private_key.public_key().public_numbers()


def test_only_the_body_is_signed_and_no_other_headers_are_present(dispatcher):
    root = etree.fromstring(dispatcher.get_signed_message(create_receipt()))
    prefixes = {"ds": ns.DS}

    assert len(root.xpath("//ds:Reference", namespaces=prefixes)) == 1
    assert len(root.xpath('/*/*[local-name()="Header"]/*')) == 1
    assert root.xpath('//*[local-name()="Timestamp"]') == []
    assert root.xpath('//*[contains(namespace-uri(), "addressing")]') == []


def test_prefixes_are_the_same_as_in_the_official_sample_messages(dispatcher):
    xml = dispatcher.get_signed_message(create_receipt())
    for fragment in (
        b"<soapenv:Envelope ",
        b"<wsse:Security ",
        b"<ds:Signature ",
        b"<ec:InclusiveNamespaces ",
        b"<soapenv:Body ",
        b'xmlns:wsu="' + ns.WSU.encode(),
        b"wsu:Id=",
        b"<v4:Trzba>",
    ):
        assert fragment in xml
    assert b"ns0" not in xml


def test_security_header_has_the_structure_of_the_official_sample_messages(dispatcher):
    official = (FIXTURES / "CZ00000019.eet.v4.req.xml").read_bytes()
    xml = dispatcher.get_signed_message(create_receipt())

    assert without_must_understand(skeleton(soap_header(official))) == without_must_understand(skeleton(soap_header(xml)))


def test_content_matches_the_published_xsd(dispatcher):
    schema = etree.XMLSchema(etree.parse(str(XSD_FILE)))
    for message in (dispatcher.get_signed_message(create_receipt()), dispatcher.get_signed_message(full_receipt(), True)):
        sale = etree.fromstring(message).find(f".//{{{ns.EET}}}Trzba")
        standalone = etree.fromstring(etree.tostring(sale))
        assert schema.validate(standalone), schema.error_log


def test_values_in_the_message(dispatcher):
    checked = dispatcher.get_signed_message(full_receipt(), True)

    assert request_attribute(checked, "Hlavicka", "uuid_zpravy") == "b3a09b52-7c87-4014-a496-4c7a53cf9125"
    assert request_attribute(checked, "Hlavicka", "prvni_zaslani") == "false"
    assert request_attribute(checked, "Hlavicka", "overeni") == "true"
    assert request_attribute(checked, "Data", "eic_poverujiciho") == "CZ8551015704"
    assert request_attribute(checked, "Data", "povereni_vice_popl") == "false"
    assert request_attribute(checked, "Data", "dat_trzby") == "2026-11-01T12:30:00+01:00"
    assert request_attribute(checked, "Data", "celk_trzba") == "-0.50"
    assert request_attribute(checked, "Data", "urceno_cerp_zuct") == "0.00"
    assert request_attribute(checked, "Data", "cerp_zuct") == "99999999.99"


def test_optional_items_without_a_value_are_not_present(dispatcher):
    plain = dispatcher.get_signed_message(create_receipt())

    assert request_attribute(plain, "Hlavicka", "prvni_zaslani") == "true"
    assert request_attribute(plain, "Data", "celk_trzba") == "1000.00"
    for item in ("eic_poverujiciho", "povereni_vice_popl", "urceno_cerp_zuct", "cerp_zuct"):
        assert request_attribute(plain, "Data", item) is None
    assert request_attribute(plain, "Hlavicka", "overeni") is None
    assert b'=""' not in plain


def test_send_time_is_in_the_czech_time_zone(dispatcher, monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    plain = dispatcher.get_signed_message(create_receipt())

    sent_at = datetime.fromisoformat(request_attribute(plain, "Hlavicka", "dat_odesl"))
    assert sent_at.utcoffset() == datetime.now(ZoneInfo("Europe/Prague")).utcoffset()


def test_longest_message_fits_into_the_limit_of_12_kb(dispatcher):
    longest = create_receipt()
    longest.eic_poverujiciho = "CZ8551015704"
    longest.povereni_vice_popl = True
    longest.id_pokl = "x" * 20
    longest.porad_cis = "x" * 25
    longest.celk_trzba = "-99999999.99"
    longest.urceno_cerp_zuct = "-99999999.99"
    longest.cerp_zuct = "-99999999.99"

    assert len(dispatcher.get_signed_message(longest, True)) < 12 * 1024


def test_signature_does_not_depend_on_documents_parsed_earlier(tmp_path):
    # lxml silently drops inclusive prefixes missing in the dictionary of the document, which documents share.
    # A message built from scratch was signed differently than the server verifies it, unless something
    # with the same prefixes had been parsed before, as the tests do. Only a new interpreter is free of that.
    key_file, certificate_file = write_key_pair(tmp_path)
    script = f"""
from datetime import datetime, timezone
from lxml import etree
from eet import Certificate, Dispatcher, Receipt, ResponseVerifier

certificate = Certificate.from_pem({str(key_file)!r}, {str(certificate_file)!r})
dispatcher = Dispatcher(Dispatcher.PLAYGROUND_URL, certificate)
receipt = Receipt(
    eic_popl="CZ00000019", id_jednotky=181, id_pokl="1", porad_cis="1",
    dat_trzby=datetime.now(timezone.utc), celk_trzba=1,
)
xml = dispatcher.get_signed_message(receipt)
ResponseVerifier().verify_signature(etree.fromstring(xml))
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
