import pytest
from helpers import (
    GFR,
    FakeTransport,
    create_receipt,
    create_response_xml,
    create_test_certificate,
    request_attribute,
)

from eet import (
    Dispatcher,
    HttpResponse,
    ResponseVerificationError,
    ResponseVerifier,
    ServerError,
    TransportError,
    ValidationError,
)
from eet import (
    namespaces as ns,
)

POK = "987a6be5-6af5-44f3-b4fc-987654321000-ff"
RECEIVED = 'dat_prij="2027-03-04T18:25:21+01:00"'
REJECTED = 'dat_odmit="2027-03-04T18:25:21+01:00"'

certificate = create_test_certificate()
server_certificate = create_test_certificate("CZ72080043", GFR)
verifier = ResponseVerifier(None, GFR)


def dispatcher_for(reply):
    transport = FakeTransport(reply)
    return Dispatcher(Dispatcher.PLAYGROUND_URL, certificate, verifier, transport), transport


def confirm(extra="", pok=POK, uuid=None, signer=server_certificate):
    """Signed confirmation of the message in the request, as the server would create it."""

    def reply(request):
        message_uuid = uuid or request_attribute(request, "Hlavicka", "uuid_zpravy")
        content = (
            f'<v4:Hlavicka uuid_zpravy="{message_uuid}" {RECEIVED}/><v4:Potvrzeni pok="{pok}" test="true"/>{extra}'
        )
        return HttpResponse(200, create_response_xml(content, signer), "trans-1")

    return reply


def reject(code, text="Popis chyby", extra=""):
    def reply(request):
        message_uuid = request_attribute(request, "Hlavicka", "uuid_zpravy")
        content = (
            f'<v4:Hlavicka uuid_zpravy="{message_uuid}" {REJECTED}/>'
            f'<v4:Chyba kod="{code}" test="true">{text}</v4:Chyba>{extra}'
        )
        return HttpResponse(200, create_response_xml(content), "trans-2")

    return reply


def test_successful_registration():
    dispatcher, transport = dispatcher_for(confirm())

    assert dispatcher.send(create_receipt()) == POK
    assert dispatcher.warnings == {}

    response = dispatcher.last_response
    assert response.is_confirmed
    assert not response.is_verified
    assert response.test is True
    assert response.pok == POK
    assert response.received_at == "2027-03-04T18:25:21+01:00"
    assert response.transaction_id == "trans-1"
    assert response.uuid == dispatcher.last_uuid

    request = transport.requests[0]
    assert request["url"] == Dispatcher.PLAYGROUND_URL
    assert request["action"] == "http://fs.gov.cz/eet/OdeslaniTrzby"
    assert request["body"] == dispatcher.last_request
    assert len(request["body"]) == dispatcher.last_request_size
    assert dispatcher.last_response_size > 0
    assert isinstance(dispatcher.last_response_time, float)


def test_parsed_response_cannot_be_changed():
    dispatcher, _ = dispatcher_for(confirm())
    dispatcher.send(create_receipt())

    with pytest.raises(AttributeError):
        dispatcher.last_response.pok = "forged"
    with pytest.raises(AttributeError):
        del dispatcher.last_response.pok
    assert dispatcher.last_response.pok == POK

    with pytest.raises(AttributeError):
        HttpResponse(200, b"body").body = b"forged"


@pytest.mark.parametrize(
    "arguments",
    [
        {"verifier": "x"},
        {"transport": "x"},
    ],
)
def test_wrong_collaborators_fail_right_away(arguments):
    with pytest.raises(TypeError):
        Dispatcher(Dispatcher.PLAYGROUND_URL, certificate, **arguments)


def test_wrong_certificate_fails_right_away():
    with pytest.raises(TypeError):
        Dispatcher(Dispatcher.PLAYGROUND_URL, "certificate.pem")


def test_warnings_do_not_stop_the_registration():
    dispatcher, _ = dispatcher_for(confirm(
        '<v4:Varovani kod_varov="1">  EIC poplatnika v datove zprave se neshoduje s EIC v certifikatu  </v4:Varovani>'
        '<v4:Varovani kod_varov="6">id_jednotky neodpovida formatem pridelenemu c. evidencni jednotky</v4:Varovani>'
    ))

    assert dispatcher.send(create_receipt()) == POK
    assert dispatcher.warnings == {
        1: "EIC poplatnika v datove zprave se neshoduje s EIC v certifikatu",
        6: "id_jednotky neodpovida formatem pridelenemu c. evidencni jednotky",
    }


def test_malformed_warning_does_not_invalidate_a_signed_confirmation():
    dispatcher, _ = dispatcher_for(confirm('<v4:Varovani kod_varov="x">Text</v4:Varovani>'))
    assert dispatcher.send(create_receipt()) == POK
    assert dispatcher.warnings == {0: "Text"}


