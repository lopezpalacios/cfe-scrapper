#!/usr/bin/env python3
"""Claim and materialize ``cfe.invoice_parse_run`` jobs.

The worker is deliberately separate from the legacy ``crm.bill`` path.  It
reads XML only from ``raw_cfe.*`` and writes the normalized result to ``cfe``.
It is safe to retry a job: child rows are replaced in one transaction and the
parser output hash is stored on the parse run.

Usage::

    DATABASE_URL='postgresql://...' python cfe_backfill.py --limit 25

The production scheduler can run the same command repeatedly.  A lease makes
an abandoned worker recoverable without manual cleanup.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, RealDictCursor

try:
    from .cfe_invoice_parser import PARSER_VERSION, parse_xml
except ImportError:  # direct script execution
    from cfe_invoice_parser import PARSER_VERSION, parse_xml


LEASE_MINUTES = 15
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
SOURCE_TABLES = {"consulta", "mi_espacio", "contigo"}


def _dsn() -> str | None:
    value = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    return value


def _connect():
    dsn = _dsn()
    if dsn:
        return psycopg2.connect(dsn)
    required = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "configure DATABASE_URL/SUPABASE_DB_URL or all of " + ", ".join(required)
        )
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.environ["PGPORT"],
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        sslmode=os.environ.get("PGSSLMODE", "require"),
        connect_timeout=int(os.environ.get("PGCONNECT_TIMEOUT", "10")),
    )


def claim_jobs(conn, limit: int) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            with candidates as (
              select p.id, p.source_id, s.source_kind, s.raw_id, s.sha256
              from cfe.invoice_parse_run p
              join cfe.invoice_source s on s.id = p.source_id
              where p.parser_version = %s
                and (
                  (p.status = 'pending' and p.next_attempt_at <= now())
                  or (p.status = 'running' and p.lease_expires_at < now())
                )
              order by p.id
              for update of p skip locked
              limit %s
            )
            update cfe.invoice_parse_run p
            set status = 'running',
                attempts = p.attempts + 1,
                claimed_at = now(),
                claimed_by = %s,
                lease_expires_at = now() + make_interval(mins => %s),
                started_at = coalesce(p.started_at, now()),
                updated_at = now()
            from candidates c
            where p.id = c.id
            returning p.id, p.source_id, c.source_kind, c.raw_id, c.sha256
            """,
            (PARSER_VERSION, limit, WORKER_ID, LEASE_MINUTES),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_xml(conn, job: dict[str, Any]) -> str:
    source_kind = job["source_kind"]
    if source_kind not in SOURCE_TABLES:
        raise ValueError(f"unsupported source kind: {source_kind}")
    query = sql.SQL("select xml_content from raw_cfe.{} where id = %s").format(sql.Identifier(source_kind))
    with conn.cursor() as cur:
        cur.execute(query, (job["raw_id"],))
        row = cur.fetchone()
    if not row or not row[0]:
        raise ValueError("raw XML is empty")
    return row[0]


def _upsert_document(cur, payload: dict[str, Any]) -> int:
    document = payload["document"]
    if not document.get("document_key"):
        raise ValueError("document key missing")
    cur.execute(
        """
        insert into cfe.invoice_document
          (document_key, cfdi_uuid, tipo_comprobante, cfdi_version, serie, folio,
           fecha_emision, moneda, subtotal, total, source_sha256,
           canonical_source_kind, canonical_source_id, canonical_score, updated_at)
        values (%(document_key)s, %(cfdi_uuid)s, %(tipo_comprobante)s, %(cfdi_version)s,
                %(serie)s, %(folio)s, %(fecha_emision)s, %(moneda)s, %(subtotal)s,
                %(total)s, %(source_sha256)s, %(source_kind)s, %(raw_id)s,
                %(canonical_score)s, now())
        on conflict (document_key) do update set
          cfdi_uuid = coalesce(excluded.cfdi_uuid, cfe.invoice_document.cfdi_uuid),
          tipo_comprobante = excluded.tipo_comprobante,
          cfdi_version = coalesce(excluded.cfdi_version, cfe.invoice_document.cfdi_version),
          serie = coalesce(excluded.serie, cfe.invoice_document.serie),
          folio = coalesce(excluded.folio, cfe.invoice_document.folio),
          fecha_emision = coalesce(excluded.fecha_emision, cfe.invoice_document.fecha_emision),
          moneda = coalesce(excluded.moneda, cfe.invoice_document.moneda),
          subtotal = coalesce(excluded.subtotal, cfe.invoice_document.subtotal),
          total = coalesce(excluded.total, cfe.invoice_document.total),
          updated_at = now()
        returning id
        """,
        {
            **document,
            **payload["source"],
            "canonical_score": 100 if payload["source"].get("source_kind") == "mi_espacio" else 90,
        },
    )
    return int(cur.fetchone()[0])


