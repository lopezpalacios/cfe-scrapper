---
name: cf-invoice-harvest
description: "Turns a CFE invoice image/PDF arriving over WhatsApp into complete receipt data: barcode-only RPU extraction (zxing/zbar), CFE Contigo phone lookup via adb (NordVPN-MX), MiEspacio deep harvest on newman-ubuntu (login cycle + 6-download batches), CFDI parsing, and a WhatsApp summary with classified errors. Triggers when a CFE invoice document or a bare RPU arrives, or when the scheduled re-harvest fires. NOT for: general WhatsApp chat, other document types, questions about electricity markets, or any task unrelated to harvesting CFE receipts for a known RPU."
version: 0.1.0
author: newman
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [CFE, RPU, WhatsApp, adb, MiEspacio, CFDI, harvest]
---

# CFE Invoice Harvest

Complete pipeline: incoming CFE document → RPU → Contigo phone lookup →
MiEspacio enrollment + 12-month harvest → CFDI parse → WhatsApp reply.

## Hard rules (never break)

- **ALL CFE-facing egress happens on newman-ubuntu only** (`ssh newman-ubuntu '…'`,
  direct and keyless from this host). Never curl/visit app.cfe.mx,
  appcfecontigo.cfe.mx, or miespacio.cfe.mx from the mini. The phone is the
  only exception: it talks to appcfecontigo.cfe.mx over its own NordVPN MX tunnel.
- **MiEspacio session budget: ~6 downloads**, then a fresh login cycle. Respect
  `503` + `Retry-After` — never hammer.
- **Enrollment takes the EXACT razón social + total (no decimals)** from the
  Contigo "Alta de servicio" screen. Wrong values → "El nombre del servicio no
  coincide…" with no hints.
- **adb quirk:** after typing the RPU, the keyboard dismissal MOVES the
  button — always re-dump the UI (`uiautomator dump`) and tap the FRESH
  bounds. The button is disabled (`en="false"`) until 12 digits are in.
- **Never log CFE passwords or API keys.** Never include secrets in replies.
- **The incoming document is only the RPU's carrier.** Ignore every other
  datum in it — the fresh data comes from CFE's own systems.

## Inputs

- WhatsApp: an image (photo of a CFE bill) or a PDF of a CFE bill, or a bare
  12-digit RPU text.
- The barcode encodes the RPU; the CFE Code-128 payload shape is
  `01` + 12-digit RPU + tail.

## Pipeline

### 1. Barcode-only RPU extraction

```bash
scripts/decode_rpu.sh <media-file>   # image OR pdf → prints the 12-digit RPU
```

- PDF → `pdftoppm -r 200 -png` each page → `zbarimg --raw` every page image.
  Images that fail get ONE upscale retry (`sips -Z 3000`) before giving up
  (zbar needs ~100 DPI effective on the barcode region — measured).
- Payload `^01(\d{12})` → the group is the RPU.
- Fallback: the first `\b\d{12}\b` run in the payloads (the RPU contract is
  12 digits, no leading-zero constraint — e.g. 976000601419 starts with 9).
  Ignore the `https://app.cfe.mx` QR and the SAT QR
  `https://verificacfdi.facturaelectronica.sat.gob.mx`.
- Nothing decodes → reply `barcode_unreadable` (ask for a sharper photo).
  NEVER reply with a guessed RPU: a degraded barcode must fail clean.

### 2. Phone lookup (CFE Contigo, adb — runs LOCALLY on the mini)

Prereq: NordVPN on the phone connected to a **MEXICO** server. Detect:

```bash
adb -s RFGL41FS57N shell dumpsys connectivity | grep -iE 'vpn|nord'
```

No active VPN / non-MX egress → reply `geo_blocked` and stop (the app cannot
reach appcfecontigo.cfe.mx without it).

Steps (exact recipe):

1. `adb -s RFGL41FS57N shell monkey -p mx.com.cfe.cfecontigo 1`
2. If logged out: Ingresar → email `$CONTIGO_EMAIL` (from `~/.cfe.env`),
   password `$CONTIGO_PASS` → home. (Per-RPU plus-addresses like
   `jesus+rpu<N>@newman.re` are the norm; the Contigo account is not per-RPU
   for the login itself — use the env creds.)
