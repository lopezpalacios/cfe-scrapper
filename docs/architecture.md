# Architecture + deployment

## Hosts

| Host | Role | Key paths |
|---|---|---|
| Mac mini (user `newman`, gui/503) | Hermes agent, gateway, adb (phone USB), orchestrator | `~/.hermes/` (config, skills), `~/evals/`, `~/newman-agents/`, `~/nix/hermes-agent/` |
| NordVPN on the mini (NordVPN.app, MX server) | **ALL CFE/CENACE egress** — the MX egress for the scraper service | connected only while the service runs |
| newman-ubuntu (colima VM) | legacy egress + MiEspacio scripts (retired as the egress path — flaky SSH) | `/root/*.js` recipes documented in docs/harvest-recipes.md |
| Galaxy A16 (RFGL41FS57N, USB) | CFE Contigo app (RPU-only lookup), its own NordVPN-MX | adb on the mini |
| Supabase `oioyawhgvazebtarigpc` | the database | schemas above |
| Mac (operator) | gws source, fixture origin | — |

Chain: `ssh mini` → `ssh newman@localhost` → `ssh newman-ubuntu` (from the
mini, direct). adb runs on the mini, never on the box.

## Hermes (agent)

- v0.21.6, native nix package: flake `~/nix/hermes-agent/` wraps
  `github:NousResearch/hermes-agent/v0.21.6` (attr `messaging`). Upgrade:
  `nix flake update hermes-agent && nix profile upgrade`.
- Provider: OpenRouter (`OPENROUTER_API_KEY` in `~/.hermes/.env`), model
  `deepseek/deepseek-v4-pro`.
- Gateway: running as a leaked process tree (launchd plist exists at
  `~/Library/LaunchAgents/ai.hermes.gateway.plist`; bootstrap failed —
  re-bootstrap after a reboot). Serves the cron ticker; WhatsApp unpaired.
- Cron: `cf-harvest-monthly` (1st, 09:00), `cf-tariff-nodes-monthly`
  (3rd, 07:00). Both deliver local until WhatsApp pairing.
- Skills: cf-invoice-harvest, cf-tariff-nodes, bill-price-audit (this repo,
  `hermes/skills/`).

## Egress hard rules

- CFE/CENACE/DOF downloads: ONLY while the mini's NordVPN is connected to a
  MEXICO server. The service enforces this at runtime: worker.py refuses any
  CFE fetch unless the current egress IP geolocates to MX (vpn_guard). The
  box (newman-ubuntu) is the legacy fallback; its SSH is flaky, so the
  service no longer depends on it.
- Supabase is not CFE-facing — reachable regardless of VPN state.
- MiEspacio ~6 downloads/session, re-login between batches, honor 503
  Retry-After. Never log CFE passwords; the service-role key lives at
  `/root/tariff/.supabase_key` (0600) and in `~/.cfe.env` (0600).
- Barcode = the RPU's carrier only; a degraded barcode fails clean
  (`barcode_unreadable`), never guesses.

## Database access

- Migrations: this repo's `supabase/migrations/` (applied in order).
- Loaders: `public.rpc_load_tarifa_cuota` / `rpc_load_nodosp` —
  service-role only. The rpu-intake edge function: key-authed
  (`x-intake-key`), verify_jwt off, CORS *, idempotent per active RPU.
- Direct connection (workers/audit): `DATABASE_URL` in `~/.cfe.env`
  (session pooler + sslmode=require).
