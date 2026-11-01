"""Registered sale (e-trzba) in the data format of the EET interface v4."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime

from . import formatting
from .exceptions import ClientError, ValidationError
from .formatting import Amount

_UUID_MASK = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}")
_EIC_MASK = re.compile(r"CZ[0-9]{8,10}")
_UNIT_MASK = re.compile(r"[1-9][0-9]{0,8}")
_CASH_REGISTER_MASK = re.compile(r"[0-9a-zA-Z.,:;/#\-_ ]{1,20}")
_SERIAL_NUMBER_MASK = re.compile(r"[0-9a-zA-Z.,:;/#\-_ ]{1,25}")

#: Items of the previous EET version with a direct replacement.
_RENAMED_ITEMS = {
    "dic_popl": "eic_popl",
    "dic_poverujiciho": "eic_poverujiciho",
    "id_provoz": "id_jednotky",
}


@dataclass(kw_only=True)
class Receipt:
    """Item names follow the specification. Their types are checked by validate() before sending."""

    eic_popl: str | None = None
    id_jednotky: int | str | None = None
    id_pokl: int | str | None = None
    porad_cis: int | str | None = None
    dat_trzby: datetime | None = None
    celk_trzba: Amount | None = None
    urceno_cerp_zuct: Amount | None = None
    cerp_zuct: Amount | None = None
    eic_poverujiciho: str | None = None
    povereni_vice_popl: bool | None = None
    #: Message UUID, a new one is generated for every sending when left empty.
    uuid_zpravy: str | None = None
    #: False for every other attempt to send the same sale.
    prvni_zaslani: bool = True

    def __setattr__(self, name: str, value: object) -> None:
        # A misspelled or removed item must not silently disappear from a tax message.
        if name not in self.__dataclass_fields__:
            renamed = _RENAMED_ITEMS.get(name)
            hint = f", use {renamed}" if renamed else ""
            raise ClientError(f"EET 2.0 has no item {name}{hint}.")
        object.__setattr__(self, name, value)

    def for_resend(self) -> Receipt:
        """Return a copy for another attempt to send the same sale.

        The data stay untouched, the new message gets a new UUID and prvni_zaslani = False.
        """
        return dataclasses.replace(self, uuid_zpravy=None, prvni_zaslani=False)

    def validate(self) -> None:
        """Raise ValidationError with all the problems found."""
        errors: list[str] = []

        self._check_mask(errors, "uuid_zpravy", _UUID_MASK, required=False)
        self._check_mask(errors, "eic_popl", _EIC_MASK, required=True)
        self._check_mask(errors, "eic_poverujiciho", _EIC_MASK, required=False)
        self._check_mask(errors, "id_jednotky", _UNIT_MASK, required=True)
        self._check_mask(errors, "id_pokl", _CASH_REGISTER_MASK, required=True)
        self._check_mask(errors, "porad_cis", _SERIAL_NUMBER_MASK, required=True)

        if self.dat_trzby is None:
            errors.append("dat_trzby is required")
        else:
            try:
                formatting.date_time(self.dat_trzby)
            except ValidationError as e:
                errors.append(f"dat_trzby {e}")

        if not isinstance(self.prvni_zaslani, bool):
            errors.append("prvni_zaslani must be a boolean")

        if self.povereni_vice_popl is not None and not isinstance(self.povereni_vice_popl, bool):
            errors.append("povereni_vice_popl must be a boolean")

        if self.celk_trzba is None:
            errors.append("celk_trzba is required")

        for item in ("celk_trzba", "urceno_cerp_zuct", "cerp_zuct"):
            try:
                formatting.price(getattr(self, item))
            except ValidationError as e:
                errors.append(f"{item}: {e}")

        if errors:
            raise ValidationError(errors)

    def _check_mask(self, errors: list[str], item: str, mask: re.Pattern[str], *, required: bool) -> None:
        value = getattr(self, item)
        if value is None:
            if required:
                errors.append(f"{item} is required")
            return

        if isinstance(value, bool) or not isinstance(value, (int, str)):
            errors.append(f"{item} must be an integer or a string")
            return

        try:
            text = str(value)
        except ValueError:  # Python refuses to convert an extremely long int to a string
            errors.append(f"{item} has an invalid format")
            return

        if not mask.fullmatch(text):
            errors.append(f'{item} has an invalid format: "{text}"')
