"""HTTP communication with the EET service."""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .exceptions import TransportError


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    transaction_id: str | None = None


@runtime_checkable
class Transport(Protocol):
    def post(self, url: str, body: bytes, soap_action: str) -> HttpResponse:
        """Send a signed SOAP message, raise TransportError when no HTTP response was received."""
        ...


class HttpsTransport:
    """HTTPS transport as prescribed by the interface: HTTP/1.1, TLS 1.2 or newer, verified server certificate.

    Every request uses a new connection, so the DNS name is always resolved again and DNS balancing
    of the production environment is respected.

    Socket timeouts of the standard library limit a single network operation only and do not cover
    the DNS resolution at all. A cash register must not wait longer than it was told, so the request
    runs in a helper thread and the limit is enforced from the outside.
    """

    def __init__(self, timeout: float = 2.5, connect_timeout: float = 2.0) -> None:
        """Set the total limit of one request and the limit for establishing a connection, in seconds."""
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._context = ssl.create_default_context()
        self._context.minimum_version = ssl.TLSVersion.TLSv1_2

    def post(self, url: str, body: bytes, soap_action: str) -> HttpResponse:
        connection, target = self._open(url)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
            "User-Agent": "eet-py",
            "Connection": "close",
        }

        abandoned = threading.Event()
        outcome: list[HttpResponse | Exception] = []

        def exchange() -> None:
            try:
                connection.connect()
                if abandoned.is_set():  # the caller has gone: it will send the sale again by itself
                    return
                assert connection.sock is not None
                connection.sock.settimeout(self._timeout)
                connection.request("POST", target, body=body, headers=headers)
                response = connection.getresponse()
                outcome.append(
                    HttpResponse(response.status, response.read(), response.getheader("X-Global-Transaction-Id"))
                )
            except Exception as e:  # handed over to the calling thread
                outcome.append(e)
            finally:
                connection.close()

        thread = threading.Thread(target=exchange, name="eet-https", daemon=True)
        thread.start()
        thread.join(self._timeout)

        if not outcome:
            abandoned.set()
            _interrupt(connection)
            raise TransportError(f"The request timed out after {self._timeout} s.")

        result = outcome[0]
        if isinstance(result, (OSError, http.client.HTTPException)):
            raise TransportError(str(result) or type(result).__name__) from result
        if isinstance(result, Exception):
            raise result

        return result

    def _open(self, url: str) -> tuple[http.client.HTTPSConnection, str]:
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port
        except ValueError as e:
            raise TransportError(f"Invalid URL {url}: {e}") from e

        # HTTPS is mandatory, an unencrypted connection is refused before anything is sent.
        if parts.scheme != "https" or not host:
            raise TransportError(f"Only an https:// URL is supported, got {url}")

        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query

        return http.client.HTTPSConnection(host, port, timeout=self._connect_timeout, context=self._context), target


def _interrupt(connection: http.client.HTTPSConnection) -> None:
    sock = connection.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