def _replace_children(cur, document_id: int, payload: dict[str, Any]) -> None:
    for table in ("invoice_measurement", "invoice_charge", "invoice_ledger_line", "invoice_history_month", "invoice_issue"):
        cur.execute(sql.SQL("delete from cfe.{} where document_id = %s").format(sql.Identifier(table)), (document_id,))

    invoice = payload["invoice"]
    document = payload["document"]
    if document.get("tipo_comprobante") in ("I", "E"):
        invoice_params = {
            **invoice,
            "document_id": document_id,
            "issues_json": Json(payload["issues"]),
        }
        cur.execute(
            """
            insert into cfe.invoice
              (document_id, rpu, period_label, period_start, period_end, billing_days,
               tarifa_reg, tarifa_legacy, sicom, division_code, cp,
               contracted_demand_kw, quality_status, quality_score, updated_at)
            values (%(document_id)s, %(rpu)s, %(period_label)s, %(period_start)s, %(period_end)s,
                    %(billing_days)s, %(tarifa_reg)s, %(tarifa_legacy)s, %(sicom)s,
                    %(division_code)s, %(cp)s, %(contracted_demand_kw)s,
                    case when jsonb_array_length(%(issues_json)s::jsonb) > 0
                         then 'needs_review' else 'accepted' end,
                    case when jsonb_array_length(%(issues_json)s::jsonb) = 0 then 1.0 else greatest(0.0, 1.0 - jsonb_array_length(%(issues_json)s::jsonb)::numeric / 20.0) end,
                    now())
            on conflict (document_id) do update set
              rpu = excluded.rpu, period_label = excluded.period_label,
              period_start = excluded.period_start, period_end = excluded.period_end,
              billing_days = excluded.billing_days, tarifa_reg = excluded.tarifa_reg,
              tarifa_legacy = excluded.tarifa_legacy, sicom = excluded.sicom,
              division_code = excluded.division_code, cp = excluded.cp,
              contracted_demand_kw = excluded.contracted_demand_kw,
              quality_status = excluded.quality_status,
              quality_score = excluded.quality_score, updated_at = now()
            """,
            invoice_params,
        )
    elif document.get("tipo_comprobante") == "P":
        cur.execute(
            """
            insert into cfe.invoice_payment (document_id, payment_date, currency, amount)
            values (%s, nullif(%s, '')::date, %s, %s)
            on conflict (document_id) do update set
              payment_date = excluded.payment_date, currency = excluded.currency,
              amount = excluded.amount
            """,
            (document_id, document.get("fecha_emision"), document.get("moneda"), document.get("total")),
        )

    for row in payload["measurements"]:
        cur.execute(
            """
            insert into cfe.invoice_measurement
              (document_id, measurement_kind, period_code, value, unit,
               source_path, source_label, quality_status)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (document_id, row.get("measurement_kind"), row.get("period_code"), row.get("value"),
             row.get("unit"), row.get("source_path"), row.get("source_label"), row.get("quality_status")),
        )
    for row in payload["charges"]:
        cur.execute(
            """
            insert into cfe.invoice_charge
              (document_id, source_slot, motivo_code, component_code, period_code,
               basis_kind, amount_total, concept_raw, source_path)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (document_id, row.get("source_slot"), row.get("motivo_code"), row.get("component_code"),
             row.get("period_code"), row.get("basis_kind"), row.get("amount_total"),
             row.get("concept_raw"), row.get("source_path")),
        )
    for row in payload["ledger_lines"]:
        cur.execute(
            """
            insert into cfe.invoice_ledger_line
              (document_id, line_number, concept_raw, amount, semantic_type, source_path)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (document_id, row.get("line_number"), row.get("concept_raw"), row.get("amount"),
             row.get("semantic_type"), row.get("source_path")),
        )
    for row in payload["history"]:
        cur.execute(
            """
            insert into cfe.invoice_history_month
              (document_id, source_slot, kwh, demand_kw, source_path)
            values (%s, %s, %s, %s, %s)
            """,
            (document_id, row.get("source_slot"), row.get("kwh"), row.get("demand_kw"), row.get("source_path")),
        )
    for issue in payload["issues"]:
        cur.execute(
            """
            insert into cfe.invoice_issue
              (document_id, severity, rule_code, field_path, raw_value, evidence)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (document_id, issue.get("severity"), issue.get("rule_code"), issue.get("field_path"),
             issue.get("raw_value"), Json(issue)),
        )


def materialize_job(conn, job: dict[str, Any]) -> str:
    try:
        xml = fetch_xml(conn, job)
        payload = parse_xml(
            xml,
            source_kind=job["source_kind"],
            raw_id=job["raw_id"],
            sha256=job["sha256"],
        )
        with conn:
            with conn.cursor() as cur:
                document_id = _upsert_document(cur, payload)
                cur.execute(
                    "update cfe.invoice_source set document_id = %s, is_canonical = false where id = %s",
                    (document_id, job["source_id"]),
                )
                _replace_children(cur, document_id, payload)
                status = "failed" if any(i.get("severity") == "fatal" for i in payload["issues"]) else (
                    "needs_review" if payload["issues"] else "succeeded"
                )
                cur.execute(
                    """
                    update cfe.invoice_parse_run
                    set document_id = %s, status = %s, output_hash = %s,
                        parsed_payload = %s, finished_at = now(), lease_expires_at = null,
                        error_class = null, error_detail = null, updated_at = now()
                    where id = %s
                    """,
                    (document_id, status, payload["output_hash"], Json(payload), job["id"]),
                )
        return status
    except Exception as exc:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update cfe.invoice_parse_run
                    set status = case when attempts >= 3 then 'failed' else 'pending' end,
                        next_attempt_at = now() + make_interval(mins => least(attempts * 5, 60)),
                        lease_expires_at = null, error_class = %s, error_detail = %s, updated_at = now()
                    where id = %s
                    """,
                    (type(exc).__name__, str(exc)[:2000], job["id"]),
                )
        return "failed"


def run(limit: int) -> dict[str, int]:
    conn = _connect()
    try:
        jobs = claim_jobs(conn, limit)
        counts = {"claimed": len(jobs), "succeeded": 0, "needs_review": 0, "failed": 0}
        for job in jobs:
            status = materialize_job(conn, job)
            counts[status] = counts.get(status, 0) + 1
        return counts
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit must be between 1 and 1000")
    print(run(args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
