"""Tariff + NodosP suite: the reference datasets must be present in Supabase.

Both checks run through the public RPCs with the service-role key (the same
door the skill scripts use). The tariff check asserts the CURATED MANIFEST
months are stored (the manifest is appended by hand when DOF publishes —
auto-discovery of the newest month is a known TODO upstream).
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://oioyawhgvazebtarigpc.supabase.co/rest/v1/rpc"
KEY = ""
p = Path("~/.cfe.env").expanduser()
if p.exists():
    for line in p.read_text().splitlines():
        if line.startswith("export NR_SERVICE_ROLE_KEY="):
            KEY = line.split("=", 1)[1].strip()

def _manifest_months():
    import json as _json
    p = Path("/Users/newman/newman-agents/newman-backend/scrapers/manifest.json")
    if not p.exists():
        return set()
    entries = _json.loads(p.read_text())
    return {(e["year"], e["month"]) for e in entries}


def _rpc(fn: str, body: dict):
    req = urllib.request.Request(
        f"{URL}/{fn}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json",
                 "apikey": KEY, "Authorization": f"Bearer {KEY}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{fn} {e.code}") from e


def _manifest_months_stored():
    months = _rpc("rpc_tarifa_months_stored", {"p_tarifa": "GDMTH"})
    if not isinstance(months, list):
        return False
    have = {(m.get("anio"), m.get("mes")) for m in months}
    need = _manifest_months()
    return bool(need) and all(mm in have for mm in need)


def _nodosp_ok():
    stats = _rpc("rpc_nodosp_stats", {})
    return stats.get("nodes", 0) > 500 and stats.get("pml_nodes", 0) > 700


WEIGHT = 10

CASES = [
    {"id": "tariff_manifest_months_stored", "points": 5,
     "fn": _manifest_months_stored,
     "expect": True},
    {"id": "nodosp_populated", "points": 5,
     "fn": _nodosp_ok, "expect": True},
]