def test_every_message_gets_a_new_uuid_and_an_explicit_one_is_respected():
    dispatcher, _ = dispatcher_for(confirm())
    receipt = create_receipt()

    dispatcher.send(receipt)
    first = dispatcher.last_uuid
    dispatcher.send(receipt)
    assert dispatcher.last_uuid != first
    assert receipt.uuid_zpravy is None

    receipt.uuid_zpravy = "b3a09b52-7c87-4014-a496-4c7a53cf9125"
    dispatcher.send(receipt)
    assert dispatcher.last_uuid == "b3a09b52-7c87-4014-a496-4c7a53cf9125"


def test_resending():
    dispatcher, transport = dispatcher_for(confirm())
    receipt = create_receipt()

    dispatcher.send(receipt)
    dispatcher.send(receipt.for_resend())
    first, second = transport.requests[0]["body"], transport.requests[1]["body"]

    assert request_attribute(first, "Hlavicka", "prvni_zaslani") == "true"
    assert request_attribute(second, "Hlavicka", "prvni_zaslani") == "false"
    assert request_attribute(first, "Hlavicka", "uuid_zpravy") != request_attribute(second, "Hlavicka", "uuid_zpravy")
    for item in ("eic_popl", "id_jednotky", "id_pokl", "porad_cis", "dat_trzby", "celk_trzba"):
        assert request_attribute(first, "Data", item) == request_attribute(second, "Data", item)


def test_invalid_receipt_is_not_sent_at_all():
    dispatcher, transport = dispatcher_for(confirm())
    receipt = create_receipt()
    receipt.eic_popl = "x"

    with pytest.raises(ValidationError):
        dispatcher.send(receipt)
    with pytest.raises(ValidationError):
        dispatcher.check(receipt)
    with pytest.raises(ValidationError):
        dispatcher.get_signed_message(receipt)
    assert transport.requests == []


def test_critical_error():
    dispatcher, _ = dispatcher_for(reject(4, "Neplatny podpis SOAP zpravy"))

    with pytest.raises(ServerError, match="Neplatny podpis SOAP zpravy") as info:
        dispatcher.send(create_receipt())

    assert info.value.code == 4
    assert not info.value.is_retryable
    assert info.value.response.error_code == 4
    assert info.value.response.transaction_id == "trans-2"


@pytest.mark.parametrize("code", [-1, 8])
def test_temporary_errors_are_retryable(code):
    dispatcher, _ = dispatcher_for(reject(code))
    with pytest.raises(ServerError) as info:
        dispatcher.send(create_receipt())
    assert info.value.is_retryable


def test_missing_text_is_replaced_by_a_description_of_the_known_code():
    dispatcher, _ = dispatcher_for(reject(6, ""))
    with pytest.raises(ServerError, match="EIČ poplatníka má chybnou strukturu") as info:
        dispatcher.send(create_receipt())
    assert info.value.code == 6


def test_text_of_the_error_is_not_cut_off_by_a_comment():
    dispatcher, _ = dispatcher_for(reject(3, "Prvni<!-- x --> druha"))
    with pytest.raises(ServerError, match="Prvni druha"):
        dispatcher.send(create_receipt())


def test_verification_mode():
    dispatcher, transport = dispatcher_for(reject(
        0, "Datovou zpravu evidovane trzby v overovacim modu se podarilo zpracovat",
        '<v4:Varovani kod_varov="5">Datum</v4:Varovani>',
    ))

    assert dispatcher.check(create_receipt()) is True
    assert request_attribute(transport.requests[0]["body"], "Hlavicka", "overeni") == "true"
    assert dispatcher.last_response.is_verified
    assert dispatcher.warnings == {5: "Datum"}


def test_verification_mode_with_a_critical_error():
    dispatcher, _ = dispatcher_for(reject(6, "EIC poplatnika ma chybnou strukturu"))

    assert dispatcher.check(create_receipt()) is False
    assert dispatcher.last_response.error_code == 6


def test_invalid_error_code_is_not_mistaken_for_success():
    dispatcher, _ = dispatcher_for(reject("abc"))

    with pytest.raises(TransportError, match="invalid error code"):
        dispatcher.check(create_receipt())
    assert dispatcher.last_response is None


def test_response_of_the_verification_mode_is_not_a_registration():
    dispatcher, _ = dispatcher_for(reject(0))
    with pytest.raises(ServerError, match="confirmation code"):
        dispatcher.send(create_receipt())


def test_unsigned_confirmation_keeps_the_data_for_diagnostics_but_is_refused():
    dispatcher, _ = dispatcher_for(confirm(signer=None))

    with pytest.raises(ResponseVerificationError, match="exactly one Signature") as info:
        dispatcher.send(create_receipt())
    assert info.value.response.pok == POK
    assert not info.value.is_retryable


def test_confirmation_of_a_different_message():
    dispatcher, _ = dispatcher_for(confirm(uuid="123e4567-e89b-42d3-a456-426655440000"))
    with pytest.raises(ResponseVerificationError, match="different message"):
        dispatcher.send(create_receipt())


@pytest.mark.parametrize("pok", ["not-a-pok", POK + "\n", POK + "0"])
def test_confirmation_code_with_an_invalid_format(pok):
    dispatcher, _ = dispatcher_for(confirm(pok=pok))
    with pytest.raises(ResponseVerificationError, match="invalid format"):
        dispatcher.send(create_receipt())


