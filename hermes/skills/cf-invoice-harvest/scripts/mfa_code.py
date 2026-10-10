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
    # hermes@newman.re reads its own inbox via the gws CLI (gmail.readonly
    # scope on the agent's OAuth token). The per-RPU group (rpu-<n>@newman.re)
    # delivers the portal code to the member inbox; no app password needed.
    import subprocess as _sp
    out = _sp.run(
        [os.path.expanduser("~/.local/bin/gws"), "gmail", "+triage", "--format", "json"],
        capture_output=True, text=True,
        env=dict(os.environ, GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND="file"))
    if out.returncode != 0:
        sys.stderr.write(f"otp: gws triage failed: {out.stderr.strip()[:200]}\n")
        return None
    import json as _json
    try:
        inbox = _json.loads(out.stdout)
    except _json.JSONDecodeError:
        sys.stderr.write("otp: gws returned non-JSON\n")
        return None
    msgs = inbox if isinstance(inbox, list) else (inbox.get("messages") or [])
    cand = [m for m in msgs
            if (m.get("from") or {}).get("email", "").lower() == "autenticacion@cfe.mx"
            and "mfa" in (m.get("subject") or "").lower()]
    if not cand:
        sys.stderr.write("otp: no CFE MFA email in hermes@newman.re inbox\n")
        return None
    msg_id = cand[0]["id"]
    out = _sp.run(
        [os.path.expanduser("~/.local/bin/gws"), "gmail", "+read", "--id", msg_id,
         "--format", "json"],
        capture_output=True, text=True,
        env=dict(os.environ, GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND="file"))
    try:
        return (_json.loads(out.stdout).get("body_html") or "")
    except _json.JSONDecodeError:
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
