# Deployment

## Requirements

- A host in a region Binance serves (a blocked region returns HTTP 451; the bot
  reports this explicitly and can route through `BINANCE_PROXY`).
- Docker and Docker Compose.
- Accurate system time. Binance rejects requests with skewed timestamps — run
  `chrony` or `systemd-timesyncd` and verify with `timedatectl`.

## First deployment

```bash
git clone <your-fork> scalper && cd scalper
cp .env.sample .env && chmod 600 .env
```

Generate the credential encryption key and put it in `.env`:

```bash
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Then set, at minimum:

| Variable | Why |
| --- | --- |
| `CREDENTIALS_ENCRYPTION_KEY` | Required before any API key can be stored |
| `JWT_SECRET` | At least 32 random characters |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Seeded on first boot |
| `POSTGRES_PASSWORD` / `DATABASE_URL` | Keep them in sync |
| `SITE_URL` / `CORS_ORIGINS` | Your real origin |
| `TRADING_MODE` | Leave at `paper` for the first run |

Bring it up:

```bash
docker compose up -d --build
docker compose logs -f backend
```

The dashboard is served by Nginx on `HTTP_PORT` (default 8080).

## Moving to a new VPS

Usually done because Binance geo-blocks the current host (HTTP 451). Pick a
region Binance serves — Tokyo is closest to their infrastructure, Singapore,
Frankfurt and London also work. Latency matters for scalping, so prefer moving
the host over proxying through another region.

**1. Prepare the host**

```bash
curl -fsSL https://get.docker.com | sh
docker network create web
```

**2. Start the reverse proxy** (the app deploy does not manage it)

```bash
mkdir -p ~/traefik && cd ~/traefik
# copy deploy/traefik.yml from this repo to the host
LETSENCRYPT_EMAIL=you@example.com docker compose -f traefik.yml up -d
```

**3. Firewall** — allow 80 and 443, plus whichever port SSH listens on.

```bash
ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 2222/tcp && ufw enable
```

**4. Point DNS** at the new IP and wait for it to resolve. Traefik cannot issue
a certificate until the domain resolves to this host.

```bash
getent hosts your-domain.example
```

**5. Update the GitHub settings**

| Setting | Where | Notes |
| --- | --- | --- |
| `VPS_HOST` | secret | New IP or hostname |
| `VPS_USER` / `VPS_PASSWORD` | secret | Credentials on the new host |
| `VPS_PORT` | variable | Only if SSH is not on 22 — e.g. `2222` |
| `VPS_SSH_KEY` | secret | Optional private key; used instead of the password when set |

Key authentication is more reliable than a password here, because some hosts
run fail2ban or a low `MaxStartups` and reset repeated password handshakes. To
use it, generate a key, add the public half to the host and the private half to
the secret:

```bash
ssh-keygen -t ed25519 -f deploy_key -N ""
ssh-copy-id -i deploy_key.pub -p 2222 user@your-host
# paste the contents of deploy_key (the private half) into VPS_SSH_KEY
```

**6. Deploy** by pushing to `main`, or run the workflow manually.

**7. Re-check the Binance API key IP allowlist.** If the key is restricted to
the old server's IP it will be rejected from the new one. Update it to the new
IP before switching out of paper mode.

The new host starts with an empty database, so the admin user is recreated from
`ADMIN_EMAIL` / `ADMIN_PASSWORD`. To carry trade history over, restore a dump
from the old host before the first deploy; otherwise nothing needs migrating —
API credentials are re-entered through Settings.

## TLS

Terminate TLS in front of Nginx — a reverse proxy such as Caddy or Traefik, or
certbot against the bundled Nginx. Never expose the backend container port
directly; `/metrics` is restricted to private networks in `deploy/nginx.conf`
and that protection lives in the Nginx layer.

## Binance API keys

1. Create the key with **trading enabled and withdrawals disabled**.
2. Restrict it to your server's IP address.
3. Enter it in the dashboard under Settings → Binance API credentials. It is
   encrypted with AES-256-GCM before storage and never returned by the API.
4. The bot validates the key immediately and **refuses to enable live trading if
   the key can withdraw**.

Testnet keys are separate from live keys — testnet mode needs Binance testnet
credentials.

## Migrations

```bash
docker compose exec backend python -m alembic upgrade head
```

The app also creates missing tables at boot, so this matters mainly when
upgrading an existing deployment. Take a database dump first:

```bash
docker compose exec db pg_dump -U scalper scalper > backup-$(date +%F).sql
```

## Monitoring

- `GET /api/health` — component-level status (market data, Binance API, database,
  risk engine, execution engine, position sync).
- `GET /metrics` — Prometheus exposition: equity, open positions, realized P&L,
  trades, win rate, max drawdown, health flag, running flag.

A minimal scrape config:

```yaml
scrape_configs:
  - job_name: scalper
    static_configs:
      - targets: ["scalper_nginx:80"]
```

Useful alerts: `scalper_health_ok == 0` for 5 minutes, `scalper_running == 0`
while you expect it running, and a sharp move in `scalper_max_drawdown_pct`.

Application logs are JSON on stdout, so `docker compose logs` feeds any log
shipper directly.

## Alerts

Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, `ALERT_WEBHOOK_URL`, and/or SMTP
settings. Critical events (emergency stop, daily loss limit, stop-order failure,
API failure, position mismatch) also go to email when SMTP is configured. Every
alert is persisted and shown in the dashboard regardless of delivery success —
alert delivery failures never interrupt trading.

## Backups

Back up the Postgres volume and `.env` separately. Losing
`CREDENTIALS_ENCRYPTION_KEY` makes stored API keys unreadable; you would need to
re-enter them.

## Upgrades

```bash
git pull
docker compose up -d --build
docker compose exec backend python -m alembic upgrade head
```

The bot comes up **stopped** after any restart and must be started explicitly.
Confirm the health strip is green and the mode is what you expect before starting.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| HTTP 451 from Binance | Region is blocked. Set `BINANCE_PROXY` to a compliant proxy in an allowed region, or move the host. |
| "Timestamp for this request" errors | Host clock drift. Fix NTP. |
| `MARKET DATA FAIL` in the health strip | No successful candle fetch recently — check connectivity and rate limits. |
| `POSITION SYNC FAIL` | The exchange and the database disagree. The bot pauses itself; reconcile manually before resuming. |
| Live mode refused | Either live was not confirmed, or the API key has withdrawal permission enabled. |
