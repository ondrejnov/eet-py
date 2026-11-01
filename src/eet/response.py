"""Parsed Odpoved element of the EET service."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True)
class Response:
    """A confirmation with POK or an error, optionally with warnings."""

    uuid: str | None = None
    received_at: str | None = None
    rejected_at: str | None = None
    pok: str | None = None
    error_code: int | None = None
    error_message: str | None = None
    #: The message was received by the non-production environment.
    test: bool = False
    #: {warning code: message}
    warnings: dict[int, str] = field(default_factory=dict)
    #: Value of the HTTP header X-Global-Transaction-Id, worth logging for support.
    transaction_id: str | None = None

    @property
    def is_confirmed(self) -> bool:
        """The sale was accepted and a POK was issued."""
        return self.pok is not None

    @property
    def is_verified(self) -> bool:
        """A message sent in the verification mode was processed without critical errors."""
        return self.error_code == 0