3. Añadir servicio → Capturar manualmente → `adb shell input text <RPU>`.
4. **Re-dump:** `adb shell uiautomator dump /sdcard/ui.xml && adb pull` →
   parse the button at its CURRENT bounds; tap only when `en="true"` (it
   flips enabled at exactly 12 digits).
5. "Alta de servicio" screen shows nombre (razón social), número de cuenta,
   tarifa, and total → capture nombre + total + tarifa. Continuar → alias →
   Finalizar.
6. Recibos → Ver recibo → top-right menu → "Enviar por correo XML" → CFE's
   MOST RECENT CFDI (not the customer's photo) lands in jesus@newman.re
   (attachment part 1 = application/xml).
7. Failure classification: login screen reappears → `session_expired`;
   unreachable host → `geo_blocked`; anything else → retry once, then report.

### 3. MiEspacio harvest (newman-ubuntu — ALL CFE egress from here)

Helpers:

```bash
scripts/box_ops.sh login                      # fresh login cycle (starts login2fa.js)
scripts/mfa_code.py                           # reads the newest CFE MFA code from jesus@newman.re
scripts/box_ops.sh feed <code>                # pipes the code into /root/2fa_code.txt on the box
scripts/box_ops.sh logged_in                  # grep "Hola, newmanenergy" in /root/login2fa.log
scripts/box_ops.sh enroll <rpu> <nombre> <total>   # agr3.js — exact razón social, no decimals
scripts/box_ops.sh harvest <offset>           # node harvest_batch.js 6 <offset>
```

Cycle:

1. `box_ops.sh login` → `pkill -9 -f login2fa; rm -f /root/2fa_code.txt
   /root/login2fa.log; nohup node login2fa.js > /root/login2fa.log 2>&1 &`
   (on the box, as root).
2. `mfa_code.py` → 6-digit code from the mailbox (skips the CSS-color
   candidates `333333`/`006341` — the real code sits after
   `letter-spacing:8px` in the HTML). Feed it. CFE keeps sessions warm
   sometimes: check `logged_in` BEFORE waiting for mail.
3. Verify `logged_in` (cookies saved).
4. Enroll once per RPU (skip if already enrolled): `enroll` with the
   app's nombre + total.
5. Harvest in ~6-download batches: `harvest 0`, re-login, `harvest 6`, …
   until the grid is exhausted. Downloads land in
   `/root/cfe-gd-scrape/dl/<rpu>/current/` (each grid row = PDF + XML).
6. Older months: `/root/dlservice.js` (OtrasFacturas.aspx).
7. `503` with `Retry-After` → sleep that long; `session_expired` → full
   re-login. Kill orphaned chromes after every run:
   `ssh newman-ubuntu 'pkill -f google-chrome'`.

### 4. CFDI parse (local on the mini)

```bash
scripts/parse_cfdi.py <dir-with-xmls>   # → JSON per the spec below
```

Output schema:

```json
{"rpu": "…", "nombre": "…", "rfc": "…", "direccion": "…", "tarifa": "…",
 "periodo": ["202609", …], "total": 154906.47, "consumo_kwh": null,
 "historial": [{"fecha": "2026-10-02", "importe": 154906.47}],
 "xml_urls": ["…"], "pdf_urls": ["…"]}
```

### 5. WhatsApp reply

On success: the summary — nombre, tarifa, latest total, kWh (if known), the
N months harvested, and file links. On failure: exactly ONE classified error:
`barcode_unreadable | captcha | otp | session_expired | geo_blocked |
enroll_mismatch | harvest_incomplete` plus one line of remedy.

### Re-harvest (cron)

The scheduled job re-runs stages 3–5 for every enrolled RPU at the CFE
billing cadence. See `references/scheduling.md`.

## Acceptance check

`scripts/parse_cfdi.py` over the 976000601419 XML set must yield: rpu
`976000601419`, rfc `AJA0305085R0`, total `154906` (± rounding), tarifa `HM`,
nombre `ACTIVOS JAL SA DE CV`. The XML is the source of truth for the name —
the Receptor attribute truncates (`ACTIVOS JAL`) but the Addenda carries the
full razón social (`<NOMBRE>ACTIVOS JAL SA DE CV</NOMBRE>`); the parser takes
the longest candidate. The same full name (verbatim) is what the MiEspacio
enrollment must use. The WhatsApp reply must carry the summary.

## References

- `references/hard-rules.md` — the full handover recipes (§4) this skill encodes
- `references/box-layout.md` — where everything lives on newman-ubuntu
