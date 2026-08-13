from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import Actor, AiAnalysis, AuditLog, GridConfig, Trade, User
from app.schemas import (
    AiAnalysisOut,
    AuditOut,
    BacktestIn,
    BacktestOut,
    BotStatusOut,
    GridConfigIn,
    GridConfigOut,
    LiveConfirmIn,
    MessageOut,
    TradeOut,
)
from app.services import grid_engine
from app.services.backtest import backtest_grid
from app.services.binance_client import binance_client

router = APIRouter(prefix="/api", tags=["bot"])
settings = get_settings()


@router.get("/status", response_model=BotStatusOut)
async def status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BotStatusOut:
    state = await grid_engine.get_or_create_state(db)
    config = await grid_engine.get_active_config(db)
    return BotStatusOut(
        running=state.running,
        killed=state.killed,
        live_confirmed=state.live_confirmed,
        testnet=settings.binance_testnet,
        last_price=state.last_price,
        realized_pnl=state.realized_pnl,
        unrealized_pnl=state.unrealized_pnl,
        peak_equity=state.peak_equity,
        daily_pnl=state.daily_pnl,
        status_message=state.status_message,
        max_capital_usdt=settings.max_capital_usdt,
        symbol=config.symbol if config else None,
    )


@router.get("/config", response_model=GridConfigOut | None)
async def get_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GridConfig | None:
    return await grid_engine.get_active_config(db)


@router.put("/config", response_model=GridConfigOut)
async def put_config(
    body: GridConfigIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> GridConfig:
    if body.upper_price <= body.lower_price:
        raise HTTPException(400, "upper_price must exceed lower_price")
    if body.capital_usdt > settings.max_capital_usdt:
        raise HTTPException(400, f"capital exceeds hard cap {settings.max_capital_usdt}")

    # Deactivate previous
    result = await db.execute(select(GridConfig).where(GridConfig.is_active.is_(True)))
    for row in result.scalars().all():
        row.is_active = False

    cfg = GridConfig(**body.model_dump(), is_active=True)
    db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    await grid_engine.audit(db, Actor.USER.value, "config_update", str(body.model_dump()))
    return cfg


@router.post("/bot/start", response_model=BotStatusOut)
async def bot_start(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BotStatusOut:
    try:
        await grid_engine.start_bot(db)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await status(db, user)


@router.post("/bot/stop", response_model=BotStatusOut)
async def bot_stop(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BotStatusOut:
    state = await grid_engine.get_or_create_state(db)
    state.running = False
    state.status_message = "stopped"
    await db.commit()
    await grid_engine.audit(db, Actor.USER.value, "bot_stop", "")
    return await status(db, _)


@router.post("/bot/kill", response_model=BotStatusOut)
async def bot_kill(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BotStatusOut:
    await grid_engine.kill_switch(db)
    return await status(db, _)


@router.post("/bot/clear-kill", response_model=BotStatusOut)
async def bot_clear_kill(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BotStatusOut:
    await grid_engine.clear_kill(db)
    return await status(db, _)


@router.post("/bot/confirm-live", response_model=BotStatusOut)
async def confirm_live(
    body: LiveConfirmIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BotStatusOut:
    try:
        await grid_engine.confirm_live(db, body.confirm_phrase)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await status(db, _)


@router.get("/trades", response_model=list[TradeOut])
async def trades(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    limit: int = 100,
) -> list[Trade]:
    result = await db.execute(select(Trade).order_by(Trade.id.desc()).limit(limit))
    return list(result.scalars().all())


@router.get("/audit", response_model=list[AuditOut])
async def audit_logs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    limit: int = 100,
) -> list[AuditLog]:
    result = await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return list(result.scalars().all())


@router.get("/ai", response_model=list[AiAnalysisOut])
async def ai_list(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
    limit: int = 50,
) -> list[AiAnalysis]:
    result = await db.execute(select(AiAnalysis).order_by(AiAnalysis.id.desc()).limit(limit))
    return list(result.scalars().all())


@router.post("/ai/run", response_model=MessageOut)
async def ai_run(
    _: User = Depends(get_current_user),
) -> MessageOut:
    await grid_engine.run_ai_analysis()
    return MessageOut(message="AI analysis job completed")


@router.post("/ai/{analysis_id}/apply", response_model=AiAnalysisOut)
async def ai_apply(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AiAnalysis:
    try:
        return await grid_engine.apply_ai_suggestion(db, analysis_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/backtest", response_model=BacktestOut)
async def backtest(
    body: BacktestIn,
    _: User = Depends(get_current_user),
) -> BacktestOut:
    try:
        ohlcv = await binance_client.fetch_ohlcv(body.symbol, "1h", body.candles)
        closes = [float(c[4]) for c in ohlcv]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Failed to fetch candles: {exc}") from exc
    result = backtest_grid(
        closes,
        body.lower_price,
        body.upper_price,
        body.grid_count,
        body.capital_usdt,
        body.grid_type,
    )
    return BacktestOut(**result.__dict__)
