# cfe-scrapper

RPU in → complete CFE receipt data in the database → stats on a dashboard.

Input a 12-digit CFE service number (RPU); the system scrapes the official
sources (CFE Contigo app on a real phone, MiEspacio portal from a Mexican
residential IP, CENACE/DOF publications), stores everything in Supabase in a
typed, auditable data model, and serves invoice stats/history on a dashboard.

## The pipeline

```
POST /v1/rpu {rpu, webhook?}          (edge function rpu-intake -> hermes.jobs)
        │
        ▼
Hermes worker (Mac mini, user newman) ── WhatsApp: invoice arrives, barcode -> RPU
        │                                  ── cf-invoice-harvest skill
        ├── phone: adb -> CFE Contigo -> nombre/total -> CFDI by email
        ├── box:   ssh newman-ubuntu (MX residential IP) -> MiEspacio login
        │          + MFA (IMAP) + enrollment + ~6-download batches
        └── CFDI parse -> JSON {rpu, nombre, rfc, direccion, tarifa,
             periodo[], total, consumo_kwh, historial[]} -> Supabase
        │
        ▼
Supabase (project oioyawhgvazebtarigpc)
  raw_cfe.*        raw XML (27,867 sources)
  pipeline.*       harvest state machines (claims/leases/RPCs)
  cfe.*            the typed model, XML-derived (document/invoice/charges/
                   measurements/history/ledger/aviso + tarifa_cuota + node)
  crm.*            clients, RPU identity, the service-month projection
  hermes.*         job queue (rpu-intake) + eval ledger
        │
        ▼
bill-price-audit skill: every importe recomputed against official cuotas
        │
        ▼
Dashboard: invoice stats + history, rendered from cfe.* (this repo, phase 3)
```

## Hard rules

- ALL CFE/CENACE-facing egress from `newman-ubuntu` only (the MX residential
  IP). The mini never fetches from cenace/cfe/DOF.
- MiEspacio: ~6 downloads per session, then re-login; respect 503 Retry-After.
- Never log CFE passwords or the service-role key. Barcode = RPU's carrier only.
- The document is the CFDI XML: every field traces to it (see docs/data-model.md).

## Repo layout

- `docs/` — architecture, data model, evals, deployment, security
- `hermes/skills/` — the three Hermes skills (cf-invoice-harvest,
  cf-tariff-nodes, bill-price-audit) + the nix flake
- `supabase/` — migrations + the rpu-intake edge function
- `evals/` — the frozen layer-0 + data-model eval suites (fixtures excluded:
  they hold real client PII)
- `PLAN.md` — the pstack multi-phase plan to the REST API + dashboard
