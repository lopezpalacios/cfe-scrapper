"""Endpoint suite: POST /v1/rpu happy path + error taxonomy (live).

Reads INTAKE_KEY from ~/.cfe.env and the URL from env RPU_INTAKE_URL
(default: the deployed Supabase edge function).
"""
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get(
    "RPU_INTAKE_URL",
    "https://oioyawhgvazebtarigpc.supabase.co/functions/v1/rpu-intake")
KEY = os.environ.get("INTAKE_KEY") or ""


def _key() -> str:
    global KEY
    if not KEY:
        p = Path("~/.cfe.env").expanduser()
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("export INTAKE_KEY="):
                    KEY = line.split("=", 1)[1].strip()
    if not KEY:
        raise RuntimeError("INTAKE_KEY missing from ~/.cfe.env")
    return KEY


def _fresh_rpu() -> str:
    return "0" + "".join(secrets.choice("0123456789") for _ in range(11))


def _post(body, key=None):
    key = key if key is not None else _key()
    headers = {"content-type": "application/json"}
    if key:
        headers["x-intake-key"] = key
    payload = dict(body, source="eval")  # eval rows are cleanable by source
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _happy():
    rpu = _fresh_rpu()
    code, body = _post({"rpu": rpu, "webhook": "https://example.com/hook"})
    return (code, isinstance(body.get("job_id"), str),
            body.get("rpu") == rpu, body.get("state") == "queued")


def _idempotent():
    rpu = _fresh_rpu()
    _, b1 = _post({"rpu": rpu})
    c2, b2 = _post({"rpu": rpu})
    return (b1.get("job_id") == b2.get("job_id"), c2, b2.get("duplicate") is True)


def _err(body):
    code, resp = _post(body)
    return code, resp.get("error")


def _bad_key():
    code, resp = _post({"rpu": "123456789012"}, key="wrong-key")
    return code, resp.get("error")


WEIGHT = 10

CASES = [
    {"id": "happy_creates_job", "points": 2.5,
     "fn": _happy, "expect": (201, True, True, True)},
    {"id": "duplicate_idempotent", "points": 2,
     "fn": _idempotent, "expect": (True, 202, True)},
    {"id": "invalid_rpu_rejected", "points": 1.5,
     "fn": lambda: _err({"rpu": "12345"}), "expect": (400, "invalid_rpu")},
    {"id": "missing_rpu_rejected", "points": 1.5,
     "fn": lambda: _err({"webhook": "https://example.com/hook"}),
     "expect": (400, "invalid_rpu")},
    {"id": "bad_webhook_rejected", "points": 1.5,
     "fn": lambda: _err({"rpu": "123456789012", "webhook": "ftp://nope"}),
     "expect": (400, "invalid_webhook")},
    {"id": "bad_key_rejected", "points": 1,
     "fn": _bad_key, "expect": (401, "unauthorized")},
]
