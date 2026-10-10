#!/usr/bin/env python3
"""kameloso layer-0 eval runner — zero-dependency, frozen metric.

Score = sum over suites (max 230, fixed denominator):
  suite_parser     55  golden assertions + corpus invariants
  suite_barcode    20  real bills + degraded variants + never-wrong safety
  suite_mfa        15  MFA extraction incl. distractor rejection
  suite_endpoint   10  POST /v1/rpu happy path + error taxonomy
  suite_tariff     10  manifest months stored + NodosP populated
  suite_datamodel  10  typed model populated + failure rate
  suite_dm_health 100  canonical/identity/population/enum health

Usage: python3 run.py [--corpus N] [--quick]
Prints per-case results, suite totals, and the overall score as the last line:
SCORE 87.5/100
Exits 0 iff every case passes, else 1.
"""
import importlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> int:
    quick = "--quick" in sys.argv
    corpus_n = 100
    if "--corpus" in sys.argv:
        corpus_n = int(sys.argv[sys.argv.index("--corpus") + 1])
    os.environ["CFE_CORPUS_DIR"] = os.environ.get(
        "CFE_CORPUS_DIR", "/Users/newman/cfe-gd-scrape/dl")
    os.environ["CFE_CORPUS_N"] = str(corpus_n)

    suites = ["suite_parser", "suite_barcode", "suite_mfa", "suite_endpoint", "suite_tariff", "suite_datamodel", "suite_dm_health"]
    total = 0.0
    # frozen denominator: a missing/broken suite scores 0, never shrinks it
    max_total = 230.0
    failed = 0
    for name in suites:
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            print(f"SUITE {name}: SKIP ({exc})")
            continue
        weight = float(getattr(mod, "WEIGHT", 0))
        suite_pts = 0.0
        for case in getattr(mod, "CASES", []):
            if quick and case.get("slow"):
                print(f"  [{name}] {case['id']}: SKIP (quick mode)")
                continue
            try:
                actual = case["fn"]()
                ok = actual == case["expect"]
                if not ok:
                    failed += 1
                pts = case["points"] if ok else 0.0
                suite_pts += pts
                print(f"  [{name}] {case['id']}: "
                      f"{'PASS' if ok else 'FAIL'} ({pts:g} pts)"
                      f"{'' if ok else f'  got={actual!r} want={case['expect']!r}'}")
            except Exception as exc:  # a crashing case is a failing case
                failed += 1
                print(f"  [{name}] {case['id']}: ERROR ({type(exc).__name__}: {exc})")
        suite_pts = min(suite_pts, weight)
        total += suite_pts
        print(f"SUITE {name}: {suite_pts:g}/{weight:g}\n")
    print(f"SCORE {total:g}/{max_total:g}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