def test_confirmation_signed_by_an_impostor():
    dispatcher, _ = dispatcher_for(confirm(signer=create_test_certificate("CZ72080043", "Utocnik")))
    with pytest.raises(ResponseVerificationError, match="unexpected organization"):
        dispatcher.send(create_receipt())


def test_verification_mode_cannot_hide_a_trust_failure():
    dispatcher, _ = dispatcher_for(confirm(signer=None))
    with pytest.raises(ResponseVerificationError):
        dispatcher.check(create_receipt())


SOAP_FAULT = (
    f'<soapenv:Envelope xmlns:soapenv="{ns.SOAP}"><soapenv:Body><soapenv:Fault><faultcode>soapenv:Server</faultcode>'
    "<faultstring>Internal Error</faultstring></soapenv:Fault></soapenv:Body></soapenv:Envelope>"
).encode()


def test_soap_fault_leaves_the_state_of_the_sale_unknown():
    dispatcher, _ = dispatcher_for(HttpResponse(500, SOAP_FAULT))

    with pytest.raises(TransportError, match="SOAP fault: Internal Error"):
        dispatcher.send(create_receipt())
    assert dispatcher.last_response is None
    assert dispatcher.last_response_body == SOAP_FAULT


def test_verification_mode_does_not_mistake_a_soap_fault_for_a_refused_message():
    dispatcher, _ = dispatcher_for(HttpResponse(500, SOAP_FAULT))

    with pytest.raises(TransportError, match="SOAP fault: Internal Error"):
        dispatcher.check(create_receipt())


def test_verification_mode_keeps_the_details_of_every_refused_message():
    dispatcher, _ = dispatcher_for(reject(4, "Neplatny podpis"))

    assert dispatcher.check(create_receipt()) is False
    assert dispatcher.last_response.error_message == "Neplatny podpis"


def test_response_which_cannot_be_canonicalized_is_not_trusted():
    # Canonicalization refuses relative namespace URIs, such a reply must not escape as an lxml error.
    signed = create_response_xml(f'<v4:Hlavicka {RECEIVED}/><v4:Potvrzeni pok="{POK}"/>', server_certificate)
    tampered = signed.replace(b"<v4:Potvrzeni", b'<r:x xmlns:r="rel/ns"/><v4:Potvrzeni')
    assert tampered != signed
    dispatcher, _ = dispatcher_for(HttpResponse(200, tampered))

    with pytest.raises(ResponseVerificationError, match="canonicalized") as info:
        dispatcher.send(create_receipt())
    assert info.value.response.pok == POK


ENVELOPE = (
    f'<soapenv:Envelope xmlns:soapenv="{ns.SOAP}" xmlns:v4="{ns.EET}"><soapenv:Body><v4:Odpoved>%s</v4:Odpoved>'
    "</soapenv:Body></soapenv:Envelope>"
)


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(HttpResponse(200, b""), id="empty body, the server may ignore suspicious messages"),
        pytest.param(HttpResponse(502, b"<html><body>Bad gateway<br></body>"), id="error page"),
        pytest.param(HttpResponse(200, b"<foo/>"), id="other XML"),
        pytest.param(
            HttpResponse(200, b'<!DOCTYPE foo [<!ENTITY x "y">]>' + (ENVELOPE % '<v4:Chyba kod="4">&x;</v4:Chyba>').encode()),
            id="document type declaration",
        ),
        pytest.param(HttpResponse(200, (ENVELOPE % "<v4:Hlavicka/>").encode()), id="neither confirmation nor error"),
        pytest.param(
            HttpResponse(200, (ENVELOPE % '<v4:Hlavicka/><v4:Potvrzeni test="true"/>').encode()),
            id="confirmation without a confirmation code",
        ),
        pytest.param(HttpResponse(200, (ENVELOPE % '<v4:Chyba test="true"/>').encode()), id="error without a code"),
    ],
)
def test_situations_in_which_the_state_of_the_sale_is_unknown(reply):
    dispatcher, _ = dispatcher_for(reply)

    with pytest.raises(TransportError):
        dispatcher.send(create_receipt())
    assert dispatcher.last_response is None


def test_external_entities_are_not_loaded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    xml = f'<!DOCTYPE foo [<!ENTITY x SYSTEM "file://{secret}">]>' + ENVELOPE % '<v4:Chyba kod="4">&x;</v4:Chyba>'
    dispatcher, _ = dispatcher_for(HttpResponse(200, xml.encode()))

    with pytest.raises(TransportError) as info:
        dispatcher.send(create_receipt())
    assert "secret" not in str(info.value).replace(str(secret), "")


def test_transport_failure():
    dispatcher, _ = dispatcher_for(TransportError("Operation timed out"))

    with pytest.raises(TransportError, match="Operation timed out"):
        dispatcher.send(create_receipt())
    with pytest.raises(TransportError):
        dispatcher.check(create_receipt())
