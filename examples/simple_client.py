#!/usr/bin/env python3
"""Send a sale to the EET playground, run it after scripts/fetch_playground_certs.py."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from eet import Certificate, Dispatcher, Receipt, ServerError, TransportError

CERT_DIR = Path(__file__).resolve().parent.parent / "_cert"

password = (CERT_DIR / "password_pokladni_cert_playground.txt").read_text().strip()
certificate = Certificate.from_pkcs12(CERT_DIR / "CA_EET-Playground-CZ00000019.p12", password)
dispatcher = Dispatcher(Dispatcher.PLAYGROUND_URL, certificate)

receipt = Receipt(
    eic_popl="CZ00000019",
    id_jednotky=181,
    id_pokl="POKLADNA-1",
    porad_cis=str(int(time.time())),
    dat_trzby=datetime.now(ZoneInfo("Europe/Prague")),
    celk_trzba=1000,
)

print("--- Verification mode ---")
print("Message can be processed:", "yes" if dispatcher.check(receipt) else "no")

print("--- Registration ---")
try:
    print("POK:", dispatcher.send(receipt))
except TransportError as e:
    # The state of the sale is unknown: send receipt.for_resend() as soon as possible.
    print("No response:", e)
except ServerError as e:
    print(f"Refused with code {e.code}: {e}")

for code, message in dispatcher.warnings.items():
    print(f"Warning {code}: {message}")

last = dispatcher.last_response
print(
    f"Request: {dispatcher.last_request_size} B, response: {dispatcher.last_response_size} B, "
    f"time: {(dispatcher.last_response_time or 0) * 1000:.0f} ms, "
    f"transaction: {last.transaction_id if last else '-'}"
)

print("--- Critical error ---")
receipt.eic_popl = "CZ00000018"
try:
    dispatcher.send(receipt)
except ServerError as e:
    print(f"Refused with code {e.code}: {e}")
