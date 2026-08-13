from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import Actor, AiAnalysis, AuditLog, BotState, GridConfig, GridLevel, Trade
from app.services.alerts import format_alert, send_alert
from app.services.anthropic_client import analyze_market
from app.services.backtest import build_grid_prices
from app.services.binance_client import binance_client

logger = logging.getLogger(__name__)
settings = get_settings()


async def audit(db: AsyncSession, actor: str, action: str, detail: str = "") -> None:
    db.add(AuditLog(actor=actor, action=action, detail=detail))
    await db.commit()


async def get_or_create_state(db: AsyncSession) -> BotState:
    result = await db.execute(select(BotState).where(BotState.id == 1))
    state = result.scalar_one_or_none()
    if state is None:
        state = BotState(id=1)
        db.add(state)
        await db.commit()
        await db.refresh(state)
    return state


async def get_active_config(db: AsyncSession) -> GridConfig | None:
    result = await db.execute(
        select(GridConfig).where(GridConfig.is_active.is_(True)).order_by(GridConfig.id.desc())
    )
    return result.scalars().first()


async def kill_switch(db: AsyncSession, actor: str = Actor.USER.value) -> BotState:
    state = await get_or_create_state(db)
    config = await get_active_config(db)
    symbol = config.symbol if config else settings.default_symbol
    await binance_client.cancel_all(symbol)
    levels = (
        await db.execute(select(GridLevel).where(GridLevel.status == "open"))
    ).scalars().all()
    for lvl in levels:
        lvl.status = "cancelled"
        lvl.order_id = None
    state.running = False
    state.killed = True
    state.status_message = "killed — all open orders cancelled"
    await db.commit()
    await audit(db, actor, "kill_switch", f"Cancelled orders for {symbol}")
    await send_alert(format_alert("KILL SWITCH", f"Trading halted for {symbol}"))
    await db.refresh(state)
    return state


async def start_bot(db: AsyncSession) -> BotState:
    state = await get_or_create_state(db)
    if state.killed:
        raise RuntimeError("Bot is killed. Clear kill switch before starting.")
    config = await get_active_config(db)
    if config is None:
        raise RuntimeError("No active grid configuration.")
    if config.capital_usdt > settings.max_capital_usdt:
        raise RuntimeError(
            f"Capital {config.capital_usdt} exceeds hard cap {settings.max_capital_usdt}"
        )
    if not settings.binance_testnet and not state.live_confirmed:
        raise RuntimeError("Live trading requires explicit confirmation.")

    await _rebuild_levels(db, config)
    state.running = True
    state.status_message = "running"
    await db.commit()
    await audit(db, Actor.USER.value, "bot_start", f"symbol={config.symbol}")
    await db.refresh(state)
    return state


async def clear_kill(db: AsyncSession) -> BotState:
    state = await get_or_create_state(db)
    state.killed = False
    state.status_message = "idle"
    await db.commit()
    await audit(db, Actor.USER.value, "clear_kill", "")
    await db.refresh(state)
    return state


async def confirm_live(db: AsyncSession, phrase: str) -> BotState:
    if phrase != "ENABLE LIVE TRADING":
        raise RuntimeError('Confirmation phrase must be exactly: ENABLE LIVE TRADING')
    if settings.binance_testnet:
        raise RuntimeError("Set BINANCE_TESTNET=false before confirming live.")
    state = await get_or_create_state(db)
    state.live_confirmed = True
    await db.commit()
    await audit(db, Actor.USER.value, "live_confirm", "Live trading enabled")
    await send_alert(format_alert("LIVE TRADING ENABLED", "Proceed with extreme caution."))
    await db.refresh(state)
    return state


async def _rebuild_levels(db: AsyncSession, config: GridConfig) -> None:
    existing = (
        await db.execute(select(GridLevel).where(GridLevel.config_id == config.id))
    ).scalars().all()
    for row in existing:
        await db.delete(row)
    await db.flush()

    prices = build_grid_prices(
        config.lower_price, config.upper_price, config.grid_count, config.grid_type
    )
    mid = (config.lower_price + config.upper_price) / 2
    qty_budget = config.capital_usdt / config.grid_count
    for i, price in enumerate(prices):
        side = "buy" if price <= mid else "sell"
        qty = qty_budget / price if price else 0
        order = await binance_client.create_limit_order(config.symbol, side, qty, price)
        db.add(
            GridLevel(
                config_id=config.id,
                level_index=i,
                price=price,
                side=side,
                quantity=qty,
                order_id=str(order.get("id")),
                status="open" if order.get("status") in ("open", "new", None) or order.get("simulated") else str(order.get("status")),
            )
        )
    await db.commit()


