#!/usr/bin/env python3
"""gws_groups.py — create the per-RPU Workspace groups via the Directory API.

Uses the mini's gws OAuth credentials (~/.config/gws: client_secret.json +
credentials.json, written by `gws auth login` as hermes@newman.re with the
admin.directory scopes). stdlib only.

Usage:
  gws_groups.py create <rpu>            one group rpu-<rpu>@newman.re
  gws_groups.py create --all            every enrolled rpu (crm.rpu, live DB)
  gws_groups.py list                    show existing rpu-* groups
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DOMAIN = "newman.re"
MEMBER = "hermes@newman.re"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DIR_URL = "https://admin.googleapis.com/admin/directory/v1"


def _load_creds() -> tuple[str, str, str]:
    # gws stores the live auth encrypted (credentials.enc, file keyring);
    # export it unmasked through the CLI itself (never printed to logs).
    import subprocess
    env = dict(os.environ, GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND="file")
    out = subprocess.run(
        ["gws", "auth", "export", "--unmasked"],
        capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"gws export failed: {out.stderr[:200]}")
    creds = json.loads(out.stdout)
    return creds["client_id"], creds["client_secret"], creds["refresh_token"]


def _access_token() -> str:
    cid, csec, rtok = _load_creds()
    data = urllib.parse.urlencode({
        "client_id": cid, "client_secret": csec,
        "refresh_token": rtok, "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def _call(method: str, path: str, body: dict | None = None) -> dict:
    tok = _access_token()
    req = urllib.request.Request(
        f"{DIR_URL}/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"directory {method} {path}: {e.code} "
                         f"{e.read()[:200]!r}\n")
        raise


def create_group(rpu: str) -> str:
    email = f"rpu-{rpu}@{DOMAIN}"
    _call("POST", "groups", {
        "email": email,
        "name": f"CFE RPU {rpu}",
        "description": "Inbox for the CFE portal verification codes of this RPU",
    })
    import time
    for attempt in range(5):
        try:
            _call("POST", f"groups/{email}/members", {"email": MEMBER, "role": "MEMBER"})
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2)
    print(email)
    return email


def list_groups() -> list[str]:
    out = _call("GET", "groups?domain=" + DOMAIN)
    return [g["email"] for g in out.get("groups", [])
            if g["email"].startswith("rpu-")]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if sys.argv[1] == "list":
        for g in list_groups():
            print(g)
        return 0
    if sys.argv[1] == "create":
        rpus = sys.argv[2:]
        if rpus == ["--all"]:
            import psycopg2
            dsn = None
            for line in Path("~/.cfe.env").expanduser().read_text().splitlines():
                if line.startswith("export DATABASE_URL="):
                    dsn = line.split("=", 1)[1].strip()
            conn = psycopg2.connect(dsn or os.environ["DATABASE_URL"])
            with conn.cursor() as cur:
                cur.execute("select rpu from crm.rpu where rpu ~ '^[0-9]{12}$' "
                            "order by id")
                rpus = [r[0] for r in cur.fetchall()]
            conn.close()
        existing = set(list_groups())
        made = 0
        for rpu in rpus:
            email = f"rpu-{rpu}@{DOMAIN}"
            if email in existing:
                print(f"skip {email} (exists)")
                continue
            try:
                create_group(rpu)
                made += 1
            except Exception as exc:
                print(f"FAIL {email}: {exc}")
        print(f"created {made}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
