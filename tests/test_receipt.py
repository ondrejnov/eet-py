from datetime import date, datetime
from decimal import Decimal

import pytest
from helpers import create_receipt

from eet import ClientError, Receipt, ValidationError


def test_valid_receipt():
    create_receipt().validate()


def test_values_from_the_specification():
    receipt = create_receipt()
    receipt.uuid_zpravy = "b3a09b52-7c87-4014-a496-4c7a53cf9125"
    receipt.eic_popl = "CZ8551015704"
    receipt.eic_poverujiciho = "CZ683555118"
    receipt.povereni_vice_popl = True
    receipt.id_jednotky = "164968741"
    receipt.id_pokl = "5a/A-q/5:22d_2"
    receipt.porad_cis = "#25/c-12/1A_2/2027"
    receipt.celk_trzba = "-187.20"
    receipt.urceno_cerp_zuct = 343
    receipt.cerp_zuct = Decimal("237.5")
    receipt.validate()


@pytest.mark.parametrize(
    ("item", "value"),
    [
        *((item, None) for item in ("eic_popl", "id_jednotky", "id_pokl", "porad_cis", "dat_trzby", "celk_trzba")),
        ("uuid_zpravy", "b3a09b52-7c87-6014-a496-4c7a53cf9125"),
        ("uuid_zpravy", "b3a09b52-7c87-4014-c496-4c7a53cf9125"),
        ("uuid_zpravy", ""),
        ("eic_popl", "x"),
        ("eic_popl", "CZ1234567"),
        ("eic_popl", "CZ12345678901"),
        ("eic_popl", "CZ00000019\n"),
        ("eic_popl", "CZ٠٠٠٠٠٠٠٠"),
        ("eic_poverujiciho", ""),
        ("id_jednotky", 0),
        ("id_jednotky", "0123"),
        ("id_jednotky", 1000000000),
        ("id_jednotky", "١٢٣"),
        pytest.param("id_jednotky", 10**5000, id="id_jednotky-extremely long int"),
        ("id_pokl", ""),
        ("id_pokl", "1" * 21),
        ("id_pokl", "pokladna č.1"),
        ("porad_cis", "1" * 26),
        ("porad_cis", "<script>"),
        ("porad_cis", "12\n"),
        ("celk_trzba", "12,50"),
        ("urceno_cerp_zuct", 100000000),
        ("cerp_zuct", "abc"),
        # Wrong types are refused as well, nothing is silently converted.
        ("dat_trzby", "2027-01-01 12:00:00"),
        ("dat_trzby", date(2027, 1, 1)),
        ("dat_trzby", datetime(2027, 1, 1, 12)),
        ("prvni_zaslani", "false"),
        ("prvni_zaslani", None),
        ("prvni_zaslani", 1),
        ("povereni_vice_popl", 1),
        ("id_jednotky", True),
        ("id_jednotky", 181.5),
        ("id_pokl", ["x"]),
        ("porad_cis", object()),
        ("celk_trzba", True),
        ("cerp_zuct", [1]),
    ],
)
def test_invalid_item(item, value):
    receipt = create_receipt()
    setattr(receipt, item, value)

    with pytest.raises(ValidationError, match=item):
        receipt.validate()


def test_all_problems_are_reported_at_once():
    with pytest.raises(ValidationError) as info:
        Receipt().validate()
    assert len(info.value.errors) == 6


def test_renamed_item_gives_a_hint():
    receipt = Receipt()
    with pytest.raises(ClientError, match="eic_popl"):
        receipt.dic_popl = "CZ00000019"


@pytest.mark.parametrize("unknown", ["id_provoz", "dan1", "zakl_dan1", "rezim", "celk_trzb"])
def test_removed_or_misspelled_item_cannot_silently_disappear(unknown):
    receipt = Receipt()
    with pytest.raises(ClientError):
        setattr(receipt, unknown, 1)
    with pytest.raises(TypeError):
        Receipt(**{unknown: 1})


def test_items_cannot_be_mixed_up_by_position():
    with pytest.raises(TypeError):
        Receipt("CZ00000019")


def test_for_resend():
    receipt = create_receipt()
    receipt.uuid_zpravy = "b3a09b52-7c87-4014-a496-4c7a53cf9125"

    copy = receipt.for_resend()

    assert copy.uuid_zpravy is None
    assert copy.prvni_zaslani is False
    assert copy.dat_trzby is receipt.dat_trzby
    assert copy.porad_cis == receipt.porad_cis
    assert receipt.uuid_zpravy == "b3a09b52-7c87-4014-a496-4c7a53cf9125"
    assert receipt.prvni_zaslani is True
