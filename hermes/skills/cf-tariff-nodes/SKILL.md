---
name: cf-tariff-nodes
description: "Refreshes CFE tariff prices and CENACE NodosP node data into Supabase. Runs the newman-backend tariff pipeline (DOF/CENACE manifest -> fetch -> parse -> load), pushes the fresh rows to cfe.tarifa_cuota, and upserts the NodosP catalog + PML/capacity enrichment into cfe.node. Triggers on 'refresh tariffs', 'update nodosp', the monthly cron, or when the tariff month or node catalog is stale. NOT for: invoice harvesting (see cf-invoice-harvest), KMZ generation, or market analysis questions."
version: 0.1.0
author: newman
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [CFE, tariff, NodosP, CENACE, Supabase, cron]
---

# CFE Tariff + NodosP Refresh

Keeps two reference datasets fresh in Supabase:

- `cfe.tarifa_cuota` — the current CFE tariff prices (per tarifa/division/
  year/month/cargo) from DOF/CENACE publications.
- `cfe.node` — the CENACE NodosP catalog with geocodes, PML 2025 stats,
  and capacity/revenue enrichment.

## Hard rules

- **ALL CFE/CENACE-facing egress runs ONLY on newman-ubuntu** (the only host
  with the MX residential IP). The mini never downloads from cenace.gob.mx/
  dof.gob.mx. The mini only orchestrates: builds the entries from the
  manifest and fires the box job over ssh.
- The box-side job (`/root/tariff/refresh.sh` + `/root/tariff/fetch_all.py`,
  venv `/root/tariff-venv`) fetches, parses, and POSTs to Supabase
  (Supabase is not CFE-facing). Supabase writes go through the public RPCs
  with the service-role key stored at `/root/tariff/.supabase_key` (0600,
  shipped once from the mini). Never log or reply with the key.
- Loads are idempotent upserts; re-running is always safe.

## Tariff refresh (monthly)

```bash
scripts/discover.py --url "<candidate PDF url>" ...   # verify + append
scripts/tariff_refresh.sh [year month]                # load every manifest month
```

The discovery loop (run by the monthly cron, agent-driven):

1. Search for the newest publications: `site:cenace.gob.mx
   16_MARCOREGULATORIO Suministro "Tarifas finales" <year>` plus the
   `"a partir del 1 de" <year>` phrasing (CRE-era months) with the web search
   tool — the gateway's own network, not the mini's IP (DDG rate-limits a
   single IP after a few queries).
2. For each candidate `.pdf` URL under
   `cenace.gob.mx/Docs/16_MARCOREGULATORIO/Suministro/`, run
   `discover.py --url <url>`: it downloads, checks the %PDF magic, and
   parse-verifies with the newman-backend parser (>= 1000 rows = verified).
   Verified months are appended to the manifest idempotently.
3. `tariff_refresh.sh` then loads ALL manifest months (idempotent upserts,
   chunked) into `cfe.tarifa_cuota`.

Known URL patterns (month/year are in the filename; the DOF date is not
derivable, which is why discovery is search-driven):

- `(DOF YYYY-MM-DD CFE) Tarifas finales Suministro Básico vigente desde 1
  <mes> <año>.pdf`
- `(DOF YYYY-MM-DD CRE) Acuerdo de tarifas finales del suministro básico a
  partir del 1 de <mes> <año>.pdf`

A month is FULL fidelity at ~1,750-1,906 rows (17 divisiones); the thin
96-row months come from the legacy loader and are not what this skill loads.

## NodosP refresh (monthly / on demand)

```bash
scripts/nodes_sync.py              # data/*.csv -> rpc_load_nodosp
```

Reads the three snapshot CSVs under `data/`:

- `nodos_enriched.csv` — clave -> geocoded lat/lon + zone/division/state
- `07_PML_Anual_por_Nodo.csv` — clave -> PML 2025 (avg/median/P5/P95,
  BESS spread, congestion)
- `09_Score_Nodos.csv` — clave -> headroom MW, convocatoria, revenue/MW-yr

Joins on clave, builds one JSON array, POSTs to `public.rpc_load_nodosp`
(upsert on clave). Verify with `public.rpc_nodosp_stats` — nodes > 500 and
pml_nodes > 700 expected.

Refresh the CSV snapshots from the coatl/MAPA pipeline on the Mac (the
`cfe-kmz` skill documents the upstream chain: coatl's
build_subestaciones_kmz.py re-queries Overpass + the CENACE xlsx, and the
MAPA_ELECTRICO_MX processed CSVs hold the PML/score joins) and scp them
into this skill's `data/`.

## Errors

- `pipeline_failed` — manifest/fetch/parse/load errored; read the pipeline
  output, fix the manifest entry, rerun.
- `db_unreachable` — local PG 5433 down; start it and rerun.
- `rpc_failed` — Supabase RPC returned an error; classify 401 as
  `key_expired`, 500 as `db_error`.
- `stale_data` — months stored or node counts below expectation; surface it
  instead of silently passing.

## Acceptance check

`rpc_tarifa_months_stored` includes the current publication month, and
`rpc_nodosp_stats` reports nodes > 500. The eval suite
`~/newman/evals/suite_tariff.py` encodes both.
