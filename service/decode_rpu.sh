#!/bin/bash
# decode_rpu.sh <file> — barcode-only RPU extraction from a CFE bill image/PDF.
# The document is ONLY the RPU's carrier: we read its barcode, nothing else.
# Output: the 12-digit RPU on stdout. Exit 1 = barcode_unreadable.
set -u
PATH="$PATH:/usr/local/bin:/opt/homebrew/bin"

f="$1"
[ -f "$f" ] || { echo "missing file: $f" >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
imgs=()

case "$f" in
  *.pdf|*.PDF)
    pdftoppm -r 200 -png "$f" "$work/pg" 2>/dev/null || { echo "pdf rasterize failed" >&2; exit 1; }
    for p in "$work"/pg*.png; do [ -f "$p" ] && imgs+=("$p"); done
    ;;
  *)
    imgs+=("$f")
    ;;
esac

payloads=""
for img in "${imgs[@]}"; do
  zbarimg --raw -q "$img" 2>/dev/null >> "$work/payloads.txt" || true
  # Low-res retry: a WhatsApp-scaled photo can sit below zbar's ~100-DPI
  # floor. Upscale once (sips is on macOS) and rescan before giving up.
  if ! grep -qE '^01[0-9]{12}' "$work/payloads.txt"; then
    if command -v sips >/dev/null 2>&1; then
      sips -Z 3000 "$img" --out "$work/big.png" >/dev/null 2>&1 \
        && zbarimg --raw -q "$work/big.png" 2>/dev/null >> "$work/payloads.txt" || true
    fi
  fi
done

[ -s "$work/payloads.txt" ] || { echo "no barcode found" >&2; exit 1; }

# Preferred: Code-128 payload shaped "01" + 12-digit RPU + tail
while IFS= read -r line; do
  line="$(printf '%s' "$line" | tr -d '\r')"
  case "$line" in
    01[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*)
      rpu="${line:2:12}"
      printf '%s\n' "$rpu"
      exit 0
      ;;
  esac
done < "$work/payloads.txt"

# Fallback: the first 12-digit run in the LINEAR payloads. QR URLs (the SAT
# verification link, app.cfe.mx) must never contribute candidates — a wrong
# RPU is worse than no RPU.
python3 - "$work/payloads.txt" <<'PYEOF'
import re, sys
for line in open(sys.argv[1]):
    line = line.strip()
    if "http" in line.lower():
        continue
    for cand in re.findall(r"\b\d{12}\b", line):
        print(cand)
        sys.exit(0)
sys.exit(1)
PYEOF
exit $?
