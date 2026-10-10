#!/usr/bin/env python3
"""mfa_code.py — read the newest CFE MiEspacio MFA code from jesus@newman.re.

Proven recipe (kameloso handover §4B + mail_intake.py): IMAP over Gmail with
the workspace app password (MAIL_INTAKE_USER / MAIL_INTAKE_APP_PASSWORD from
~/.cfe.env). The 6-digit code sits right after `letter-spacing:8px` in the
email HTML; the CSS-color candidates 333333/006341 are distractors.

Prints the 6 digits, or exits 1 with a reason on stderr (otp classification).
stdlib only.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import sys
from pathlib import Path

IMAP_HOST = "imap.gmail.com"


def _load_env() -> None:
    for path in ("~/.cfe.env", "~/.newman-pipeline.env"):
        p = os.path.expanduser(path)
        if os.path.exists(p):
            with open(p) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[len("export "):]
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def fetch_latest_mfa_html() -> str | None:
    user = os.environ.get("MAIL_INTAKE_USER")
    pwd = os.environ.get("MAIL_INTAKE_APP_PASSWORD")
    if not user or not pwd:
        sys.stderr.write("otp: MAIL_INTAKE_* env missing\n")
        return None
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        imap.login(user, pwd)
        imap.select('INBOX')
        status, data = imap.search(None, '(FROM "autenticacion@cfe.mx")')
        if status != "OK" or not data or not data[0]:
            sys.stderr.write("otp: no CFE MFA email in inbox\n")
            return None
        ids = data[0].split()
        latest = ids[-1]  # search returns ascending; newest is last
        status, msg = imap.fetch(latest, "(RFC822)")
        if status != "OK":
            sys.stderr.write("otp: fetch failed\n")
            return None
        raw = msg[0][1]
        m = email.message_from_bytes(raw)  # compat32: no email.policy dep
        for part in m.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        sys.stderr.write("otp: email has no HTML body\n")
        return None
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def main() -> int:
    _load_env()
    if "--html-file" in sys.argv:
        idx = sys.argv.index("--html-file")
        html = Path(sys.argv[idx + 1]).read_text(encoding="utf-8", errors="replace")
    else:
        html = fetch_latest_mfa_html()
        if not html:
            return 1

    m = re.search(r"letter-spacing:\s*8px[^>]*>\s*(\d{6})\s*<", html, re.S)
    if m and m.group(1) not in ("333333", "006341"):
        print(m.group(1))
        return 0

    text = re.sub(r"<[^>]+>", " ", html)
    for cand in re.findall(r"\b\d{6}\b", text):
        if cand not in ("333333", "006341"):
            print(cand)
            return 0

    sys.stderr.write("otp: no 6-digit code found in MFA email\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
