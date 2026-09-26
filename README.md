# Binance Adaptive Scalping Bot

A regime-aware scalping system for Binance Spot and USDT-M Futures, with a FastAPI
backend, a React dashboard, an event-driven backtester and an AI assistant that
explains the bot's decisions.

**It is not a profit machine.** The design goal is capital preservation: the bot
is built so that *doing nothing* is a normal, frequent and correct outcome. No
configuration in this repository guarantees or implies a profit, and backtested
or simulated results are not evidence of future returns.

---

## What makes it different from an indicator bot

It never runs a rule like `RSI < 30 → BUY`. Every decision passes through a
pipeline, and any stage can stop the trade:

```
MarketDataService      candles for 15m / 5m / 1m, closed candles only
        ↓
IndicatorEngine        EMA, RSI, MACD, ADX, ATR, Bollinger, VWAP, volume
        ↓
MarketRegimeEngine     bull / bear / ranging / high-vol / low-vol / unclear
        ↓
SignalEngine           strategies allowed by the regime, scored 0–100
        ↓
RiskEngine             can veto any signal
        ↓
PositionSizer          quantity derived from risk, not from a fixed amount
        ↓
ExecutionEngine        order placement, fill confirmation, protective stop
        ↓
OrderManager           reconciliation, partial fills, retries
        ↓
PortfolioManager       equity, snapshots, performance
```

A typical outcome looks like this:

```
BTCUSDT
Market regime: Strong Bull
  Trend strategy:  ENABLED
  Range strategy:  DISABLED
  Short strategy:  DISABLED
Signal: LONG · Score 84/100 · Risk 0.35%
```

and just as often like this:

```
BTCUSDT
Market regime: Unclear / High Volatility
Trading: DISABLED
Reason: conflicting higher-timeframe signals
```

## Trading modes

| Mode | Places orders | Uses real money | Notes |
| --- | --- | --- | --- |
| `backtest` | no | no | Historical replay in the Research tab |
| `paper` | no | no | Live Binance data, simulated fills with fees and slippage |
| `testnet` | yes | no | Binance testnet keys |
| `live` | yes | **yes** | Requires an explicit confirmation phrase and key validation |

The bot always comes up **stopped** after a restart, in whatever mode was last
set. Switching to `live` additionally requires confirming in the dashboard, and
the confirmation is refused if the API key has withdrawal permission enabled.

## Quick start

```bash
cp .env.sample .env
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
# paste the value into CREDENTIALS_ENCRYPTION_KEY, then set ADMIN_PASSWORD and JWT_SECRET
docker compose up -d --build
```

The dashboard is then on <http://localhost:8080>. Sign in with `ADMIN_EMAIL` /
`ADMIN_PASSWORD`.

### Local development

```bash
cd backend && pip install -r requirements-dev.txt && uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm run dev
```

```bash
cd backend && python -m pytest        # 48 tests, no network access needed
cd frontend && npm run typecheck
```

### Database migrations

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/scalper python -m alembic upgrade head
```

The app also creates tables on first boot, so migrations matter mainly for
upgrades to an existing deployment.

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/STRATEGY.md](docs/STRATEGY.md) | Regime detection, the three strategies, scoring, stops and targets |
| [docs/RISK.md](docs/RISK.md) | Every risk limit, how sizing works, what halts the bot |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment, secrets, monitoring, backups |
| [docs/GO_LIVE_CHECKLIST.md](docs/GO_LIVE_CHECKLIST.md) | The checks to complete before enabling live trading |

## API surface

| Area | Endpoints |
| --- | --- |
| Auth | `POST /api/auth/login`, `POST /api/auth/refresh`, `GET /api/auth/me` |
| Bot | `GET /api/bot/status`, `/overview`, `/positions`, `/evaluations`, `POST /start` `/pause` `/stop` `/close-all` `/emergency-stop` `/mode` `/confirm-live` `/risk-config` |
| Market | `GET /api/market/scan`, `/candles`, `/analysis/{symbol}` |
| Journal | `GET /api/journal/trades`, `/trades.csv`, `/signals`, `/risk-events`, `/alerts`, `/performance` |
| Research | `POST /api/research/backtest`, `GET /api/research/backtests` |
| AI | `POST /api/ai/ask`, `/explain/{symbol}`, `GET /api/ai/history` |
| Ops | `GET /api/health`, `GET /metrics` (Prometheus) |

Interactive docs are at `/docs` when the backend runs.

## Known gaps

- Market data is polled over REST on the engine interval, not streamed over
  Binance WebSockets. The staleness check and health component exist and work,
  but a WebSocket feed would cut latency and API weight.
- Redis is provisioned in the compose stack but is not yet used by the backend;
  scheduling is handled in-process by APScheduler rather than Celery.
- Parameter optimisation is manual: the backtester provides the train /
  validation / out-of-sample split and the overfitting warning, but there is no
  automated search.

## Security

- Exchange credentials are encrypted at rest with AES-256-GCM and are never
  returned by any endpoint — the API exposes only a masked fragment.
- JWT access tokens plus rotating refresh tokens; roles are `viewer` /
  `trader` / `admin`.
- Login lockout after repeated failures, audit logging on every state change,
  rate limiting and security headers at the Nginx layer.
- **Never enable withdrawal permission on the Binance API key.** Restrict the key
  to your server's IP address. The bot refuses to go live with a withdrawal-capable key.

## The AI assistant

It reads the bot's own state — regime, indicators, score breakdown, risk
decisions, recent trades — and explains it. It has no order-placement path, it
cannot change configuration, and its prompt forbids claiming any trade will be
profitable.

## Disclaimer

This software is provided for research and educational purposes. Trading
cryptocurrency carries substantial risk of loss, and leveraged futures trading
can lose more than the initial margin. You are responsible for any orders this
software places on your behalf. Do not run it with money you cannot afford to
lose.
