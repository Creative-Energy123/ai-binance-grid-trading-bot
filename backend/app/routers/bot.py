from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import audit, get_current_user, require_admin, require_trader
from app.config import TRADING_MODES, get_settings
from app.db import get_db
from app.models import BotInstance, User
from app.schemas import (
    BotStatusOut,
    EmergencyStopIn,
    LiveConfirmIn,
    MessageOut,
    ModeChangeIn,
    OverviewOut,
    PositionOut,
    RiskConfigIn,
)
from app.services import portfolio
from app.services.engine import trading_engine
from app.services.exchange import exchange

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bot", tags=["bot"])


def _status(bot: BotInstance) -> BotStatusOut:
    settings = get_settings()
    return BotStatusOut(
        mode=bot.mode,
        running=bot.running,
        paused=bot.paused,
        emergency_stopped=bot.emergency_stopped,
        live_confirmed=bot.live_confirmed,
        status_message=bot.status_message,
        equity=bot.equity,
        peak_equity=bot.peak_equity,
        realized_pnl=bot.realized_pnl,
        consecutive_losses=bot.consecutive_losses,
        cooldown_until=bot.cooldown_until,
        last_tick_at=bot.last_tick_at,
        symbols=settings.symbol_list,
        futures_enabled=settings.futures_enabled,
        leverage=settings.futures_leverage if settings.futures_enabled else 1,
        places_real_orders=settings.places_real_orders,
    )


