"""Exceptions of the library, all of them derive from EETError."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .response import Response


class EETError(Exception):
    """Base class of all errors raised by the library."""


class ClientError(EETError):
    """The problem occurred on the client side, the server has not accepted anything."""


class ValidationError(ClientError):
    """The receipt does not match the data masks of the EET interface and was not sent."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class TransportError(ClientError):
    """No valid EET response was received (timeout, connection error, unexpected HTTP response).

    The state of the sale is unknown, so it has to be sent again: see Receipt.for_resend().
    """


class ServerError(EETError):
    """The server rejected the message. The attribute code is the EET error code."""

    #: Error codes of temporary failures, the message should be sent again later.
    RETRYABLE_CODES = (-1, 8)

    def __init__(self, message: str, code: int, response: Response | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.response = response

    @property
    def is_retryable(self) -> bool:
        return self.code in self.RETRYABLE_CODES


class ResponseVerificationError(ServerError):
    """The response is not trustworthy: invalid signature, untrusted certificate or a different UUID.

    The POK it may contain, available through the attribute response, must not be treated
    as a confirmation.
    """

    def __init__(self, message: str, response: Response | None = None) -> None:
        super().__init__(message, 0, response)
