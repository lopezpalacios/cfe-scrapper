# kameloso layer-0 evals

Frozen metric. One command, zero dependencies:

```bash
python3 run.py            # full: corpus 100, all suites
python3 run.py --quick    # skips slow (corpus) cases
```

Score = 100 max.

| Suite | Weight | What it measures |
|---|---|---|
| suite_parser | 30 | golden CFDI assertions (976000601419 → ACTIVOS JAL SA DE CV, AJA0305085R0, HM, 154906, 48208) |
| parser corpus | 25 | schema-invariant pass% over N corpus XMLs (rpu/nombre/rfc/tarifa/total/consumo/fecha all present and sane) |
| suite_barcode | 20 | real bills decode to the exact RPU; 4 degraded variants (r72, r100, gray, jpeg30) must still decode |
| suite_mfa | 15 | MFA extraction from the real email fixture; distractor colors 333333/006341 rejected; no-code → otp |
| suite_endpoint | 10 | POST /v1/rpu happy path + error taxonomy (live only; 0 until deployed) |

## Stop predicate (hill-climb)

Target: all cases green (≥ 92/100 with the endpoint live; ≥ 82.5/90 before it).
Floor: at least 8 iterations logged in decision.tsv. Never relax the predicate.

## Fixtures

`fixtures/` holds the ground truth. `cfe_*.pdf` are real CFE bills from the
operator's inbox; `golden_976000601419.xml` is the harvested CFDI;
`mfa_email.html` is a real CFE MFA email (code stale, structure golden).
These contain real client PII (RFC, razón social, address). This repo is
local-only: never push it.

## Decision log

`decision.tsv`, one row per hill-climb attempt (gitignored, append-only).
Columns: ts, phase, decision, why, evidence, result.

## Ledger

Suite totals land in Supabase `hermes.eval_runs` (schema `hermes`); rows are
inserted via the Supabase MCP, one per run.
