"""Parser suites: golden assertions + corpus schema invariants.

The golden CFDI is the source of truth fixture; the corpus pass% measures how
well the KV map generalizes across tariffs/bill shapes.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_SCRIPTS = Path(
    "/Users/newman/.hermes/skills/cf-invoice-harvest/scripts")
sys.path.insert(0, str(SKILL_SCRIPTS))
from parse_cfdi import parse_one  # noqa: E402

GOLDEN = HERE / "fixtures" / "golden_976000601419.xml"

KNOWN_TARIFAS = {
    "68", "71", "72", "73", "74", "75", "77", "78",      # SICOM codes
    "HM", "HMC", "GDMTH", "OM", "PDBT", "DAC", "02", "01", "03", "07", "E1",
}


def _golden():
    b = parse_one(GOLDEN)
    return b


def _invariant_ok(path: Path) -> tuple:
    """Run parse_one and check schema invariants. Returns (ok, why)."""
    b = parse_one(path)
    if not b.get("rpu") or not re.fullmatch(r"\d{12}", b["rpu"] or ""):
        return False, "rpu"
    if not b.get("nombre"):
        return False, "nombre"
    if not b.get("rfc"):
        return False, "rfc"
    if not b.get("tarifa"):
        return False, "tarifa"
    if not b.get("total") or b["total"] <= 0:
        return False, "total"
    if b.get("consumo_kwh") is None or b["consumo_kwh"] < 0:
        return False, "consumo"
    if not b.get("fecha"):
        return False, "fecha"
    return True, ""


def _corpus_pass_ratio(n: int):
    corpus = Path(os.environ.get(
        "CFE_CORPUS_DIR", "/Users/newman/cfe-gd-scrape/dl"))
    xmls = sorted(corpus.rglob("*.xml"))
    sample = xmls[:n]
    if not sample:
        raise RuntimeError(f"no XMLs under {corpus}")
    ok = 0
    counted = 0
    skipped_empty = 0
    for p in sample:
        if p.stat().st_size == 0:
            skipped_empty += 1  # failed download, not a CFDI
            continue
        counted += 1
        try:
            good, _ = _invariant_ok(p)
        except Exception:
            good = False
        ok += good
    if skipped_empty:
        print(f"  corpus: skipped {skipped_empty} empty (harvest-artifact) files")
    if counted == 0:
        raise RuntimeError("no non-empty XMLs in corpus sample")
    return ok / counted


WEIGHT = 30 + 25

CASES = [
    {"id": "golden_rpu", "points": 5,
     "fn": lambda: _golden()["rpu"], "expect": "976000601419"},
    {"id": "golden_nombre", "points": 5,
     "fn": lambda: _golden()["nombre"], "expect": "ACTIVOS JAL SA DE CV"},
    {"id": "golden_rfc", "points": 5,
     "fn": lambda: _golden()["rfc"], "expect": "AJA0305085R0"},
    {"id": "golden_tarifa", "points": 5,
     "fn": lambda: _golden()["tarifa"], "expect": "HM"},
    {"id": "golden_total_enrol", "points": 5,
     "fn": lambda: _golden()["total_enrol"], "expect": "154906"},
    {"id": "golden_consumo", "points": 5,
     "fn": lambda: _golden()["consumo_kwh"], "expect": 48208},
    {"id": "corpus_invariants", "points": 25,
     "fn": lambda: round(_corpus_pass_ratio(int(os.environ.get("CFE_CORPUS_N", "100"))) * 25, 1),
     "expect": 25.0, "slow": True},
]
