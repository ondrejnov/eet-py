"""Real communication with the EET playground, run only with the environment variable EET_PLAYGROUND=1
after downloading the certificates by scripts/fetch_playground_certs.py.
"""

import os
import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from helpers import CERT_DIR

from eet import Certificate, Dispatcher, HttpsTransport, Receipt, ServerError

pytestmark = [
    pytest.mark.skipif(os.environ.get("EET_PLAYGROUND") != "1", reason="Set EET_PLAYGROUND=1 to run against the playground."),
    pytest.mark.skipif(
        not (CERT_DIR / "CA_EET-Playground-CZ00000019.p12").is_file(), reason="Run scripts/fetch_playground_certs.py first."
    ),
]

PRAGUE = ZoneInfo("Europe/Prague")
POK = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}-ff")


def dispatcher_for(eic):
    password = (CERT_DIR / "password_pokladni_cert_playground.txt").read_text().strip()
    certificate = Certificate.from_pkcs12(CERT_DIR / f"CA_EET-Playground-{eic}.p12", password)
    return Dispatcher(Dispatcher.PLAYGROUND_URL, certificate, transport=HttpsTransport(15, 10))


def receipt_for(eic):
    return Receipt(
        eic_popl=eic,
        id_jednotky=181,
        id_pokl="test/1",
        porad_cis="T" + secrets.token_hex(8),
        dat_trzby=datetime.now(PRAGUE),
        celk_trzba=1000,
    )


@pytest.mark.parametrize("eic", ["CZ00000019", "CZ683555118", "CZ8551015704"])
def test_all_test_identities(eic):
    """Legal person, taxpayer's own number, natural person."""
    dispatcher = dispatcher_for(eic)

    assert POK.fullmatch(dispatcher.send(receipt_for(eic)))

    response = dispatcher.last_response
    assert response.test is True
    assert response.uuid == dispatcher.last_uuid
    assert response.warnings == {}
    assert response.transaction_id is not None

    assert dispatcher.check(receipt_for(eic)) is True
    assert dispatcher.last_response.error_code == 0


def test_delegation_negative_amount_prepaid_items_and_resending():
    dispatcher = dispatcher_for("CZ00000019")
    receipt = receipt_for("CZ00000019")
    receipt.eic_poverujiciho = "CZ8551015704"
    receipt.povereni_vice_popl = True
    receipt.celk_trzba = -187.2
    receipt.urceno_cerp_zuct = 343
    receipt.cerp_zuct = "237.50"

    assert POK.fullmatch(dispatcher.send(receipt))
    assert dispatcher.warnings == {}

    assert POK.fullmatch(dispatcher.send(receipt.for_resend()))
    assert dispatcher.warnings == {}


def test_warning_eic_differs_from_the_certificate():
    dispatcher = dispatcher_for("CZ00000019")
    dispatcher.send(receipt_for("CZ683555118"))
    assert list(dispatcher.warnings) == [1]


def test_warning_format_of_the_unit():
    dispatcher = dispatcher_for("CZ00000019")
    receipt = receipt_for("CZ00000019")
    receipt.id_jednotky = 7
    assert dispatcher.check(receipt) is True
    assert list(dispatcher.warnings) == [6]


def test_warning_sale_is_too_old():
    dispatcher = dispatcher_for("CZ00000019")
    receipt = receipt_for("CZ00000019")
    receipt.dat_trzby = datetime(2026, 6, 1, 12, tzinfo=PRAGUE)
    assert dispatcher.check(receipt) is True
    assert list(dispatcher.warnings) == [5]


def test_critical_error_eic_with_a_wrong_check_digit():
    dispatcher = dispatcher_for("CZ00000019")

    with pytest.raises(ServerError) as info:
        dispatcher.send(receipt_for("CZ00000018"))
    assert info.value.code == 6

    assert dispatcher.check(receipt_for("CZ00000018")) is False
    assert dispatcher.last_response.error_code == 6
