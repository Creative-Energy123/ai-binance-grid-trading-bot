# AI-Assisted Binance Grid Trading Bot

Self-hosted spot **grid trading** bot with an authenticated dashboard, Anthropic (Claude) **advisory** analysis, and Docker / GitHub Actions deploy to **https://trade.creativeenergy.pk**.

> **Risk disclaimer:** Grid strategies can lose significant capital in trending markets. Nothing here guarantees profit. Default is **Binance Testnet**. Enable live trading only after backtests, with withdrawals disabled on the API key, IP restriction to your VPS, and a hard capital cap.

Deploy pattern mirrors [Mind-Sync-Clinic](../Mind-Sync-Clinic): build → push to GHCR → SSH to VPS → Traefik-labeled `docker compose`.

## Stack

| Layer | Tech |
|--------|------|
| API | Python 3.11, FastAPI, APScheduler |
| Exchange | ccxt (Binance spot, testnet/live) |
| AI | Anthropic API (server-side only) |
| UI | React, TypeScript, Tailwind (served by API in prod) |
| Data | PostgreSQL 16, Redis 7 |
| Edge | Traefik + Let's Encrypt on VPS (`web` Docker network) |

## Local quick start

```bash
cp .env.sample .env
# edit .env — set ADMIN_PASSWORD, JWT_SECRET, POSTGRES_PASSWORD
docker compose up -d --build
```

- App: http://localhost:8000  
- Health: http://localhost:8000/api/health  
- Login with `ADMIN_EMAIL` / `ADMIN_PASSWORD`

Frontend-only dev:

```bash
cd frontend && npm install && npm run dev
# API on :8000, Vite proxies /api
```

## Production domain

| Setting | Value |
|---------|--------|
| Public URL | `https://trade.creativeenergy.pk` |
| Traefik host | `trade.creativeenergy.pk` (+ `www`) |
| Remote app dir | `~/ai-binance-grid-trading-bot` |
| Containers | `gridbot_app`, `gridbot_db`, `gridbot_redis` |
| GHCR image | `ghcr.io/creative-energy123/ai-binance-grid-trading-bot` |

Adjust `IMAGE_NAME` in `.github/workflows/deploy.yml` if your GitHub owner/org differs from `creative-energy123`.

## GitHub Actions deploy (Mind-Sync style)

Workflow: `.github/workflows/deploy.yml`

1. On `main` push: build multi-stage Docker image, push to GHCR  
2. SSH to VPS (`appleboy/ssh-action`), pull image, write `docker-compose.yml` with Traefik labels  
3. `docker compose up -d`, health-check `/api/health`

### Repository secrets

| Secret | Purpose |
|--------|---------|
| `VPS_HOST` | VPS IP or hostname |
| `VPS_USER` | SSH user |
| `VPS_PASSWORD` | SSH password (also used for `sudo`) |
| `ADMIN_EMAIL` | Dashboard admin email (seeded) |
| `ADMIN_PASSWORD` | Dashboard admin password |
| `JWT_SECRET` | JWT signing secret (≥32 chars) |
| `BINANCE_API_KEY` | Binance API key (trading only) |
| `BINANCE_API_SECRET` | Binance API secret |
| `ANTHROPIC_API_KEY` | Claude API key |
| `POSTGRES_PASSWORD` | Postgres password |
| `TELEGRAM_BOT_TOKEN` | Optional alerts |
| `TELEGRAM_CHAT_ID` | Optional alerts |
| `ALERT_WEBHOOK_URL` | Optional generic webhook |

`GITHUB_TOKEN` is provided by Actions for GHCR login (same as Mind-Sync).

### Repository variables

| Variable | Example / default |
|----------|-------------------|
| `SITE_URL` | `https://trade.creativeenergy.pk` |
| `APP_DOMAIN` | `trade.creativeenergy.pk` |
| `CORS_ORIGINS` | `https://trade.creativeenergy.pk` |
| `BINANCE_TESTNET` | `true` |
| `MAX_CAPITAL_USDT` | `500` |
| `POSTGRES_USER` | `gridbot` |
| `POSTGRES_DB` | `gridbot` |
| `AI_ANALYSIS_INTERVAL_MINUTES` | `30` |
| `AI_AUTO_APPLY` | `false` |

## VPS prerequisites (one-time)

1. Docker + Docker Compose plugin installed  
2. Traefik already running and attached to external Docker network **`web`** (same as Mind-Sync Clinic) with `web` / `websecure` entrypoints and `letsencrypt` cert resolver  
3. DNS **A record**: `trade.creativeenergy.pk` → VPS public IP (and optional `www`)  
4. Ports 80/443 open to Traefik  
5. Push this repo to GitHub under an account that can publish `ghcr.io/<owner>/ai-binance-grid-trading-bot` (package write on push)  
6. After first deploy, confirm: `https://trade.creativeenergy.pk/api/health`

If Traefik is not yet installed on the VPS, install it before the first Actions deploy (Mind-Sync already expects this shared `web` network).

## Binance API key checklist

1. Prefer **Testnet** keys until you are ready  
2. Live key: **enable spot trading only**, **disable withdrawals**  
3. Restrict key to the VPS static IP  
4. Live mode: set `BINANCE_TESTNET=false`, then in the UI click **Confirm live trading** (phrase `ENABLE LIVE TRADING`)

## Kill switch & risk controls

- UI / `POST /api/bot/kill` — cancel open orders and halt  
- Max drawdown % and daily loss limit auto-halt  
- `MAX_CAPITAL_USDT` hard cap per instance  
- AI suggestions require **Apply** unless `AI_AUTO_APPLY=true`  
- AI never places exchange orders

## Project layout

```
.ai-binance-grid-trading-bot/
  .github/workflows/deploy.yml
  backend/app/          # FastAPI, grid engine, Binance, Anthropic
  frontend/             # React dashboard
  Dockerfile            # frontend build + API image
  docker-compose.yml    # local db/redis/app
  .env.sample
```

## License / responsibility

You are solely responsible for exchange account security, regulatory compliance, and trading losses. This software is provided as a self-hosted toolkit, not financial advice.
