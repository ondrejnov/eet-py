#!/usr/bin/env python3
"""Download the shared test certificates of the EET 2.0 playground to _cert/.

The certificates are published by the Financial Administration for all testers.
"""

from __future__ import annotations

import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

from eet import Certificate

URL = "https://eet.gov.cz/assets/cs/cmsmedia/pro-vyvojare/CAEET_Playground_2026_v1.zip"
TARGET = Path(__file__).resolve().parent.parent / "_cert"
EXPECTED_SUFFIXES = (".p12", ".crt", ".txt")
PASSWORD_FILE = "password_pokladni_cert_playground.txt"


def main() -> int:
    TARGET.mkdir(mode=0o700, exist_ok=True)

    with urllib.request.urlopen(URL, timeout=60) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))

    for member in archive.infolist():
        name = Path(member.filename).name  # no directories from the archive
        if member.is_dir() or not name.endswith(EXPECTED_SUFFIXES):
            continue
        descriptor = os.open(TARGET / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(archive.read(member))

    password = (TARGET / PASSWORD_FILE).read_text().strip()
    for path in sorted(TARGET.glob("CA_EET-Playground-*.p12")):
        print(path.name, Certificate.from_pkcs12(path, password))

    return 0


if __name__ == "__main__":
    sys.exit(main())
