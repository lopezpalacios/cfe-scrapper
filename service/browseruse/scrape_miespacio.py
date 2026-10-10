#!/usr/bin/env python3
"""scrape_miespacio.py <rpu> — the MiEspacio leg via Browser Use Cloud (API V4).

Original plan, agent-first: Hermes claims the job; this module runs the cloud
agent (MX residential proxy, domain-scoped credential bindings) against
MiEspacio. When the portal hits its two-step verification, the code is polled
from the Workspace mailbox (the same mfa_code.py the box recipes used) and fed
back as a same-session follow-up. The deterministic parts (parsing, budgets,
Supabase writes) stay in the worker — the agent only does the interaction.

Env (never committed): BROWSER_USE_API_KEY, CFE_USER, CFE_PASS,
MAIL_INTAKE_USER, MAIL_INTAKE_APP_PASSWORD. Optional: BU_MAX_COST_USD.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent

TASK = (
    "Log in to Mi Espacio at "
    "https://app.cfe.mx/Aplicaciones/CCFE/MiEspacio/MiEspacio.aspx using the "
    "cfe_user and cfe_pass credentials. The account already has the service "
    "RPU {rpu} enrolled. Open its most recent 'recibo' (invoice) and report "
    "back as JSON with these fields: razon_social, rpu, tarifa, periodo, "
    "total. Do not download anything. Do not modify anything."
)
CODE_HINT = re.compile(
    r"(two-step|verificaci|c[oó]digo|verification code)", re.IGNORECASE)


def load_env() -> None:
    p = Path.home() / ".cfe.env"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("export "):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip()[7:], v.strip().strip('"').strip("'"))


def poll_code(timeout: int = 300) -> str | None:
    """Poll the mailbox for the 6-digit CFE code (mfa_code.py contract)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            [sys.executable, str(APP / "mfa_code.py")],
            capture_output=True, text=True, env=os.environ)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
        time.sleep(15)
    return None


def main() -> int:
    if len(sys.argv) < 2 or not re.fullmatch(r"\d{12}", sys.argv[1]):
        print("usage: scrape_miespacio.py <rpu-12-digits>", file=sys.stderr)
        return 2
    rpu = sys.argv[1]
    load_env()

    from browser_use_sdk.v4 import BrowserUse

    bindings = [
        {"alias": "cfe_user",
         "source": {"type": "inline", "value": os.environ["CFE_USER"]},
         "allowedDomains": ["app.cfe.mx"]},
        {"alias": "cfe_pass",
         "source": {"type": "inline", "value": os.environ["CFE_PASS"]},
         "allowedDomains": ["app.cfe.mx"]},
    ]
    max_cost = os.environ.get("BU_MAX_COST_USD", "0.50")

    with BrowserUse() as client:
        run = client.runs.create(
            TASK.format(rpu=rpu),
            browser_settings={"proxyCountryCode": "mx"},
            secret_bindings=bindings,
            max_cost_usd=max_cost,
        )
        session_id = getattr(run, "session_id", None) or run.id
        result = client.runs.wait_for_completion(run.id, timeout=1200)
        text = getattr(result, "result", "") or ""

        if CODE_HINT.search(text):
            code = poll_code()
            if not code:
                print(json.dumps({"rpu": rpu, "error_class": "otp",
                                  "detail": "no code in mailbox within 5 min"}))
                return 1
            follow = client.runs.create(
                f"Enter the code {code} in the two-step verification field, "
                "submit, then report the recibo summary as JSON with fields "
                "razon_social, rpu, tarifa, periodo, total.",
                session_id=session_id,
                browser_settings={"proxyCountryCode": "mx"},
                max_cost_usd=max_cost,
            )
            result = client.runs.wait_for_completion(follow.id, timeout=1200)
            text = getattr(result, "result", "") or ""

    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
