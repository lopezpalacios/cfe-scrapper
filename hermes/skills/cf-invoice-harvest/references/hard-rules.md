# Hard rules + proven recipes (condensed from kameloso HANDOVER.md §2/§4)

## Hard rules

1. ALL CFE-facing egress from newman-ubuntu only (MX residential, Mazatlán,
   AS8151). The mini's own egress is Swiss. The phone must be on NordVPN MEXICO
   or it cannot reach appcfecontigo.cfe.mx.
2. MiEspacio: ~6 downloads per session, then re-login (login2fa.js + code via
   IMAP into /root/2fa_code.txt). Never hammer beyond; respect 503 Retry-After.
3. Enrollment needs the EXACT razón social + total (no decimals); wrong values
   → "El nombre del servicio no coincide..." with no hints.
4. adb: after typing the RPU, keyboard dismissal MOVES the button — always
   re-dump the UI and tap the fresh bounds. Button disabled (en=false) until
   12 digits are in.
5. Never put CFE passwords/keys in plaintext logs or commits.

## Recipe A — Contigo app flow (phone)

1. NordVPN → Mexico server (mandatory).
2. adb -s RFGL41FS57N shell monkey launch mx.com.cfe.cfecontigo.
3. If logged out: Ingresar → email + password → home. New RPU account:
   Regístrate → form (dummy phone) → code via mailbox → Verificar cuenta.
4. Añadir servicio → Capturar manualmente → type RPU → re-dump UI → tap
   button at CURRENT bounds → "Alta de servicio" shows nombre/cuenta/tarifa →
   Continuar → alias → Finalizar.
5. Recibos → Ver recibo → top-right menu → "Enviar por correo XML" → CFDI
   (attachment part 1 = application/xml) lands in jesus@newman.re.

## Recipe B — MiEspacio harvest (newman-ubuntu)

1. Fresh session: pkill -9 -f login2fa; rm -f /root/2fa_code.txt
   /root/login2fa.log; nohup node login2fa.js > /root/login2fa.log 2>&1 &
2. Poll the mailbox for the new 6-digit MFA (skip CSS-color candidates), feed
   it: printf '<code>' | ssh newman-ubuntu 'cat > /root/2fa_code.txt'
3. Verify "Hola, newmanenergy" in the log → cookies saved.
4. Enrollment (once per RPU): node agr3.js (exact razón social + total).
5. Harvest: node harvest_batch.js 6 <offset> — 6 downloads per session,
   offset = 6×batches (grid rows ctl00$MainContent$GVHistorial$ctlNN$...);
   downloads land in /root/cfe-gd-scrape/dl/<rpu>/current/.
6. Re-login between batches until the grid is exhausted; older months live in
   OtrasFacturas.aspx (dlservice.js).

## Barcode payload

CFE CODE128 (30 digits): [0:2] '01' · [2:14] RPU (12) · [14:16] YY ·
[16:18] MM · [18:20] '12' · [20:30] adeudo×10. Validated 12/12 golden bills.

## Asset map

- Mac: gws (mailbox, keychain) — superseded on the mini by IMAP app-password.
- Mini: ~/cfe-gd-scrape/dl/<rpu>/ (69-RPU corpus, 410 XMLs);
  ~/wt-mi-espacio-scraping-skills/harvest/ (barcode_identify, golden_engine,
  mail_intake, recibo_parser — proven code this skill reuses);
  ~/cfe-brain/vault/tools (golden engine cfe_savings.extract).
- newman-ubuntu: /root/login2fa.js, /root/agr3.js, /root/harvest_batch.js,
  /root/dlservice.js, /root/cfe_cookies.json, /root/.cfe_solver.env.
- Phone: Galaxy A16 serial RFGL41FS57N, adb over USB, NordVPN MX required.
