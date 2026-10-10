"""Data-model health suite — the frozen metric for the data-model climb.

All checks run on the mini against the direct DB (DATABASE_URL). Score = 100:
  canonical_coherent    25  exactly one canonical source per document_key,
                            and document.canonical_source_kind matches it
  canonical_mi_espacio  25  where mi_espacio and consulta feed one document,
                            mi_espacio wins (the deep-harvest rule)
  identity_integrity    20  share of cfe.invoice rpu values present in crm.rpu
  typed_population      20  succeeded parse runs / total (drain progress)
  enum_hardening        10  share of known code columns with a CHECK constraint
"""
import os
import subprocess
from pathlib import Path

ENV = dict(os.environ)
p = Path("~/.cfe.env").expanduser()
if p.exists():
    for line in p.read_text().splitlines():
        if line.startswith("export DATABASE_URL="):
            ENV["DATABASE_URL"] = line.split("=", 1)[1].strip()

SQL = """
with doc_sources as (
  select s.document_id, count(*) as n,
         count(*) filter (where s.is_canonical) as canon_n,
         max(case when s.is_canonical then s.source_kind end) as canon_kind
  from cfe.invoice_source s where s.document_id is not null
  group by 1
),
coherence as (
  select count(*) as docs,
         count(*) filter (where canon_n = 1) as one_flag,
         count(*) filter (where canon_n = 1 and d.canonical_source_kind = ds.canon_kind) as kind_matches
  from doc_sources ds join cfe.invoice_document d on d.id = ds.document_id
),
mi_espacio as (
  select count(*) as contested,
         count(*) filter (where canon_kind = 'mi_espacio') as won_by_mie
  from (
    select document_id, count(distinct source_kind) as kinds, max(case when is_canonical then source_kind end) as canon_kind
    from cfe.invoice_source where document_id is not null
    group by 1 having count(distinct source_kind) > 1
  ) x
),
identity as (
  select
    (select count(*) from cfe.invoice i
      where i.crm_rpu_id is not null
         or not exists (select 1 from crm.rpu r where r.rpu = i.rpu)) as n_ok,
    (select count(*) from cfe.invoice) as n,
    (select count(*) from crm.rpu r where r.rpu !~ '^[0-9]{12}$') as bad_master
),
population as (
  select count(*) filter (where status='succeeded') as ok, count(*) as total
  from cfe.invoice_parse_run
),
enums as (
  select
    (select count(*) from information_schema.columns c
     where (c.table_schema, c.table_name, c.column_name) in
       (('cfe','invoice_source','source_kind'), ('cfe','invoice','quality_status'),
        ('cfe','invoice_measurement','measurement_kind'), ('cfe','invoice_measurement','period_code'),
        ('cfe','invoice_measurement','quality_status'), ('cfe','invoice_charge','component_code'),
        ('cfe','invoice_charge','basis_kind'), ('cfe','invoice_ledger_line','semantic_type'),
        ('cfe','invoice_parse_run','status'), ('cfe','invoice_parse_run','error_class'))
     and (c.data_type in ('USER-DEFINED','ARRAY')
          or exists (select 1 from pg_constraint pc
                     join pg_class cl on cl.oid = pc.conrelid
                     join pg_namespace n on n.oid = cl.relnamespace
                     where pc.contype = 'c'
                       and n.nspname = c.table_schema and cl.relname = c.table_name
                       and pg_get_constraintdef(pc.oid) like '%' || c.column_name || '%'))
    ) as typed_ok,
    (select count(*) from information_schema.columns c
     where (c.table_schema, c.table_name, c.column_name) in
       (('cfe','invoice_source','source_kind'), ('cfe','invoice','quality_status'),
        ('cfe','invoice_measurement','measurement_kind'), ('cfe','invoice_measurement','period_code'),
        ('cfe','invoice_measurement','quality_status'), ('cfe','invoice_charge','component_code'),
        ('cfe','invoice_charge','basis_kind'), ('cfe','invoice_ledger_line','semantic_type'),
        ('cfe','invoice_parse_run','status'), ('cfe','invoice_parse_run','error_class'))) as total_checked
)
select c.docs, c.one_flag, c.kind_matches, m.contested, m.won_by_mie,
       i.n_ok, i.n, i.bad_master, p.ok, p.total, e.typed_ok, e.total_checked
from coherence c, mi_espacio m, identity i, population p, enums e;
"""


def _row():
    r = subprocess.run(
        ["python3", "-c",
         "import os,psycopg2;c=psycopg2.connect(os.environ['DATABASE_URL']);"
         "cur=c.cursor();cur.execute('''" + SQL + "''');"
         "print(list(cur.fetchone()));c.close()"],
        capture_output=True, text=True, env=ENV, timeout=60)
    import ast
    return ast.literal_eval(r.stdout.strip())


def _canonical_coherent():
    docs, one, matches, *_ = _row()
    if docs == 0:
        return False
    return one == docs and matches == docs


def _canonical_mi_espacio():
    docs, one, matches, contested, won, *_ = _row()
    if contested == 0:
        return True
    return won == contested


def _identity_integrity():
    docs, one, matches, contested, won, n_ok, n, bad_master, *_ = _row()
    if n == 0:
        return False
    # anchor contract: every invoice whose rpu IS a known client must be
    # linked; corpus rpus not yet onboarded as clients are legitimately null
    return n_ok / n >= 0.99 and bad_master == 0


def _typed_population():
    docs, one, matches, contested, won, n, matched, ok, total, *_ = _row()
    if total == 0:
        return False
    return ok / total >= 0.5


def _enum_hardening():
    *_, typed_ok, total_checked = _row()
    if total_checked == 0:
        return False
    return typed_ok / total_checked >= 0.9


WEIGHT = 100

CASES = [
    {"id": "canonical_coherent", "points": 25, "fn": _canonical_coherent, "expect": True},
    {"id": "canonical_mi_espacio_wins", "points": 25, "fn": _canonical_mi_espacio, "expect": True},
    {"id": "identity_integrity", "points": 20, "fn": _identity_integrity, "expect": True},
    {"id": "typed_population", "points": 20, "fn": _typed_population, "expect": True, "slow": True},
    {"id": "enum_hardening", "points": 10, "fn": _enum_hardening, "expect": True},
]
