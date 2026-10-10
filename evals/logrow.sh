#!/bin/sh
# usage: logrow.sh <file> <phase> <decision> <why> <evidence> <result>
f="$1"; shift
if [ ! -f "$f" ]; then
  printf 'ts\tphase\tdecision\twhy\tevidence\tresult\n' > "$f"
fi
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "$1" "$2" "$3" "$4" "$5" >> "$f"
