#!/usr/bin/env python3
"""nodes_sync.py — NodosP snapshot CSVs -> Supabase cfe.node via rpc_load_nodosp.

stdlib only. Reads data/nodos_enriched.csv + 07_PML_Anual_por_Nodo.csv +
09_Score_Nodos.csv (clave join), builds the row array, POSTs with the
service-role key from ~/.cfe.env. Idempotent upsert on clave.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SUPA_URL = os.environ.get(
    "SUPA_URL", "https://oioyawhgvazebtarigpc.supabase.co/rest/v1/rpc")


def _key() -> str:
    p = Path("~/.cfe.env").expanduser()
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("export NR_SERVICE_ROLE_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("NR_SERVICE_ROLE_KEY", "")


def _rows(path: Path) -> list:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def main() -> int:
    enriched = {r["clave"].strip(): r for r in _rows(DATA / "nodos_enriched.csv")}
    pml = {r["CLAVE"].strip(): r for r in _rows(DATA / "07_PML_Anual_por_Nodo.csv")}
    score = {r["clave"].strip(): r for r in _rows(DATA / "09_Score_Nodos.csv")}

    out = []
    for clave, e in enriched.items():
        p, s = pml.get(clave), score.get(clave)
        out.append({
            "clave": clave,
            "nombre": e.get("nombre", ""),
            "tension": e.get("kv", ""),
            "zona_carga": e.get("zona_carga", ""),
            "division": s.get("sistema", "") if s else e.get("sistema", ""),
            "municipio": e.get("municipio", ""),
            "estado": e.get("estado", ""),
            "lat": _num(e.get("lat")), "lon": _num(e.get("lon")),
            "pml_promedio": _num(p.get("PML_Promedio")) if p else None,
            "pml_mediana": _num(p.get("PML_Mediana")) if p else None,
            "pml_p5": _num(p.get("PML_P5")) if p else None,
            "pml_p95": _num(p.get("PML_P95")) if p else None,
            "bess_spread": _num(p.get("Spread_BESS")) if p else None,
            "congestion": p.get("Congestion_Avg", "") if p else "",
            "revenue_mw_yr": _num(s.get("revenue_per_MW_year_MXN")) if s else None,
            "geocode_source": e.get("source", ""),
        })
    print(f"nodes to upsert: {len(out)}")

    key = _key()
    if not key:
        sys.stderr.write("key_missing: NR_SERVICE_ROLE_KEY not found\n")
        return 1
    req = urllib.request.Request(
        f"{SUPA_URL}/rpc_load_nodosp",
        data=json.dumps({"p_rows": out}).encode(),
        headers={"content-type": "application/json",
                 "apikey": key, "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            print("RPC:", r.read().decode())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"rpc_failed: {e.code} {e.read()[:200]!r}\n")
        return 2

    req2 = urllib.request.Request(
        f"{SUPA_URL}/rpc_nodosp_stats", data=b"{}",
        headers={"content-type": "application/json",
                 "apikey": key, "Authorization": f"Bearer {key}"},
        method="POST")
    with urllib.request.urlopen(req2, timeout=60) as r:
        print("stats:", r.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
