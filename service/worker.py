#!/usr/bin/env python3
"""worker.py — the scraper service entrypoint (runs inside the nix image).

Loop: vpn_guard -> claim hermes.jobs -> harvest pipeline -> typed Supabase
write -> job state transition with a classified error. Every CFE fetch is
gated on the mini's NordVPN being connected to a MEXICO server.

Env (never baked into the image):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, TWOCAPTCHA_KEY,
  CFE_USER, CFE_PASS, MAIL_INTAKE_USER, MAIL_INTAKE_APP_PASSWORD,
  ADB_SERIAL (default RFGL41FS57N), CHROME_PATH, POLL_SECONDS, LEASE_MINUTES.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent

ERRORS = {
    "barcode_unreadable", "captcha", "otp", "session_expired",
    "geo_blocked", "enroll_mismatch", "harvest_incomplete",
}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


# ── egress guard: CFE fetches only while the egress IP is MX ──────────────
def vpn_guard(timeout: int = 10) -> str:
    """Returns the egress country code. Raises on failure to determine it."""
    req = urllib.request.Request(
        "https://ipinfo.io/json",
        headers={"User-Agent": "cfe-scrapper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("country", "")


def require_mx() -> None:
    country = vpn_guard()
    if country != "MX":
        raise PermissionError(f"geo_blocked: egress is {country}, need MX "
                              "(NordVPN on the mini -> Mexico server)")


# ── job queue (hermes.jobs via the service-role REST door) ────────────────
def supabase_rpc(fn: str, body: dict) -> dict:
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    url = f"{env('SUPABASE_URL')}/rest/v1/rpc/{fn}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"content-type": "application/json",
                 "apikey": key, "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{fn}: {e.code} {e.read()[:200]!r}") from e


# ── harvest legs (thin wrappers over the proven recipes in harvest/) ───────
def run_harvest(rpu: str) -> dict:
    """MiEspacio cycle. Returns the parsed-bill summary."""
    node = os.environ.get("NODE_BIN", "node")
    out = subprocess.run(
        [node, str(APP / "harvest" / "harvest.js"), rpu],
        capture_output=True, text=True, timeout=1800,
        env={**os.environ, "SUPABASE_URL": env("SUPABASE_URL"),
             "SUPABASE_SERVICE_ROLE_KEY": env("SUPABASE_SERVICE_ROLE_KEY")})
    if out.returncode != 0:
        raise RuntimeError(f"harvest failed: {(out.stderr or out.stdout)[:300]}")
    return json.loads(out.stdout.splitlines()[-1])


def decode_rpu(media: str) -> str:
    out = subprocess.run(
        ["bash", str(APP / "decode_rpu.sh"), media],
        capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise ValueError("barcode_unreadable")
    return out.stdout.strip()


# ── loop ───────────────────────────────────────────────────────────────────
def main() -> int:
    poll = int(env("POLL_SECONDS", "30"))
    log("worker up")
    while True:
        try:
            require_mx()
        except PermissionError as exc:
            log(f"vpn: {exc}")
            time.sleep(poll * 4)
            continue
        try:
            job = supabase_rpc("rpc_claim_scrape_job", {})
        except RuntimeError as exc:
            log(f"claim: {exc}")
            time.sleep(poll)
            continue
        if not job:
            time.sleep(poll)
            continue
        jid = job.get("job_id")
        rpu = job.get("rpu")
        log(f"claimed {jid} rpu={rpu}")
        try:
            if job.get("media"):
                rpu = decode_rpu(job["media"])
            summary = run_harvest(rpu)
            supabase_rpc("rpc_finish_scrape_job",
                         {"p_id": jid, "p_state": "done",
                          "p_summary": summary})
            log(f"{jid} done: {summary.get('months')} months")
        except Exception as exc:
            cls = "unknown"
            for e in ERRORS:
                if e in str(exc):
                    cls = e
                    break
            supabase_rpc("rpc_finish_scrape_job",
                         {"p_id": jid, "p_state": "failed",
                          "p_error_class": cls, "p_detail": str(exc)[:500]})
            log(f"{jid} failed [{cls}]: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
