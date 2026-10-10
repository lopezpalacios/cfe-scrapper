"""Data-model suite: the XML-derived typed model must be populated and clean.

Checks via the direct DB connection (DATABASE_URL from ~/.cfe.env):
- the parse pipeline is functioning (succeeded runs > 100)
- the failure rate is negligible (< 1% of runs)
- the typed tables are receiving rows (invoice_document count tracks succeeded)
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

_Q = """
select
  (select count(*) filter (where status='succeeded') from cfe.invoice_parse_run) as ok,
  (select count(*) filter (where status='failed') from cfe.invoice_parse_run) as failed,
  (select count(*) from cfe.invoice_parse_run) as total,
  (select count(*) from cfe.invoice_document) as docs;
"""


def _stats():
    r = subprocess.run(
        ["python3", "-c",
         "import os,psycopg2;c=psycopg2.connect(os.environ['DATABASE_URL']);"
         "cur=c.cursor();cur.execute('''" + _Q + "''');"
         "print(cur.fetchone());c.close()"],
        capture_output=True, text=True, env=ENV, timeout=60)
    out = r.stdout.strip().strip("()")
    parts = [x.strip() for x in out.split(",")]
    return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])


def _pipeline_working():
    ok, failed, total, docs = _stats()
    # document_key dedup: dual-source harvests (consulta + mi_espacio)
    # collapse to one document per CFDI, so docs < ok by design (~0.85x)
    return (ok > 100, docs >= ok * 0.7)


def _failures_negligible():
    ok, failed, total, docs = _stats()
    if total == 0:
        return False
    return failed <= max(3, total * 0.01)


WEIGHT = 10

CASES = [
    {"id": "typed_model_populated", "points": 5,
     "fn": _pipeline_working, "expect": (True, True)},
    {"id": "parse_failures_negligible", "points": 5,
     "fn": _failures_negligible, "expect": True, "slow": True},
]