@router.get("/status", response_model=BotStatusOut)
async def status(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return _status(await portfolio.get_bot(db))


@router.get("/overview", response_model=OverviewOut)
async def overview(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    bot = await portfolio.get_bot(db)
    perf = await portfolio.performance(db, mode=bot.mode)
    positions = await portfolio.open_positions(db)
    unrealized = sum(p.unrealized_pnl for p in positions)
    exposure = sum(p.remaining_quantity * p.entry_price / max(p.leverage, 1) for p in positions)
    return OverviewOut(
        equity=bot.equity + unrealized,
        balance=bot.equity,
        available=max(bot.equity - exposure, 0.0),
        today_pnl=perf.today_pnl,
        week_pnl=perf.week_pnl,
        month_pnl=perf.month_pnl,
        win_rate=perf.win_rate,
        profit_factor=perf.profit_factor,
        max_drawdown_pct=perf.max_drawdown_pct,
        trades=perf.trades,
        open_positions=len(positions),
        unrealized_pnl=unrealized,
    )


@router.get("/positions", response_model=list[PositionOut])
async def positions(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return await portfolio.open_positions(db)


@router.get("/evaluations")
async def evaluations(_: User = Depends(get_current_user)) -> dict:
    """What the engine decided on its most recent tick, per symbol."""
    return {
        "last_tick_at": trading_engine.last_tick_at.isoformat() if trading_engine.last_tick_at else None,
        "symbols": trading_engine.last_evaluations,
    }


@router.post("/start", response_model=BotStatusOut)
async def start(db: AsyncSession = Depends(get_db), user: User = Depends(require_trader)):
    bot = await portfolio.get_bot(db)
    try:
        await trading_engine.start(db, bot)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit(db, user, "bot_start", f"mode={bot.mode}")
    await db.commit()
    return _status(bot)


@router.post("/pause", response_model=BotStatusOut)
async def pause(db: AsyncSession = Depends(get_db), user: User = Depends(require_trader)):
    bot = await portfolio.get_bot(db)
    await trading_engine.pause(db, bot)
    await audit(db, user, "bot_pause")
    await db.commit()
    return _status(bot)


@router.post("/stop", response_model=BotStatusOut)
async def stop(db: AsyncSession = Depends(get_db), user: User = Depends(require_trader)):
    bot = await portfolio.get_bot(db)
    await trading_engine.stop(db, bot)
    await audit(db, user, "bot_stop")
    await db.commit()
    return _status(bot)


@router.post("/close-all", response_model=MessageOut)
async def close_all(db: AsyncSession = Depends(get_db), user: User = Depends(require_trader)):
    bot = await portfolio.get_bot(db)
    closed = await trading_engine.close_all(db, bot)
    await audit(db, user, "close_all_positions", f"closed={closed}")
    await db.commit()
    return MessageOut(message=f"Closed {closed} position(s)", data={"closed": closed})


@router.post("/emergency-stop", response_model=MessageOut)
async def emergency_stop(
    payload: EmergencyStopIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_trader),
):
    bot = await portfolio.get_bot(db)
    result = await trading_engine.emergency_stop(db, bot, payload.close_positions)
    await audit(db, user, "emergency_stop", str(result))
    await db.commit()
    return MessageOut(message="Emergency stop engaged", data=result)


@router.post("/mode", response_model=BotStatusOut)
async def set_mode(
    payload: ModeChangeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    settings = get_settings()
    bot = await portfolio.get_bot(db)

    if payload.mode not in TRADING_MODES:
        raise HTTPException(status_code=400, detail=f"Mode must be one of {TRADING_MODES}")

    open_count = len(await portfolio.open_positions(db))
    if open_count:
        raise HTTPException(
            status_code=400,
            detail=f"Close the {open_count} open position(s) before switching mode",
        )

    if payload.mode == "live" and not bot.live_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Confirm live trading first via POST /api/bot/confirm-live",
        )
    if payload.mode in ("testnet", "live") and not exchange.has_credentials():
        raise HTTPException(
            status_code=400, detail=f"{payload.mode} mode requires Binance API keys"
        )

    bot.mode = payload.mode
    bot.running = False
    bot.status_message = f"Mode set to {payload.mode}; bot stopped"
    settings.trading_mode = payload.mode
    await exchange.reset()  # rebuild the ccxt client for the new environment
    await audit(db, user, "mode_change", payload.mode)
    await db.commit()
    return _status(bot)


@router.post("/confirm-live", response_model=BotStatusOut)
async def confirm_live(
    payload: LiveConfirmIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    settings = get_settings()
    if payload.confirm_phrase != settings.live_confirm_phrase:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation phrase must be exactly "{settings.live_confirm_phrase}"',
        )
    if not payload.acknowledge_risk:
        raise HTTPException(status_code=400, detail="Risk acknowledgement is required")
    if not exchange.has_credentials():
        raise HTTPException(status_code=400, detail="Binance API keys are not configured")

    validation = await exchange.validate_credentials()
    if not validation.get("valid"):
        raise HTTPException(
            status_code=400, detail=f"API keys failed validation: {validation.get('error')}"
        )
    if validation.get("can_withdraw"):
        raise HTTPException(
            status_code=400,
            detail=(
                "These API keys have withdrawal permission enabled. Disable withdrawals on the "
                "Binance API key before enabling live trading."
            ),
        )

    bot = await portfolio.get_bot(db)
    bot.live_confirmed = True
    bot.status_message = "Live trading confirmed (bot still stopped)"
    await audit(db, user, "confirm_live", "live trading confirmed")
    await db.commit()
    return _status(bot)


@router.post("/risk-config", response_model=MessageOut)
async def update_risk(
    payload: RiskConfigIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    settings = get_settings()
    bot = await portfolio.get_bot(db)
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(settings, key, value)
    bot.config_overrides = {**(bot.config_overrides or {}), **changes}
    await audit(db, user, "risk_config", str(changes))
    await db.commit()
    return MessageOut(message="Risk configuration updated", data=changes)


@router.get("/risk-config")
async def read_risk(_: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    return {
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_weekly_loss_pct": settings.max_weekly_loss_pct,
        "max_positions": settings.max_positions,
        "max_consecutive_losses": settings.max_consecutive_losses,
        "max_trades_per_hour": settings.max_trades_per_hour,
        "max_symbol_exposure_pct": settings.max_symbol_exposure_pct,
        "max_total_exposure_pct": settings.max_total_exposure_pct,
        "loss_cooldown_minutes": settings.loss_cooldown_minutes,
        "minimum_signal_score": settings.minimum_signal_score,
        "score_weights": settings.score_weights,
        "stop_mode": settings.stop_mode,
        "atr_stop_multiplier": settings.atr_stop_multiplier,
        "tp1_r_multiple": settings.tp1_r_multiple,
        "tp2_r_multiple": settings.tp2_r_multiple,
        "futures_enabled": settings.futures_enabled,
        "futures_leverage": settings.futures_leverage,
        "taker_fee_pct": settings.taker_fee_pct,
        "slippage_pct": settings.slippage_pct,
    }