async def tick() -> None:
    """Periodic price poll + risk checks. Invoked by APScheduler."""
    async with SessionLocal() as db:
        state = await get_or_create_state(db)
        if not state.running or state.killed:
            return
        config = await get_active_config(db)
        if config is None:
            return

        try:
            price = await binance_client.fetch_ticker_price(config.symbol)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Price poll failed: %s", exc)
            await send_alert(format_alert("Exchange API failure", str(exc)))
            await audit(db, Actor.SYSTEM.value, "price_poll_error", str(exc))
            return

        state.last_price = price
        today = date.today().isoformat()
        if state.daily_pnl_date != today:
            state.daily_pnl = 0.0
            state.daily_pnl_date = today

        equity = config.capital_usdt + state.realized_pnl + state.unrealized_pnl
        state.peak_equity = max(state.peak_equity or equity, equity)
        drawdown_pct = (
            ((state.peak_equity - equity) / state.peak_equity) * 100 if state.peak_equity else 0
        )

        # Stop-loss / max drawdown circuit breaker
        if drawdown_pct >= config.max_drawdown_pct:
            state.status_message = f"halted: max drawdown {drawdown_pct:.2f}%"
            await db.commit()
            await kill_switch(db, Actor.SYSTEM.value)
            await send_alert(format_alert("Max drawdown breach", state.status_message))
            return

        if state.daily_pnl <= -abs(config.daily_loss_limit_usdt):
            state.status_message = "halted: daily loss limit"
            await db.commit()
            await kill_switch(db, Actor.SYSTEM.value)
            await send_alert(format_alert("Daily loss limit", state.status_message))
            return

        # Naive fill detection: if price crosses a buy/sell level, record trade (sim/testnet)
        levels = (
            await db.execute(
                select(GridLevel).where(
                    GridLevel.config_id == config.id, GridLevel.status == "open"
                )
            )
        ).scalars().all()
        for lvl in levels:
            filled = False
            if lvl.side == "buy" and price <= lvl.price:
                filled = True
            if lvl.side == "sell" and price >= lvl.price:
                filled = True
            if not filled:
                continue
            pnl = 0.0
            if lvl.side == "sell":
                pnl = (lvl.price - ((config.lower_price + config.upper_price) / 2)) * lvl.quantity
            trade = Trade(
                symbol=config.symbol,
                side=lvl.side,
                price=lvl.price,
                quantity=lvl.quantity,
                pnl=pnl,
                exchange_order_id=lvl.order_id,
            )
            db.add(trade)
            lvl.status = "filled"
            state.realized_pnl += pnl
            state.daily_pnl += pnl
            await audit(
                db,
                Actor.SYSTEM.value,
                "order_filled",
                f"{lvl.side} {lvl.quantity:.6f} @ {lvl.price}",
            )

        state.unrealized_pnl = 0.0  # simplified; dashboard still shows realized
        state.updated_at = datetime.now(timezone.utc)
        await db.commit()


async def run_ai_analysis() -> None:
    async with SessionLocal() as db:
        config = await get_active_config(db)
        if config is None:
            return
        state = await get_or_create_state(db)
        try:
            ohlcv = await binance_client.fetch_ohlcv(config.symbol, "1h", 100)
        except Exception as exc:  # noqa: BLE001
            logger.exception("OHLCV fetch failed: %s", exc)
            return

        data = await analyze_market(
            config.symbol,
            ohlcv,
            config.lower_price,
            config.upper_price,
            config.grid_count,
            state.realized_pnl,
        )
        row = AiAnalysis(
            trend=str(data.get("trend", "")),
            volatility_regime=str(data.get("volatility_regime", "")),
            suggested_lower=data.get("suggested_lower"),
            suggested_upper=data.get("suggested_upper"),
            suggested_grid_count=data.get("suggested_grid_count"),
            risk_warnings=str(data.get("risk_warnings", "")),
            raw_json=json.dumps(data),
        )
        db.add(row)
        await db.commit()
        await audit(db, Actor.AI.value, "ai_analysis", row.trend)

        if data.get("risk_warnings"):
            await send_alert(format_alert("AI risk flag", str(data.get("risk_warnings"))))

        if settings.ai_auto_apply and data.get("suggested_lower") and data.get("suggested_upper"):
            # Still require that auto-apply is opted in; log as AI-suggested apply
            config.lower_price = float(data["suggested_lower"])
            config.upper_price = float(data["suggested_upper"])
            if data.get("suggested_grid_count"):
                config.grid_count = int(data["suggested_grid_count"])
            row.approved = True
            row.applied = True
            await db.commit()
            await audit(db, Actor.AI.value, "ai_auto_apply", row.raw_json[:500])


async def apply_ai_suggestion(db: AsyncSession, analysis_id: int) -> AiAnalysis:
    result = await db.execute(select(AiAnalysis).where(AiAnalysis.id == analysis_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise RuntimeError("Analysis not found")
    config = await get_active_config(db)
    if config is None:
        raise RuntimeError("No active config")
    if row.suggested_lower is not None:
        config.lower_price = row.suggested_lower
    if row.suggested_upper is not None:
        config.upper_price = row.suggested_upper
    if row.suggested_grid_count is not None:
        config.grid_count = row.suggested_grid_count
    row.approved = True
    row.applied = True
    await db.commit()
    await audit(db, Actor.USER.value, "ai_apply", f"analysis_id={analysis_id}")
    await db.refresh(row)
    return row
