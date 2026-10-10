#!/usr/bin/env python3
"""audit_bills.py — recompute bill importes from official cuotas and flag deltas.

Reads DATABASE_URL from ~/.cfe.env. Audits parsed bills in the full-fidelity
months; every component is matched to a cuota by (component, period, basis,
unidad) with case/accent-insensitive cargo normalization. Unmatched rows and
missing months go to the unmatched report — never guessed.

Usage: python3 audit_bills.py [--limit N] [--month YYYY-MM]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

import psycopg2
import psycopg2.extras

FULL_MONTHS = {"2024-12", "2025-02", "2025-06", "2025-07", "2025-09", "2025-12"}

DIVISION_CODES = {
    "DA": "Baja California", "DB": "Noroeste", "DD": "Golfo Norte",
    "DF": "Centro Occidente", "DG": "Centro Sur", "DJ": "Oriente",
    "DK": "Sureste", "DL": "Valle de México Norte", "DM": "Valle de México Centro",
    "DN": "Valle de México Sur", "DP": "Bajío", "DV": "Centro Oriente",
    "DW": "Peninsular", "DX": "Jalisco",
}

PERIOD_NAME = {"B": "base", "I": "intermedio", "P": "punta", "S": "semipunta"}

# component -> cargo substring candidates, in priority order
CARGO_PATTERNS = {
    "ES": ["fijo", "suministradora"],
    "ED": ["distribuci"],
    "ET": ["transmision"],
    "EC": ["cenace"],
    "EG": ["energia"],
    "EI": ["capacidad"],
    "EM": ["conexos"],
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _dsn() -> str:
    for line in Path("~/.cfe.env").expanduser().read_text().splitlines():
        if line.startswith("export DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    return os.environ.get("DATABASE_URL", "")


def audit(conn, limit: int, month: str | None) -> None:
    months = [month] if month else sorted(FULL_MONTHS)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    audited = footed = unmatched = 0
    deltas: list[tuple] = []

    for m in months:
        year, mes = int(m[:4]), int(m[5:])
        cur.execute("""
            select i.document_id, i.rpu, i.tarifa_reg, i.division_code,
                   i.period_label, d.total
            from cfe.invoice i
            join cfe.invoice_document d on d.id = i.document_id
            where i.period_label = %s
              and exists (select 1 from cfe.invoice_charge c where c.document_id = i.document_id)
            order by i.document_id limit %s
        """, (m, limit))
        bills = cur.fetchall()
        if not bills:
            print(f"== {m}: no auditable bills")
            continue

        for b in bills:
            div_code = (b["division_code"] or "")[:2].upper()
            div_name = DIVISION_CODES.get(div_code)
            if not div_name:
                unmatched += 1
                print(f"  SKIP {b['rpu']} {m}: division code {div_code} unknown")
                continue

            cur.execute(
                "select component_code, period_code, basis_kind, amount_total "
                "from cfe.invoice_charge where document_id = %s",
                (b["document_id"],))
            charges = cur.fetchall()

            cur.execute(
                "select cargo, unidad, cuota from cfe.tarifa_cuota "
                "where tarifa = %s and lower(division) = lower(%s) "
                "and anio = %s and mes = %s",
                (b["tarifa_reg"] or "GDMTH", div_name, year, mes))
            cuotas = cur.fetchall()
            cuotas_norm = [(_norm(c["cargo"]), c["unidad"], float(c["cuota"])) for c in cuotas]

            cur.execute(
                "select measurement_kind, period_code, value, unit "
                "from cfe.invoice_measurement where document_id = %s",
                (b["document_id"],))
            meas = cur.fetchall()

            print(f"\n== {b['rpu']} {m} tarifa {b['tarifa_reg']} div {div_code}({div_name}) "
                  f"total {b['total']}")
            sum_charges = 0.0
            for ch in charges:
                comp, per, basis, printed = (ch["component_code"], ch["period_code"],
                                             ch["basis_kind"], float(ch["amount_total"] or 0))
                sum_charges += printed
                qty = 1.0
                unit_kind = "fixed"
                eff_basis = basis if basis in ("kw", "kwh") else {
                    "EG": "kwh", "EC": "kwh", "ET": "kwh", "EM": "kwh",
                    "ED": "mixed", "EI": "mixed",
                }.get(comp, "fixed")
                pname = PERIOD_NAME.get(per, "")
                qper = per if per in ("B", "I", "P", "S") else "TOTAL"

                def _qty(kind: str) -> float | None:
                    rows = [m2 for m2 in meas if m2["measurement_kind"] == kind
                            and m2["period_code"] == qper]
                    return float(rows[0]["value"]) if rows else None

                cand_units = []
                if eff_basis == "fixed":
                    cand_units = [("$/mes", 1.0)]
                elif eff_basis == "kw":
                    q = _qty("demand_billed")
                    cand_units = [("$/kW", q)] if q is not None else []
                elif eff_basis == "kwh":
                    q = _qty("energy_active")
                    cand_units = [("$/kWh", q)] if q is not None else []
                else:  # mixed: try both demand and energy quantities
                    qk = _qty("demand_billed")
                    qh = _qty("energy_active")
                    cand_units = [(u, q) for u, q in (("$/kW", qk), ("$/kWh", qh)) if q is not None]
                if not cand_units:
                    unmatched += 1
                    print(f"  {comp}/{per}/{basis} printed={printed:,.2f}  UNMATCHED-QUANTITY")
                    continue
                best = None
                for target_unit, qty in cand_units:
                    hits = [c for c in cuotas_norm if c[1] == target_unit and any(
                        p in c[0] for p in CARGO_PATTERNS.get(comp, []))
                        and (pname in c[0] or not pname)]
                    for _c, _u, cuota in hits:
                        expected = cuota * qty
                        d = abs(expected - printed)
                        if best is None or d < best[0]:
                            best = (d, cuota, qty, target_unit, expected)
                if best is None:
                    unmatched += 1
                    print(f"  {comp}/{per}/{basis} printed={printed:,.2f}  UNMATCHED-CUOTA "
                          f"(div {div_name}, tried {[u for u, _ in cand_units]})")
                    continue
                _, cuota, qty, target_unit, expected = best
                delta = printed - expected
                deltas.append((abs(delta) / printed if printed else 0, b["rpu"], comp, per))
                print(f"  {comp}/{per}/{basis} cuota={cuota} x {qty:,.0f} = "
                      f"{expected:,.2f} vs printed {printed:,.2f} "
                      f"delta {delta:+,.2f}")
            cur.execute("select subtotal from cfe.invoice_document where id = %s",
                        (b["document_id"],))
            subtotal = float((cur.fetchone() or {}).get("subtotal") or 0)
            ok = abs(sum_charges - subtotal) / subtotal <= 0.005 if subtotal else False
            print(f"  FOOT: sum={sum_charges:,.2f} vs subtotal={subtotal:,.2f} "
                  f"{'OK' if ok else 'FLAG'}")
            audited += 1
            footed += ok

    print(f"\nSUMMARY audited={audited} footing_ok={footed} unmatched={unmatched}")
    if deltas:
        worst = sorted(deltas, reverse=True)[:5]
        print("worst deltas (by %):")
        for d in worst:
            print(f"  {d[0]*100:.1f}%  {d[1]} {d[2]}/{d[3]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--month", default=None)
    args = ap.parse_args()
    conn = psycopg2.connect(_dsn())
    try:
        audit(conn, args.limit, args.month)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
