---
name: bill-price-audit
description: "Audits every parsed CFE bill's Importes against the OFFICIAL tariff prices: recomputes each component (cuota oficial x cantidad facturada) from cfe.tarifa_cuota and cfe.invoice_measurement, compares with cfe.invoice_charge.amount_total, and foots the total. Triggers on 'audit the bills', 'audit the importes', 'check bills against the tariff', the post-refresh cron, or any request to verify bill amounts. NOT for: harvesting, enrollment, tariff discovery itself (see cf-tariff-nodes), or generic SQL questions."
version: 0.1.0
author: newman
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [CFE, tariff, audit, invoice, prices]
---

# Bill Price Audit

For each parsed bill, recompute every component's importe from the published
cuota and the bill's own measured quantities, and flag deltas.

## Precondition: the month must be loaded full-fidelity

Only these months have full tariff rows (~1,750+/month) in `cfe.tarifa_cuota`:
2024-12, 2025-02, 2025-06, 2025-07, 2025-09, 2025-12. Thin 96-row legacy
months are NOT auditable.

**If the newest published month is missing from that set, pull it FIRST**:
run the cf-tariff-nodes discovery + refresh for the most recent month
(search for the publication → `discover.py --url <candidate>` for each
candidate → `tariff_refresh.sh`), THEN audit. The audit never runs against
an absent month.

## Data

- Bills: `cfe.invoice` (rpu, tarifa_reg, division_code, period_label, cuenta)
- Components: `cfe.invoice_charge` (component_code, period_code, basis_kind,
  amount_total)
- Quantities: `cfe.invoice_measurement` (measurement_kind, period_code,
  value, unit)
- Prices: `cfe.tarifa_cuota` (tarifa, division, anio, mes, cargo, unidad,
  cuota)
- Division code→name: `~/newman-agents/newman-backend/resource/
  cfe_division_codes.py` (DN → Valle de México Sur, DX → Jalisco, ...)

## Matching (see references/cargo-mapping.md for the full table)

component ES fixed → cargo 'fijo'/'suministradora', $/mes
ED kw → 'distribuci…', $/kW
ET → '<periodo> transmision' (or bare 'transmision'), $/kWh
EC → '<periodo> cenace', $/kWh
EG → '<periodo> energia' (incl. 'Base/Intermedia/Punta Variable (Energía)'), $/kWh
EI → 'capacidad', $/kW
EM → 'conexos' if present, else unmatched

Period: B→base, I→intermedio, P→punta, S→semipunta. Quantity = the same
(period, kind) measurement (energy_active for $/kWh, demand_billed for
$/kW, 1 for fixed). Expected = cuota × quantity.

Cargo strings come from two loader eras and division names mix case and
accents — normalize (lower, strip accents) before matching. NEVER invent a
match: an unmatched component or missing month goes to the unmatched list,
not zero.

## Foot check

Per bill: sum(charge amounts) × 1.16 vs the document total (the bill's own
printed total). Delta > 0.5% = flag.

## Run

```bash
python3 scripts/audit_bills.py [--limit N] [--month YYYY-MM]
```

Output: per-bill table (component | cuota | unidad | cantidad | esperado |
impreso | delta%) + verdict, then a corpus summary (bills audited, bills
footing, worst deltas) and the unmatched list.
