import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from helpers import create_key_pair

from eet import HttpsTransport, TransportError


def test_https_is_mandatory():
    with pytest.raises(TransportError, match="https"):
        HttpsTransport(0.5, 0.5).post("http://127.0.0.1:1/", b"<x/>", "action")


@pytest.mark.parametrize("url", ["", "ftp://example.com/", "https://", "https://example.com:port/"])
def test_invalid_url(url):
    with pytest.raises(TransportError):
        HttpsTransport(0.5, 0.5).post(url, b"<x/>", "action")


def test_connection_error_is_a_transport_problem():
    with pytest.raises(TransportError):
        HttpsTransport(0.5, 0.5).post("https://127.0.0.1:1/", b"<x/>", "action")


@pytest.fixture
def silent_server():
    """Accepts connections and never answers."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        yield server.getsockname()[1]


def test_total_limit_is_enforced(silent_server):
    start = time.perf_counter()
    with pytest.raises(TransportError, match="timed out"):
        HttpsTransport(0.3, 5).post(f"https://127.0.0.1:{silent_server}/", b"<x/>", "action")
    assert time.perf_counter() - start < 1


def test_total_limit_covers_a_blocked_dns_resolution(monkeypatch):
    release = threading.Event()

    def blocked(*args, **kwargs):
        release.wait(5)
        raise socket.gaierror("released")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    start = time.perf_counter()
    try:
        with pytest.raises(TransportError, match="timed out"):
            HttpsTransport(0.2, 0.2).post("https://example.invalid/", b"<x/>", "action")
        assert time.perf_counter() - start < 1
    finally:
        release.set()


@pytest.fixture
def untrusted_tls_server(tmp_path):
    """TLS server with a self-signed certificate."""
    key, certificate = create_key_pair("localhost")
    pem = tmp_path / "server.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        + certificate.public_bytes(serialization.Encoding.PEM)
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem)

    server = socket.create_server(("127.0.0.1", 0))

    def serve():
        try:
            connection, _ = server.accept()
            with context.wrap_socket(connection, server_side=True):
                pass
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    server.close()


def test_server_certificate_is_verified(untrusted_tls_server):
    with pytest.raises(TransportError, match="(?i)certificate"):
        HttpsTransport(2, 2).post(f"https://127.0.0.1:{untrusted_tls_server}/", b"<x/>", "action")


@pytest.fixture
def trusted_tls_server(tmp_path):
    """TLS server trusted by the client, records the request and answers with a prepared response."""
    key = create_key_pair("localhost")[0]
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = tmp_path / "server.pem"
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        + certificate_pem
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem)

    server = socket.create_server(("127.0.0.1", 0))
    received = {}

    def serve():
        try:
            connection, _ = server.accept()
            with context.wrap_socket(connection, server_side=True) as tls:
                received["version"] = tls.version()
                data = b""
                while b"\r\n\r\n" not in data or not data.endswith(b"<x/>"):
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                received["request"] = data
                tls.sendall(
                    b"HTTP/1.1 200 OK\r\nX-Global-Transaction-ID: abc-1\r\nContent-Length: 4\r\n"
                    b"Connection: close\r\n\r\n<ok>"
                )
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1], certificate_pem, received
    server.close()


def test_request_on_the_wire(trusted_tls_server):
    port, certificate_pem, received = trusted_tls_server
    transport = HttpsTransport(5, 5)
    transport._context.load_verify_locations(cadata=certificate_pem.decode())

    response = transport.post(f"https://localhost:{port}/eet/v4?a=1", b"<x/>", "http://fs.gov.cz/eet/OdeslaniTrzby")

    assert response.status == 200
    assert response.body == b"<ok>"
    assert response.transaction_id == "abc-1"

    request = received["request"]
    assert request.startswith(b"POST /eet/v4?a=1 HTTP/1.1\r\n")
    assert b"Content-Type: text/xml; charset=utf-8\r\n" in request
    assert b'SOAPAction: "http://fs.gov.cz/eet/OdeslaniTrzby"\r\n' in request
    assert b"Content-Length: 4\r\n" in request
    assert received["version"] in ("TLSv1.2", "TLSv1.3")
