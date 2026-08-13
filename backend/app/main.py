from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import ensure_admin_user
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.routers import auth, bot
from app.schemas import HealthOut
from app.services.binance_client import binance_client
from app.services.grid_engine import run_ai_analysis, tick

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await ensure_admin_user(db)

    scheduler.add_job(tick, "interval", seconds=settings.price_poll_seconds, id="price_tick")
    scheduler.add_job(
        run_ai_analysis,
        "interval",
        minutes=settings.ai_analysis_interval_minutes,
        id="ai_analysis",
    )
    scheduler.start()
    logger.info("Scheduler started (testnet=%s)", settings.binance_testnet)
    yield
    scheduler.shutdown(wait=False)
    await binance_client.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(bot.router)


@app.get("/api/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(status="ok", site_url=settings.site_url, testnet=settings.binance_testnet)


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return {"detail": "Not Found"}
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return {"detail": "Frontend not built"}
