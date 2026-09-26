"""PortfolioManager — equity tracking, snapshots and performance statistics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BotInstance, PortfolioSnapshot, Position, PositionStatus, Trade
from app.services.contracts import Side
from app.services.exchange import exchange

logger = logging.getLogger(__name__)


async def get_bot(db: AsyncSession) -> BotInstance:
    bot = (await db.execute(select(BotInstance).where(BotInstance.id == 1))).scalar_one_or_none()
    if bot is None:
        settings = get_settings()
        bot = BotInstance(
            id=1,
            mode=settings.trading_mode,
            equity=settings.starting_paper_equity_usdt,
            peak_equity=settings.starting_paper_equity_usdt,
        )
        db.add(bot)
        await db.flush()
    return bot


async def open_positions(db: AsyncSession) -> list[Position]:
    return list(
        (
            await db.execute(
                select(Position).where(Position.status == PositionStatus.OPEN.value)
            )
        ).scalars().all()
    )


def unrealized(position: Position, price: float) -> float:
    direction = 1 if Side(position.side) is Side.LONG else -1
    return (price - position.entry_price) * position.remaining_quantity * direction


async def refresh_equity(db: AsyncSession, bot: BotInstance, prices: dict[str, float]) -> float:
    """Equity = exchange balance (real modes) or simulated balance, plus open P&L."""
    settings = get_settings()

    if settings.places_real_orders and exchange.has_credentials():
        try:
            bot.equity = await exchange.fetch_equity_usdt()
        except RuntimeError as exc:
            logger.error("Balance refresh failed: %s", exc)
            raise

    positions = await open_positions(db)
    total_unrealized = 0.0
    for position in positions:
        price = prices.get(position.symbol)
        if price is None:
            continue
        position.unrealized_pnl = unrealized(position, price)
        total_unrealized += position.unrealized_pnl

    bot.peak_equity = max(bot.peak_equity, bot.equity + total_unrealized)
    await db.flush()
    return bot.equity + total_unrealized


async def take_snapshot(db: AsyncSession, bot: BotInstance, prices: dict[str, float]) -> PortfolioSnapshot:
    positions = await open_positions(db)
    total_unrealized = sum(unrealized(p, prices.get(p.symbol, p.entry_price)) for p in positions)
    exposure = sum(p.remaining_quantity * p.entry_price for p in positions)
    snapshot = PortfolioSnapshot(
        mode=bot.mode,
        equity=bot.equity + total_unrealized,
        balance=bot.equity,
        available=max(bot.equity - sum(p.remaining_quantity * p.entry_price / max(p.leverage, 1) for p in positions), 0.0),
        unrealized_pnl=total_unrealized,
        open_positions=len(positions),
        exposure=exposure,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


@dataclass
class Performance:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    net_pnl: float = 0.0
    fees: float = 0.0
    average_trade: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    expectancy_r: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    today_pnl: float = 0.0
    week_pnl: float = 0.0
    month_pnl: float = 0.0
    equity_curve: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def summarise(trades: list[Trade], starting_equity: float) -> Performance:
    perf = Performance()
    if not trades:
        return perf

    ordered = sorted(trades, key=lambda t: t.closed_at or datetime.now(timezone.utc))
    equity = starting_equity
    peak = starting_equity or 1.0
    max_dd = 0.0
    returns: list[float] = []
    r_multiples: list[float] = []

    for trade in ordered:
        perf.trades += 1
        perf.net_pnl += trade.pnl
        perf.fees += trade.fees
        if trade.pnl >= 0:
            perf.wins += 1
            perf.gross_profit += trade.pnl
        else:
            perf.losses += 1
            perf.gross_loss += abs(trade.pnl)
        r_multiples.append(trade.r_multiple)

        base = equity or 1.0
        equity += trade.pnl
        returns.append(trade.pnl / base)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, 100 * (peak - equity) / peak)
        perf.equity_curve.append(
            {
                "t": (trade.closed_at or datetime.now(timezone.utc)).isoformat(),
                "equity": round(equity, 4),
                "pnl": round(trade.pnl, 4),
            }
        )

    perf.win_rate = 100 * perf.wins / perf.trades
    if perf.gross_loss > 0:
        perf.profit_factor = perf.gross_profit / perf.gross_loss
    else:
        # No losing trades yet: report a capped value rather than infinity, which
        # is not JSON-serialisable and reads as a guarantee it is not.
        perf.profit_factor = 999.0 if perf.gross_profit > 0 else 0.0
    perf.average_trade = perf.net_pnl / perf.trades
    perf.average_win = perf.gross_profit / perf.wins if perf.wins else 0.0
    perf.average_loss = -perf.gross_loss / perf.losses if perf.losses else 0.0
    perf.expectancy_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
    perf.max_drawdown_pct = max_dd

    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = variance**0.5
        # Per-trade Sharpe annualised on the observed trade frequency is noisy for
        # small samples; this is the plain per-trade ratio scaled by sqrt(N).
        perf.sharpe = (mean / std) * (len(returns) ** 0.5) if std > 0 else 0.0

    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    perf.today_pnl = sum(t.pnl for t in ordered if (t.closed_at or now) >= start_of_day)
    perf.week_pnl = sum(t.pnl for t in ordered if (t.closed_at or now) >= now - timedelta(days=7))
    perf.month_pnl = sum(t.pnl for t in ordered if (t.closed_at or now) >= now - timedelta(days=30))
    return perf


async def performance(db: AsyncSession, mode: str | None = None, limit: int = 1000) -> Performance:
    query = select(Trade).order_by(Trade.closed_at.desc()).limit(limit)
    if mode:
        query = query.where(Trade.mode == mode)
    trades = list((await db.execute(query)).scalars().all())
    settings = get_settings()
    bot = await get_bot(db)
    realized = sum(t.pnl for t in trades)
    starting = (bot.equity - realized) or settings.starting_paper_equity_usdt
    return summarise(trades, starting)


async def trade_count_today(db: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        (await db.execute(select(func.count(Trade.id)).where(Trade.closed_at >= start))).scalar_one()
    )
