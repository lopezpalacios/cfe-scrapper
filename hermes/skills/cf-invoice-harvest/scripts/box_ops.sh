#!/bin/bash
# box_ops.sh — wrappers around the proven MiEspacio recipes on newman-ubuntu.
# ALL CFE egress runs on the box; this host only drives it over ssh.
# Usage:
#   box_ops.sh login              fresh login cycle (login2fa.js + code poll)
#   box_ops.sh feed <code>        write the 6-digit MFA code into /root/2fa_code.txt
#   box_ops.sh logged_in          grep "Hola, newmanenergy" in /root/login2fa.log
#   box_ops.sh enroll <rpu> <nombre> <total>    agr3.js (exact razón social, no decimals)
#   box_ops.sh harvest <offset>   node harvest_batch.js 6 <offset>
#   box_ops.sh cleanup            kill orphaned chromes
set -u

BOX="newman-ubuntu"
sshq() { ssh "$BOX" "$@"; }

case "${1:-}" in
  login)
    sshq "pkill -9 -f login2fa; rm -f /root/2fa_code.txt /root/login2fa.log; nohup node /root/login2fa.js > /root/login2fa.log 2>&1 & echo started"
    ;;
  feed)
    [ $# -ge 2 ] || { echo "usage: box_ops.sh feed <code>"; exit 2; }
    printf '%s' "$2" | sshq "cat > /root/2fa_code.txt"
    ;;
  logged_in)
    sshq "grep -c 'Hola, newmanenergy' /root/login2fa.log 2>/dev/null || true"
    ;;
  enroll)
    [ $# -ge 4 ] || { echo "usage: box_ops.sh enroll <rpu> <nombre> <total>"; exit 2; }
    # agr2.js <rpu> <nombre> <total> — the EXACT razón social + total (no decimals)
    sshq "cd /root && node agr2.js \"$2\" \"$3\" \"$4\" 2>&1 | tail -5"
    ;;
  harvest)
    off="${2:-0}"
    sshq "cd /root && node harvest_batch.js 6 $off 2>&1 | tail -8"
    ;;
  cleanup)
    sshq "pkill -f google-chrome 2>/dev/null; echo cleaned"
    ;;
  *)
    echo "unknown command: ${1:-}" >&2; exit 2 ;;
esac
