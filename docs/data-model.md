# Data model

Everything traces to the CFDI XML — the document is the source of truth. The
typed model mirrors the bill's printed boxes.

## Schemas

| Schema | Role | Key tables (rows as of 2026-10) |
|---|---|---|
| `raw_cfe` | raw XML ingest | mi_espacio 20,700 · consulta 7,065 · contigo 102 |
| `pipeline` | harvest state machines | mi_espacio/consulta/twilio/contigo_* (claims, leases, advance RPCs) |
| `cfe` | **the typed model** | invoice_document, invoice, invoice_charge, invoice_measurement, invoice_history_month, invoice_ledger_line, invoice_aviso, invoice_source, invoice_parse_run, invoice_field_catalog, invoice_issue, tarifa_cuota, tarifa_horario/temporada/factor_carga, node, cp_municipio, solar_index |
| `crm` | clients + identity + projection | rpu (814, the master identity), client (436), bill (20,575) |
| `hermes` | intake + eval ledger | jobs, eval_runs |
| `intake/design/energy/risk/orchestration` | adjacent products | — |

## The XML-derived core (parser contract `cfe-xml-v2.1.0`)

Parser output maps 1:1 to tables: `document` → invoice_document,
`invoice` → invoice, `charges` → invoice_charge, `measurements` →
invoice_measurement, `history` → invoice_history_month, `ledger_lines` →
invoice_ledger_line, `issues` → invoice_issue, `source` → invoice_source,
`field_catalog` → invoice_field_catalog (1,024 observed XML paths — the
observed schema, used for drift detection).

## The bill's boxes → tables

| Box | Table | Example (976000601419) |
|---|---|---|
| Header (cliente/RFC/tarifa/cuenta) | invoice_document + invoice | ACTIVOS JAL SA DE CV · AJA0305085R0 · HM/73 · 82DN20B018200290 |
| Detalle del consumo | invoice_measurement | 48,208 kWh TOTAL · 233 kW · 22,670 kVArh · fases 3,355/34,694/10,159 |
| Importes (9 rows) | invoice_ledger_line | Cargo Fijo 422.07 … Total 154,906.97 |
| MEM componentes | invoice_charge | ES1 422.07 · ED1 7,986.24 · EGB 9,011.03 · EGI 54,958.77 · EGP 6,321.49 · EID 45,592.84 |
| Historial (15 meses) | invoice_history_month | SEP26 48,208 kWh · FP 90.49 · PMV 2.7701 |
| Avisos | invoice_aviso | "Corte a partir del 16 OCT 2026." |
| Barcode/fechas | invoice.codigo_barras, fecha_limite/corte | 019760006014192610150001549062 · 15 OCT · 16 OCT |

Only energía carries base/intermedia/punta; capacidad (EID) and distribución
(ED1) are demand-based singles — the tariff structure, not a modeling choice.

## Identity

`crm.rpu` (id + rpu text + client FK) is the master identity. `cfe.invoice`
anchors to it via `crm_rpu_id` (trigger on write + backfill + reverse anchor
when a new crm.rpu lands). Invariant: every invoice whose rpu IS a known
client must be linked; corpus rpus not yet onboarded stay null by design.
Unique index on crm.rpu.rpu; 12-digit CHECKs (NOT VALID pending the one bad
row, id 85).

## Idempotency + canonicality

- Documents: `document_key` = CFDI UUID — the dedup key; dual-source harvests
  (consulta + mi_espacio) collapse to one document.
- Service-month: `crm.bill (rpu_id, period)` UNIQUE — the one-bill-per-period
  projection; `cfe.bill_current_period` view is the same over cfe.*.
- Canonical: `cfe.refresh_canonical` (trigger on invoice_source) — exactly one
  canonical source per document; mi_espacio wins contested documents.
- Parse queue: `invoice_parse_run` — lease-based, idempotent, `output_hash`
  stamped. 27,860 seeded; the drain runs batches of 100 with 0 failures.

## Fiscal/business split

`invoice_document` = the SAT CFDI layer (identity + totals + emisor/receptor/
uso/método/forma/sustituye — the latter columns fill with parser v2.2).
`invoice*` children = the CFE business layer. Same XML, two cadences of change
(SAT version vs CFE trama `Version_Trama`).

## Prices + audit

`tarifa_cuota` (tarifa, division, anio, mes, cargo, unidad, cuota) holds the
official DOF/CENACE publications — full-fidelity months: 2024-12, 2025-02/06/
07/09/12. The `bill-price-audit` skill recomputes every importe (cuota ×
cantidad) and foots vs subtotal; unmatched goes to a report, never a guess.
Known gaps: simple-tariff fijo + DB2 escalones (loader), fiscal columns
(parser v2.2), cancellation_status (sustituciones).
