# Service (the scraper, dockerized + nix-reproducible)

The deployable unit: a single image that runs the whole scraping service on
the machine it's deployed to (the Mac mini). Nix builds the image
(`nix build .#scraper-image`) — bit-reproducible, layer-hashed — so the exact
service that passed CI is what runs.

## Egress model (2026-10-10, operator decision)

The service runs ON the mini. CFE/CENACE egress requires the mini's NordVPN
connected to a MEXICO server. `worker.py` enforces it at runtime: before any
CFE fetch it geolocates the current egress IP (ipinfo) and refuses unless the
country is MX (`geo_blocked` classified error). newman-ubuntu is retired as
the egress path (its SSH is flaky); its MiEspacio recipes are documented in
`docs/harvest-recipes.md` and reimplemented here.

## Worker loop (`worker.py`)

1. `vpn_guard()` — egress IP must be MX. Else sleep + retry, never fetch.
2. Claim a job: `hermes.jobs` (state queued → running, lease).
3. Pipeline per the cf-invoice-harvest skill:
   - if the job carries media: barcode-only RPU extraction (decode_rpu.sh)
   - phone lookup via adb (Contigo: nombre/total) — phone has its own
     NordVPN-MX
   - MiEspacio cycle: `harvest/login2fa.py` (WAF-clear + login + 2FA poll
     against the mailbox via IMAP) → enroll (`harvest/enroll.py`, exact
     razón social + total) → `harvest/harvest_batch.py` (6-download batches,
     re-login between batches, 503 Retry-After respected)
   - CFDI parse (`cfe_invoice_parser.py`) → Supabase (service role)
4. Job → done / failed with the classified error
   (barcode_unreadable|captcha|otp|session_expired|geo_blocked|…).
5. Optionally: tariff refresh (`fetch_all.py`) + `bill-price-audit`.

Config via env only — never baked into the image:
SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, TWOCAPTCHA_KEY, CFE_USER, CFE_PASS,
MAIL_INTAKE_USER, MAIL_INTAKE_APP_PASSWORD, ADB_SERIAL.

## Image (flake.nix, `dockerTools.buildImage`)

- python312 with psycopg2 + pdfplumber + requests
- nodejs_20 + puppeteer-core + chromium (google-chrome shimmed for the
  legacy script paths)
- zbar + poppler (barcode decode)
- the service tree at `/app`, entrypoint `/app/worker.py`
- `nix build .#scraper-image && docker load < result` → run with
  `docker run --env-file .env --device /dev/… cfe-scrapper:latest`

## Reproducibility contract

The image digest is a pure function of the repo's flake inputs. CI builds
the same image; `docker push` publishes it to
`ghcr.io/lopezpalacios/cfe-scrapper`.
