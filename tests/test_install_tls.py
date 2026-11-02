"""deploy/install.sh keeps the TLS= choice when it is run again to update.

Running it again without TLS= used to mean TLS=auto, which swapped a name's
certificate for one with only the IP address, so the name stopped working.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parent.parent / "deploy" / "install.sh"

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")


def saved_tls(tmp_path, choice=None, san=None):
    """Run install.sh's saved_tls() with a choice file and/or a certificate."""
    text = INSTALL.read_text()
    func = re.search(r"^saved_tls\(\) \{\n.*?^\}\n", text, re.S | re.M).group(0)
    choicefile = tmp_path / "tls-choice"
    cert = tmp_path / "cert.pem"
    if choice is not None:
        choicefile.write_text(choice + "\n")
    if san is not None:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-keyout", str(tmp_path / "key.pem"), "-out", str(cert), "-days", "1",
             "-subj", "/", "-addext", f"subjectAltName={san}"],
            check=True, capture_output=True)
    script = f'TLSCHOICE="{choicefile}"\nLIVECERT="{cert}"\n{func}saved_tls\n'
    return subprocess.run(["sh", "-c", script], check=True, capture_output=True,
                          text=True).stdout.strip()


def test_a_new_machine_has_no_choice(tmp_path):
    assert saved_tls(tmp_path) == ""


def test_the_saved_choice_wins(tmp_path):
    assert saved_tls(tmp_path, choice="domain:example.org",
                     san="IP:192.0.2.7") == "domain:example.org"


def test_a_name_and_an_address_mean_both(tmp_path):
    assert saved_tls(tmp_path, san="DNS:entrance.example.org,IP:192.0.2.7") == \
        "both:entrance.example.org"


def test_a_name_alone_means_domain(tmp_path):
    assert saved_tls(tmp_path, san="DNS:entrance.example.org") == "domain:entrance.example.org"


def test_an_address_alone_means_auto(tmp_path):
    assert saved_tls(tmp_path, san="IP:192.0.2.7") == "auto"


def test_the_choice_is_saved_after_the_certificate():
    text = INSTALL.read_text()
    assert 'TLS="${TLS:-auto}"' not in text
    assert text.index('printf \'%s\\n\' "$TLS" > "$TLSCHOICE"') > text.index('case "$TLS" in\n  none)')
