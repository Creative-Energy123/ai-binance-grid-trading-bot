# Backend

FastAPI service for the adaptive scalping bot. `app.main:app` is the ASGI entry
point; it also serves the built frontend from `backend/static` when present.

## Layout

```
app/
  config.py            all runtime settings (env-driven)
  security.py          AES-256-GCM helpers for exchange credentials
  auth.py              JWT access + rotating refresh tokens, roles, audit
  models.py            SQLAlchemy schema
  schemas.py           Pydantic request/response models
  routers/             auth, account, bot, market, journal, research, ai
  services/
    exchange.py        ccxt Binance wrapper (spot + USDT-M futures)
    market_data.py     candle cache, staleness, MarketContext assembly
    indicators.py      pure-function indicators, no look-ahead
    regime.py          market regime classification
    strategies/        trend pullback, breakout, range scalping
    signals.py         weighted scoring, stops and targets
    risk.py            the only component that can veto a signal
    sizing.py          risk-derived position sizing
    execution.py       order placement, fill confirmation, position management
    portfolio.py       equity, snapshots, performance statistics
    engine.py          the tick loop, health checks and bot controls
    backtest.py        event-driven backtester
    scanner.py         per-symbol dashboard snapshot
    ai_assistant.py    explanation-only assistant
    alerts.py          Telegram / webhook / email
migrations/            Alembic
tests/                 63 tests, no network access required
```

## Commands

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
python -m pytest
python -m alembic upgrade head
ruff check app tests
```
