#!/bin/bash
# run_backfill_loop.sh — drain cfe.invoice_parse_run in batches and measure.
# The XML-derived typed model (cfe.invoice* family) is the parser's output
# contract; this loop materializes the 27k queued raw XMLs into it.
#
# Requires DATABASE_URL (Supabase direct connection) in the environment.
# Idempotent: each job's child rows are replaced in one transaction, and the
# output_hash is stored, so re-runs are safe.
set -u
. ~/.cfe.env
export DATABASE_URL
DIR="/Users/newman/newman-agents/automation/ocr"
BATCH="${BATCH_SIZE:-25}"
MAX_BATCHES="${MAX_BATCHES:-40}"

metric() {
  python3 - "$@" <<'PYEOF'
import os, sys
import psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
with conn.cursor() as cur:
    cur.execute("""
      select count(*) filter (where status='succeeded'),
             count(*) filter (where status='pending'),
             count(*) filter (where status='failed')
      from cfe.invoice_parse_run""")
    s, p, f = cur.fetchone()
    total = s + p + f
    print(f"typed_model_population {s}/{total} ({s/total*100:.3f}%)  pending={p} failed={f}")
conn.close()
PYEOF
}

echo "== baseline"
metric
for i in $(seq 1 "$MAX_BATCHES"); do
  ( cd "$DIR" && python3 cfe_backfill.py --limit "$BATCH" )
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "worker rc=$rc at batch $i — stopping"
    exit 1
  fi
  metric
  REMAINING=$(python3 - "$@" <<'PYEOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
with conn.cursor() as cur:
    cur.execute("select count(*) from cfe.invoice_parse_run where status='pending'")
    print(cur.fetchone()[0])
conn.close()
PYEOF
)
  [ "$REMAINING" -le 0 ] && { echo "QUEUE_DRAINED"; exit 0; }
done
echo "MAX_BATCHES reached"
