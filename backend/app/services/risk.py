"""RiskEngine — the only component allowed to veto a signal.

Every check is evaluated (not short-circuited) so the dashboard and the AI
assistant can explain exactly which limit blocked a trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import BotInstance, DailyStatistic, Position, PositionStatus, RiskEvent, Trade
from app.services.contracts import RiskDecision, Side, TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    equity: float
    open_positions: int
    symbol_exposure: float
    total_exposure: float
    daily_pnl: float
    weekly_pnl: float
    consecutive_losses: int
    trades_last_hour: int
    cooldown_until: datetime | None


def _utc_day(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


async def gather_state(db: AsyncSession, bot: BotInstance, symbol: str) -> RiskState:
    now = datetime.now(timezone.utc)

    open_rows = (
        await db.execute(select(Position).where(Position.status == PositionStatus.OPEN.value))
    ).scalars().all()
    symbol_exposure = sum(
        p.remaining_quantity * p.entry_price for p in open_rows if p.symbol == symbol
    )
    total_exposure = sum(p.remaining_quantity * p.entry_price for p in open_rows)

    daily_pnl = (
        await db.execute(
            select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(
                Trade.closed_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)
            )
        )
    ).scalar_one()
    weekly_pnl = (
        await db.execute(
            select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(
                Trade.closed_at >= now - timedelta(days=7)
            )
        )
    ).scalar_one()
    trades_last_hour = (
        await db.execute(
            select(func.count(Trade.id)).where(Trade.closed_at >= now - timedelta(hours=1))
        )
    ).scalar_one()

    return RiskState(
        equity=bot.equity or 0.0,
        open_positions=len(open_rows),
        symbol_exposure=symbol_exposure,
        total_exposure=total_exposure,
        daily_pnl=float(daily_pnl),
        weekly_pnl=float(weekly_pnl),
        consecutive_losses=bot.consecutive_losses,
        trades_last_hour=int(trades_last_hour),
        cooldown_until=bot.cooldown_until,
    )


def evaluate_limits(
    signal: TradeSignal, state: RiskState, settings: Settings | None = None
) -> RiskDecision:
    """Pure risk evaluation, so the same rules run in live trading and backtests."""
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    equity = state.equity
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def check(name: str, passed: bool, message: str) -> None:
        checks[name] = passed
        if not passed:
            failures.append(message)

    check("equity_known", equity > 0, "Account equity is zero or unknown")

    check(
        "max_positions",
        state.open_positions < settings.max_positions,
        f"Already holding {state.open_positions} of {settings.max_positions} allowed positions",
    )

    daily_loss_limit = -abs(equity * settings.max_daily_loss_pct / 100)
    check(
        "daily_loss",
        state.daily_pnl > daily_loss_limit,
        f"Daily P&L {state.daily_pnl:.2f} hit the {settings.max_daily_loss_pct}% daily loss limit",
    )

    weekly_loss_limit = -abs(equity * settings.max_weekly_loss_pct / 100)
    check(
        "weekly_loss",
        state.weekly_pnl > weekly_loss_limit,
        f"Weekly P&L {state.weekly_pnl:.2f} hit the {settings.max_weekly_loss_pct}% weekly loss limit",
    )

    check(
        "consecutive_losses",
        state.consecutive_losses < settings.max_consecutive_losses,
        f"{state.consecutive_losses} consecutive losses reached the limit of {settings.max_consecutive_losses}",
    )

    check(
        "trade_rate",
        state.trades_last_hour < settings.max_trades_per_hour,
        f"{state.trades_last_hour} trades in the last hour reached the cap of {settings.max_trades_per_hour}",
    )

    cooldown_until = state.cooldown_until
    if cooldown_until is not None and cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    in_cooldown = bool(cooldown_until and cooldown_until > now)
    check(
        "cooldown",
        not in_cooldown,
        f"Post-loss cooldown active until {cooldown_until.isoformat()}" if in_cooldown else "",
    )

    risk_amount = equity * settings.risk_per_trade_pct / 100
    stop_distance_pct = signal.stop_distance_pct
    notional = (risk_amount / (stop_distance_pct / 100)) if stop_distance_pct > 0 else 0.0
    if settings.futures_enabled:
        notional = min(notional, equity * settings.futures_leverage)

    max_symbol_exposure = equity * settings.max_symbol_exposure_pct / 100
    check(
        "symbol_exposure",
        state.symbol_exposure + notional <= max_symbol_exposure,
        (
            f"{signal.symbol} exposure {state.symbol_exposure + notional:.2f} would exceed the "
            f"{settings.max_symbol_exposure_pct}% per-symbol cap ({max_symbol_exposure:.2f})"
        ),
    )

    max_total_exposure = equity * settings.max_total_exposure_pct / 100
    check(
        "total_exposure",
        state.total_exposure + notional <= max_total_exposure,
        (
            f"Total exposure {state.total_exposure + notional:.2f} would exceed the "
            f"{settings.max_total_exposure_pct}% cap ({max_total_exposure:.2f})"
        ),
    )

    check(
        "stop_present",
        signal.stop_price > 0 and signal.stop_distance > 0,
        "Signal has no usable protective stop",
    )

    # A stop wider than the per-trade risk budget allows would force an
    # unacceptably small (or dust) position.
    check(
        "stop_sanity",
        0 < stop_distance_pct <= 5.0,
        f"Stop distance {stop_distance_pct:.2f}% is outside the sane 0–5% scalping band",
    )

    if signal.side is Side.SHORT:
        check(
            "short_allowed",
            settings.futures_enabled,
            "Short signals require futures; spot markets cannot be shorted",
        )

    approved = all(checks.values())
    return RiskDecision(
        approved=approved,
        reason="approved" if approved else "; ".join(f for f in failures if f),
        checks=checks,
        details={
            "equity": equity,
            "risk_amount": risk_amount,
            "planned_notional": notional,
            "daily_pnl": state.daily_pnl,
            "weekly_pnl": state.weekly_pnl,
            "open_positions": float(state.open_positions),
            "consecutive_losses": float(state.consecutive_losses),
        },
    )


async def approve(db: AsyncSession, bot: BotInstance, signal: TradeSignal) -> RiskDecision:
    settings = get_settings()
    state = await gather_state(db, bot, signal.symbol)
    decision = evaluate_limits(signal, state, settings)
    if not decision.approved:
        db.add(
            RiskEvent(
                event_type="signal_rejected",
                severity="info",
                symbol=signal.symbol,
                detail=decision.reason,
                context={"checks": decision.checks, "details": decision.details},
            )
        )
        await db.flush()
    return decision


async def register_trade_result(db: AsyncSession, bot: BotInstance, pnl: float) -> None:
    """Update loss streak, cooldown and the daily statistics row after a close."""
    settings = get_settings()
    now = datetime.now(timezone.utc)

    if pnl < 0:
        bot.consecutive_losses += 1
        bot.cooldown_until = now + timedelta(minutes=settings.loss_cooldown_minutes)
    else:
        bot.consecutive_losses = 0

    day = _utc_day(now)
    row = (
        await db.execute(select(DailyStatistic).where(DailyStatistic.day == day))
    ).scalar_one_or_none()
    if row is None:
        # Column defaults only apply at INSERT time, so the counters are set
        # explicitly here — this row is mutated before it is ever flushed.
        row = DailyStatistic(
            day=day,
            mode=bot.mode,
            starting_equity=bot.equity,
            realized_pnl=0.0,
            fees=0.0,
            trades=0,
            wins=0,
            losses=0,
            max_drawdown_pct=0.0,
            halted=False,
        )
        db.add(row)
    row.realized_pnl += pnl
    row.trades += 1
    if pnl >= 0:
        row.wins += 1
    else:
        row.losses += 1

    if bot.consecutive_losses >= settings.max_consecutive_losses:
        bot.paused = True
        bot.status_message = (
            f"Paused: {bot.consecutive_losses} consecutive losses reached the configured limit"
        )
        db.add(
            RiskEvent(
                event_type="consecutive_loss_halt",
                severity="critical",
                detail=bot.status_message,
                context={"consecutive_losses": bot.consecutive_losses},
            )
        )

    loss_limit = -abs(bot.equity * settings.max_daily_loss_pct / 100)
    if row.realized_pnl <= loss_limit:
        row.halted = True
        bot.paused = True
        bot.status_message = f"Paused: daily loss limit ({settings.max_daily_loss_pct}%) reached"
        db.add(
            RiskEvent(
                event_type="daily_loss_limit",
                severity="critical",
                detail=bot.status_message,
                context={"realized_pnl": row.realized_pnl, "limit": loss_limit},
            )
        )
    await db.flush()
