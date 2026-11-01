"""Sending of registered sales to the EET 2.0 service."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from lxml import etree

from . import formatting
from . import namespaces as ns
from .certificate import Certificate
from .exceptions import ResponseVerificationError, ServerError, TransportError
from .receipt import Receipt
from .response import Response
from .signer import sign_message
from .transport import HttpResponse, HttpsTransport, Transport
from .verifier import ResponseVerifier
from .xmlutil import parse_xml, text_content

_LOCAL_TIMEZONE = ZoneInfo("Europe/Prague")
_POK_MASK = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}-[0-9a-fA-F]{2}"
)
_ERROR_CODE_MASK = re.compile(r"-?[0-9]{1,3}")
_WARNING_CODE_MASK = re.compile(r"[0-9]{1,3}")
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'

_ERROR_MESSAGES = {
    -1: "Dočasná technická chyba zpracování – odešlete prosím datovou zprávu později.",
    2: "Kódování XML není platné.",
    3: "XML zpráva nevyhověla kontrole XML schématu.",
    4: "Neplatný podpis SOAP zprávy.",
    6: "EIČ poplatníka má chybnou strukturu.",
    7: "Datová zpráva je příliš velká.",
    8: "Datová zpráva nebyla zpracována kvůli technické chybě nebo chybě dat.",
}


class Dispatcher:
    """Details of the last call are kept in the instance, use one instance per thread."""

    PLAYGROUND_URL = "https://pg.trzbyeet.gov.cz/eet/services/EETServiceSOAP/v4"
    SOAP_ACTION = "http://fs.gov.cz/eet/OdeslaniTrzby"

    def __init__(
        self,
        service: str,
        certificate: Certificate,
        verifier: ResponseVerifier | None = None,
        transport: Transport | None = None,
    ) -> None:
        """Set the service URL, the cash register certificate and optional collaborators.

        verifier checks signed confirmations, ResponseVerifier.for_playground() by default.
        transport is the HTTP client, HttpsTransport by default.
        """
        # A wrong type must fail before anything is sent.
        if not isinstance(certificate, Certificate):
            raise TypeError("certificate must be an instance of Certificate")
        if verifier is not None and not isinstance(verifier, ResponseVerifier):
            raise TypeError("verifier must be an instance of ResponseVerifier")
        if transport is not None and not isinstance(transport, Transport):
            raise TypeError("transport must implement Transport")

        self._service = service
        self._certificate = certificate
        self._verifier = verifier if verifier is not None else ResponseVerifier.for_playground()
        self._transport = transport if transport is not None else HttpsTransport()

        self._last_request: bytes | None = None
        self._last_response_body: bytes | None = None
        self._last_response: Response | None = None
        self._last_uuid: str | None = None
        self._last_response_time: float | None = None

    def send(self, receipt: Receipt) -> str:
        """Register the sale and return POK, the confirmation code.

        Raises ValidationError when the receipt is invalid and nothing was sent,
        TransportError when no valid response was received: send Receipt.for_resend() later,
        ServerError when the server rejected the message: see ServerError.is_retryable,
        ResponseVerificationError when the response cannot be trusted.
        """
        response = self._process(receipt, check=False)

        if response.pok is None:
            raise ServerError(
                "The response does not contain a confirmation code.", response.error_code or 0, response
            )

        return response.pok

    def check(self, receipt: Receipt) -> bool:
        """Send the sale in the verification mode (overeni="true").

        The sale is not registered and no POK is issued. Details of a refused message
        are available through last_response.

        Raises ValidationError when the receipt is invalid and nothing was sent,
        TransportError when no valid response was received,
        ResponseVerificationError when the response cannot be trusted.
        """
        try:
            return self._process(receipt, check=True).is_verified
        except ResponseVerificationError:
            raise
        except ServerError:
            return False

    def get_signed_message(self, receipt: Receipt, check: bool = False) -> bytes:
        """Return the signed SOAP message for the sale without sending it."""
        receipt.validate()
        return self._build_message(receipt, receipt.uuid_zpravy or _new_uuid(), check)

    @property
    def warnings(self) -> dict[int, str]:
        """Warnings of the last response, {warning code: message}."""
        return {} if self._last_response is None else self._last_response.warnings

    @property
    def last_response(self) -> Response | None:
        return self._last_response

    @property
    def last_request(self) -> bytes | None:
        """Signed SOAP message sent by the last call."""
        return self._last_request

    @property
    def last_uuid(self) -> str | None:
        """UUID of the message sent by the last call."""
        return self._last_uuid

    @property
    def last_response_body(self) -> bytes | None:
        """Raw body of the last HTTP response."""
        return self._last_response_body

    @property
    def last_response_time(self) -> float | None:
        """Duration of the last HTTP call in seconds."""
        return self._last_response_time

    @property
    def last_request_size(self) -> int:
        return len(self._last_request or b"")

    @property
    def last_response_size(self) -> int:
        return len(self._last_response_body or b"")

    def _process(self, receipt: Receipt, *, check: bool) -> Response:
        receipt.validate()

        self._last_uuid = receipt.uuid_zpravy or _new_uuid()
        self._last_request = self._build_message(receipt, self._last_uuid, check)
        self._last_response = None
        self._last_response_body = None
        self._last_response_time = None

        start = time.perf_counter()
        http = self._transport.post(self._service, self._last_request, self.SOAP_ACTION)
        self._last_response_time = time.perf_counter() - start
        self._last_response_body = http.body

        response = self._parse_response(http)
        self._last_response = response

        if response.error_code:
            raise ServerError(
                response.error_message or _ERROR_MESSAGES.get(response.error_code, "Unknown EET error."),
                response.error_code,
                response,
            )

        return response

    def _build_message(self, receipt: Receipt, message_uuid: str, check: bool) -> bytes:
        envelope = etree.Element(f"{{{ns.SOAP}}}Envelope", nsmap={"soapenv": ns.SOAP, "v4": ns.EET})
        etree.SubElement(envelope, f"{{{ns.SOAP}}}Header")
        body = etree.SubElement(envelope, f"{{{ns.SOAP}}}Body")
        sale = etree.SubElement(body, f"{{{ns.EET}}}Trzba")

        _add_element(sale, "Hlavicka", {
            "uuid_zpravy": message_uuid,
            "dat_odesl": formatting.date_time(datetime.now(_LOCAL_TIMEZONE)),
            "prvni_zaslani": formatting.boolean(receipt.prvni_zaslani),
            "overeni": "true" if check else None,
        })

        assert receipt.dat_trzby is not None  # guaranteed by validate()
        _add_element(sale, "Data", {
            "eic_popl": receipt.eic_popl,
            "eic_poverujiciho": receipt.eic_poverujiciho,
            "povereni_vice_popl": (
                None if receipt.povereni_vice_popl is None else formatting.boolean(receipt.povereni_vice_popl)
            ),
            "id_jednotky": str(receipt.id_jednotky),
            "id_pokl": str(receipt.id_pokl),
            "porad_cis": str(receipt.porad_cis),
            "dat_trzby": formatting.date_time(receipt.dat_trzby),
            "celk_trzba": formatting.price(receipt.celk_trzba),
            "urceno_cerp_zuct": formatting.price(receipt.urceno_cerp_zuct),
            "cerp_zuct": formatting.price(receipt.cerp_zuct),
        })

        signed = sign_message(envelope, self._certificate)

        # lxml would write the declaration with apostrophes, the specification prescribes this exact form.
        return _XML_DECLARATION + etree.tostring(signed, encoding="UTF-8", xml_declaration=False)

    def _parse_response(self, http: HttpResponse) -> Response:
        try:
            envelope = parse_xml(http.body)
        except (etree.LxmlError, ValueError) as e:
            raise TransportError(f"The response with HTTP status {http.status} is not a valid XML message.") from e

        faults = _xpath(envelope, "/s:Envelope/s:Body/s:Fault")
        if faults:
            message = str(faults[0].xpath("string(faultstring)")).strip()
            # A fault is not an answer to the sale, so its state stays unknown.
            raise TransportError(f"SOAP fault: {message}")

        answers = _xpath(envelope, "/s:Envelope/s:Body/eet:Odpoved")
        if not answers:
            raise TransportError(f"The response with HTTP status {http.status} is not an EET message.")
        answer = answers[0]

        header = _first(answer, "eet:Hlavicka")
        confirmation = _first(answer, "eet:Potvrzeni")
        error = _first(answer, "eet:Chyba")

        pok = _attribute(confirmation, "pok")
        if pok is None and error is None:
            raise TransportError("The response contains neither a confirmation code nor an error.")

        result = confirmation if confirmation is not None else error
        response = Response(
            uuid=_attribute(header, "uuid_zpravy"),
            received_at=_attribute(header, "dat_prij"),
            rejected_at=_attribute(header, "dat_odmit"),
            pok=pok,
            error_code=None if error is None else _error_code(error),
            error_message=None if error is None else text_content(error),
            test=_attribute(result, "test") in ("true", "1"),
            warnings=_warnings(answer),
            transaction_id=http.transaction_id,
        )

        if response.is_confirmed:
            self._verify_confirmation(envelope, response)

        return response

    def _verify_confirmation(self, envelope: etree._Element, response: Response) -> None:
        try:
            self._verifier.verify(envelope)
        except ResponseVerificationError as e:
            e.response = response
            raise

        if response.uuid != self._last_uuid:
            raise ResponseVerificationError("The confirmation belongs to a different message.", response)

        if not _POK_MASK.fullmatch(response.pok or ""):
            raise ResponseVerificationError("The confirmation code has an invalid format.", response)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _add_element(parent: etree._Element, name: str, attributes: dict[str, str | None]) -> None:
    """Add an element, items without a value must not be present in the message at all."""
    element = etree.SubElement(parent, f"{{{ns.EET}}}{name}")
    for attribute, value in attributes.items():
        if value is not None:
            element.set(attribute, value)


def _xpath(context: etree._Element, query: str) -> list[etree._Element]:
    return context.xpath(query, namespaces=ns.XPATH_PREFIXES)


def _first(context: etree._Element, query: str) -> etree._Element | None:
    nodes = _xpath(context, query)
    return nodes[0] if nodes else None


def _attribute(element: etree._Element | None, name: str) -> str | None:
    return None if element is None else element.get(name)


def _error_code(error: etree._Element) -> int:
    # An unknown code must not turn into 0, which means success of the verification mode.
    code = error.get("kod", "")
    if not _ERROR_CODE_MASK.fullmatch(code):
        raise TransportError(f"The response contains an invalid error code: {code!r}")
    return int(code)


def _warnings(answer: etree._Element) -> dict[int, str]:
    # A malformed warning must not invalidate a signed confirmation, it is reported under the code 0.
    warnings = {}
    for warning in _xpath(answer, "eet:Varovani"):
        code = warning.get("kod_varov", "")
        warnings[int(code) if _WARNING_CODE_MASK.fullmatch(code) else 0] = text_content(warning)
    return warnings
