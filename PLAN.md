# cfe-scrapper — multi-phase plan (pstack)

Goal: a REST API where an RPU goes in and the full receipt data comes back,
everything stored in the typed Supabase model, plus a basic dashboard showing
invoice stats/history and the data model.

Each phase ends in a verifiable unit: a check that can pass or fail, and the
eval suite that owns it. One commit per accepted change.

## Phase 0 — repo + docs (this commit)

Deliverable: this repo, documenting what exists, with the frozen evals.
Check: `python3 evals/run.py --quick` green on the mini; the docs reproduce
every live system (paths, keys, migrations).

## Phase 1 — worker that drains hermes.jobs

What exists: the intake endpoint enqueues `hermes.jobs` (idempotent per active
RPU, `source`-tagged); the harvest legs are proven separately (Contigo phone
driver, MiEspacio login/enroll/harvest on the box, CFDI parser v2.1, MFA via
IMAP). What's missing: the worker loop that claims a job and runs the
cf-invoice-harvest pipeline end to end, marking the job done/failed with the
classified error.

Steps:
1. `service/worker.py` in the image: claim (lease, idempotent) -> vpn_guard
   (egress must be MX) -> barcode/decode (if media) -> MiEspacio harvest
   (service/harvest/harvest.js) -> parse -> rpc/DB write -> job state
   transition. No box dependency.
2. Deploy: `nix build .#scraper-image && docker load < result && docker run
   --env-file .env` on the mini with NordVPN-MX up.
3. Eval: `suite_worker` — a golden end-to-end (976000601419) run gated on
   the phone's NordVPN-MX and a fresh MFA window; layer-0 gates every edit.

Check: a POSTed RPU ends `hermes.jobs` state = done with a row per month in
`cfe.invoice`, or a classified failure. Cost-bounded (~6 downloads/session).

## Phase 2 — REST API surface

1. `GET /v1/rpu/{rpu}` edge function: reads the typed model (invoice history,
   latest bill, tarifa, canonical source) — service-role, key-authed like the
   intake.
2. `GET /v1/rpu/{rpu}/cfdi/{uuid}` returns the CFDI JSON + file links.
3. Rate limits + the allowlist on the intake (already key-authed).
Check: `curl` against the deployed functions with the key; suite_endpoint
extended with the read paths.

## Phase 3 — dashboard

Basic static dashboard (vanilla HTML+JS, no framework) served from an edge
function or Supabase hosting:
1. Stats row: invoices stored, RPUs enrolled, months covered, latest harvest,
   audit status.
2. Per-RPU view: consumption history (the 15-month box), importes (the 9
   ledger rows), audit deltas, CFDI list with links.
3. A "data model" page rendering docs/data-model.md's schema live (tables +
   row counts).
Auth: same key mechanism; the dashboard ships with the key out of the client
bundle (server-side fetch proxy).
Check: `medboard-ui-check`-style smoke: the dashboard renders the golden RPU
(976000601419) with the expected values (ACTIVOS JAL SA DE CV, 154906.47).

## Phase 4 — close the known gaps (each is one hill-climb iteration)

1. Parser v2.2: fiscal header (emisor/receptor/uso/metodo/forma/sustituye)
   into the document payload; re-parse pass fills the fiscal columns.
2. Tariff loader: simple-tariff sections (cargo fijo + escalones for
   PDBT/DB2/DB1...) so the bill-price-audit's ES column audites clean.
3. Audit escalones: DB2 tiered energy recomputation.
4. Cancellation_status from the sustitución relationship; bill_current_period
   filters replaced bills.
5. crm.bill backfill for the corpus RPUs (reconciliation target 100%).

## Standing decisions (operator-owned)

- Canonical rule: mi_espacio wins contested documents (implemented; reversible).
- Bill ownership: cfe.invoice canonical, crm.bill the engine projection.
- crm.rpu id=85 bad rpu (8100709807531) needs the true 12-digit value.
- WhatsApp pairing + bot number (parked).
