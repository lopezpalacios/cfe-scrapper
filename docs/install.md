# Install & self-host (for Hermes, on the mini or a fresh machine)

Everything the scraper needs lives in this repo. A fresh Hermes instance can
clone it and run the whole system itself. The env values are NEVER in the
repo — only names, in `.env.example`.

## 1. Get the repo + env

```bash
git clone https://github.com/lopezpalacios/cfe-scrapper.git ~/cfe-scrapper
cp ~/cfe-scrapper/.env.example ~/.cfe.env   # fill in the real values
chmod 600 ~/.cfe.env
```

Env keys (see `.env.example`): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
DATABASE_URL, BROWSER_USE_API_KEY, CFE_USER, CFE_PASS, TWOCAPTCHA_KEY,
MAIL_INTAKE_USER, MAIL_INTAKE_APP_PASSWORD, ADB_SERIAL, INTAKE_KEY.

## 2. Build the image (nix, deterministic)

```bash
nix build ~/cfe-scrapper#scraper-image      # digest = f(flake.lock + source)
docker load < result
```

CI builds the same image and pushes it to
`ghcr.io/lopezpalacios/cfe-scrapper` on main.

## 3. Database (Supabase oioyawhgvazebtarigpc)

Apply `supabase/migrations/` in order (the numbering is the order; the
rpu-intake edge function source is in `supabase/functions/rpu-intake/`).
The worker's job RPCs (`rpc_claim_scrape_job`, `rpc_finish_scrape_job`) and
the loader RPCs are created by the migrations.

## 4. Workspace identity (hermes@newman.re)

```bash
export GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file
gws auth login --scopes \
  https://www.googleapis.com/auth/admin.directory.group,\
  https://www.googleapis.com/auth/admin.directory.group.member,\
  https://www.googleapis.com/auth/gmail.readonly
# sign in as hermes@newman.re (Workspace admin) — device/tunnel flow
```

Per-RPU groups: `python3 service/workspace/gws_groups.py create <rpu>` —
the group `rpu-<n>@newman.re` is the CFE portal account's email; the 2FA
code lands in the group and is delivered to the hermes@newman.re inbox
(read by `service/mfa_code.py` via gws).

## 5. Run the service

```bash
docker run -d --name cfe-scrapper --restart unless-stopped \
  --env-file ~/.cfe.env \
  ghcr.io/lopezpalacios/cfe-scrapper:latest
# or without Docker: cd ~/cfe-scrapper/service && python3 worker.py
```

The worker claims `hermes.jobs` (fed by the rpu-intake edge function or the
WhatsApp intake), runs the MiEspacio leg through Browser Use Cloud (MX
residential proxy, domain-scoped credential bindings, 2FA code via the
mailbox as a same-session follow-up), parses the CFDI, and writes the typed
model. Puppeteer fallback: `service/harvest/harvest.js`.

## 6. Hermes (the agent that can host all of this itself)

```bash
mkdir -p ~/.hermes/skills
cp -r ~/cfe-scrapper/hermes/skills/* ~/.hermes/skills/
```

Skills: cf-invoice-harvest, cf-tariff-nodes, bill-price-audit,
cfe-scrapper-ops (the ops runbook — it is the "how to run this system"
memory for the agent).

WhatsApp pairing: the Baileys bridge is vendored at
`hermes/whatsapp-bridge/` (the upstream hermes package does not ship it —
copy it to `~/.hermes/scripts/whatsapp-bridge`, `npm install`, then
`node bridge.js --pair-only --session ~/.hermes/whatsapp/session`).

Cron (agent-side): `cf-harvest-monthly` (1st, 09:00), `cf-tariff-nodes-
monthly` (3rd, 07:00), delivery local until WhatsApp pairing.

## 7. Evals

```bash
cd ~/cfe-scrapper/evals && python3 run.py     # 230-pt frozen metric
```

Fixtures (real bills + golden CFDI) are NOT in git — they live on the
operator's machine (`evals/fixtures/`).
