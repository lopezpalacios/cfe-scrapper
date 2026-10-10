#!/bin/bash
# mini-side launcher: build the entries JSON from the manifest, fire the box
# refresh detached, poll its log.
set -u
BACKEND="/Users/newman/newman-agents/newman-backend/scrapers"
ENTRIES=$(python3 - "$BACKEND" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
from manifest import ensure_seed
entries = [{"url": e.url, "year": e.year, "month": e.month, "authority": e.authority}
           for e in ensure_seed()]
print(json.dumps(entries))
PYEOF
)
ssh -o ConnectTimeout=10 newman-ubuntu "rm -f /root/tariff/refresh.log; nohup /root/tariff/refresh.sh '$ENTRIES' >> /root/tariff/refresh.log 2>&1 & echo launched"
for i in $(seq 1 80); do
  sleep 30
  DONE=$(ssh -o ConnectTimeout=10 newman-ubuntu "grep -c REFRESH_DONE /root/tariff/refresh.log 2>/dev/null" || echo 0)
  if [ "$DONE" -ge 1 ]; then
    echo "COMPLETE after ~$((i*30))s"
    ssh newman-ubuntu "grep -E 'rows|RPC|FAILED|REFRESH_DONE' /root/tariff/refresh.log | tail -25"
    exit 0
  fi
  echo "waiting ($((i*30))s)"
done
echo "TIMED_OUT after 40min"
ssh newman-ubuntu "tail -10 /root/tariff/refresh.log"
exit 1
