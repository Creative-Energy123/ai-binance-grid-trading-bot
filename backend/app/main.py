from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.auth import ensure_admin_user
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.routers import account, ai, auth, bot, journal, market, research
from app.schemas import HealthComponentOut, HealthOut
from app.services import portfolio
from app.services import secrets as secret_store
from app.services.engine import trading_engine
from app.services.exchange import exchange

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _restore_runtime_config() -> None:
    """Re-apply operator overrides saved on the bot row after a restart."""
    async with SessionLocal() as db:
        bot_row = await portfolio.get_bot(db)
        for key, value in (bot_row.config_overrides or {}).items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        settings.trading_mode = bot_row.mode
        applied = await secret_store.load_into_settings(db)
        if applied:
            logger.info("Loaded %d stored integration secret(s)", len(applied))
        # A restart never resumes trading on its own.
        bot_row.running = False
        bot_row.status_message = "Stopped (service restarted)"
        await db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await ensure_admin_user(db)
    await _restore_runtime_config()

    scheduler.add_job(
        trading_engine.tick,
        "interval",
        seconds=settings.engine_interval_seconds,
        id="engine_tick",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        trading_engine.reconcile,
        "interval",
        seconds=settings.reconcile_interval_seconds,
        id="reconcile",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started in %s mode", settings.trading_mode)
    yield
    scheduler.shutdown(wait=False)
    await exchange.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response


app.include_router(auth.router)
app.include_router(account.router)
app.include_router(bot.router)
app.include_router(market.router)
app.include_router(journal.router)
app.include_router(research.router)
app.include_router(ai.router)


@app.get("/api/health", response_model=HealthOut)
async def health() -> HealthOut:
    async with SessionLocal() as db:
        bot_row = await portfolio.get_bot(db)
        report = await trading_engine.health(db, bot_row)
        await db.commit()
    return HealthOut(
        status="ok" if report.ok else "degraded",
        mode=bot_row.mode,
        ok=report.ok,
        components=[HealthComponentOut(**c) for c in report.as_dict()["components"]],
        ai_configured=bool(settings.anthropic_api_key.strip()),
    )


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    """Prometheus-compatible exposition of the numbers that matter operationally."""
    async with SessionLocal() as db:
        bot_row = await portfolio.get_bot(db)
        perf = await portfolio.performance(db, mode=bot_row.mode)
        positions = await portfolio.open_positions(db)
    health_ok = 1 if trading_engine.last_health.ok else 0
    lines = [
        "# HELP scalper_equity_usdt Account equity in USDT",
        "# TYPE scalper_equity_usdt gauge",
        f"scalper_equity_usdt {bot_row.equity}",
        "# HELP scalper_open_positions Number of open positions",
        "# TYPE scalper_open_positions gauge",
        f"scalper_open_positions {len(positions)}",
        "# HELP scalper_realized_pnl_total Realized P&L in USDT",
        "# TYPE scalper_realized_pnl_total gauge",
        f"scalper_realized_pnl_total {bot_row.realized_pnl}",
        "# HELP scalper_trades_total Closed trades",
        "# TYPE scalper_trades_total counter",
        f"scalper_trades_total {perf.trades}",
        "# HELP scalper_win_rate Win rate percentage",
        "# TYPE scalper_win_rate gauge",
        f"scalper_win_rate {perf.win_rate}",
        "# HELP scalper_max_drawdown_pct Maximum drawdown percentage",
        "# TYPE scalper_max_drawdown_pct gauge",
        f"scalper_max_drawdown_pct {perf.max_drawdown_pct}",
        "# HELP scalper_health_ok 1 when all health components pass",
        "# TYPE scalper_health_ok gauge",
        f"scalper_health_ok {health_ok}",
        "# HELP scalper_running 1 when the bot is running",
        "# TYPE scalper_running gauge",
        f"scalper_running {1 if bot_row.running else 0}",
    ]
    return "\n".join(lines) + "\n"


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith(("api/", "metrics")):
            return PlainTextResponse(json.dumps({"detail": "Not Found"}), status_code=404)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return PlainTextResponse("Frontend not built", status_code=404)
