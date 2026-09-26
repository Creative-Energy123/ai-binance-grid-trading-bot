from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import audit, get_current_user, require_admin, require_trader
from app.config import TRADING_MODES, get_settings, reload_settings
from app.db import get_db
from app.models import BotInstance, User
from app.schemas import (
    TIMEFRAMES,
    BotStatusOut,
    ConfigIn,
    EmergencyStopIn,
    LiveConfirmIn,
    MessageOut,
    ModeChangeIn,
    OverviewOut,
    PositionOut,
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


EDITABLE_FIELDS = tuple(ConfigIn.model_fields.keys())

# Changing these mid-flight would leave open positions being managed by rules
# they were not entered under.
REQUIRES_FLAT = {
    "symbols",
    "timeframe_primary",
    "timeframe_setup",
    "timeframe_entry",
    "futures_enabled",
    "futures_leverage",
}


def _current_config() -> dict:
    settings = get_settings()
    return {field: getattr(settings, field) for field in EDITABLE_FIELDS}


@router.get("/config")
async def read_config(_: User = Depends(get_current_user)) -> dict:
    """Current values plus the metadata the settings UI needs to render them."""
    settings = get_settings()
    return {
        "values": _current_config(),
        "meta": {
            "timeframes": list(TIMEFRAMES),
            "modes": list(TRADING_MODES),
            "stop_modes": ["atr", "structure", "fixed"],
            "requires_flat": sorted(REQUIRES_FLAT),
            "round_trip_cost_pct": settings.round_trip_cost_pct,
            "normalised_weights": settings.score_weights,
        },
    }


@router.post("/config", response_model=MessageOut)
async def update_config(
    payload: ConfigIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Apply configuration to the running engine and persist it on the bot row
    so it survives a restart."""
    settings = get_settings()
    bot = await portfolio.get_bot(db)
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No settings were supplied")

    # Only count fields that actually differ, so re-saving an unchanged form
    # does not trip the open-position guard.
    current = _current_config()
    changes = {k: v for k, v in changes.items() if current.get(k) != v}
    if not changes:
        return MessageOut(message="No changes", data={})

    structural = REQUIRES_FLAT & changes.keys()
    if structural:
        open_count = len(await portfolio.open_positions(db))
        if open_count:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Close the {open_count} open position(s) before changing "
                    f"{', '.join(sorted(structural))} — open trades would then be managed "
                    "under rules they were not entered under."
                ),
            )

    for key, value in changes.items():
        setattr(settings, key, value)
    bot.config_overrides = {**(bot.config_overrides or {}), **changes}

    if "futures_enabled" in changes:
        # Spot and futures are different ccxt clients.
        await exchange.reset()

    await audit(db, user, "config_update", str(changes))
    await db.commit()
    return MessageOut(message=f"Updated {len(changes)} setting(s)", data=changes)


@router.post("/config/reset", response_model=MessageOut)
async def reset_config(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Drop every override and fall back to the deployed environment values."""
    bot = await portfolio.get_bot(db)
    removed = bot.config_overrides or {}
    bot.config_overrides = {}
    reloaded = reload_settings()
    reloaded.trading_mode = bot.mode
    await audit(db, user, "config_reset", str(removed))
    await db.commit()
    return MessageOut(
        message="Configuration reset to the deployed environment values",
        data={"cleared": list(removed)},
    )


@router.post("/risk-config", response_model=MessageOut)
async def update_risk(
    payload: ConfigIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Retained for older clients; /config supersedes it."""
    return await update_config(payload, db, user)


@router.get("/risk-config")
async def read_risk(_: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    values = _current_config()
    return {**values, "score_weights": settings.score_weights}
