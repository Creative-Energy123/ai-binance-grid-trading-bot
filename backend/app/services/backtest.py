"""Event-driven backtester.

Walks the base (entry) timeframe one candle at a time and rebuilds the higher
timeframes from candles that had already *closed* at that moment, so a signal can
never see data from its own future. Fills are simulated pessimistically:

* entries pay slippage and the taker fee,
* when a candle touches both the stop and a target, the stop is assumed first,
* exits pay slippage and the taker fee again,
* futures runs accrue a funding charge every 8 hours.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import Settings, get_settings
from app.services.contracts import MarketContext, Side
from app.services.exchange import exchange
from app.services.indicators import Candle, compute_snapshot
from app.services.regime import detect_regime
from app.services.risk import RiskState, evaluate_limits
from app.services.signals import evaluate
from app.services.sizing import SizingError, size_position

logger = logging.getLogger(__name__)

TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
FUNDING_INTERVAL_SECONDS = 8 * 3600
FUNDING_RATE_PCT = 0.01  # typical 8h funding on a major pair


def aggregate(candles: list[Candle], base_tf: str, target_tf: str) -> list[Candle]:
    """Roll base candles up into `target_tf` buckets. Only complete buckets are
    returned, so nothing here can leak a partially formed candle."""
    base_s = TIMEFRAME_SECONDS[base_tf]
    target_s = TIMEFRAME_SECONDS[target_tf]
    if target_s == base_s:
        return list(candles)
    if target_s % base_s:
        raise ValueError(f"{target_tf} is not a multiple of {base_tf}")
    per_bucket = target_s // base_s

    out: list[Candle] = []
    bucket: list[Candle] = []
    for candle in candles:
        bucket_start = (int(candle[0]) // 1000 // target_s) * target_s * 1000
        if bucket and int(bucket[0][0]) // 1000 // target_s != bucket_start // 1000 // target_s:
            if len(bucket) == per_bucket:
                out.append(_merge(bucket))
            bucket = []
        bucket.append(candle)
    if len(bucket) == per_bucket:
        out.append(_merge(bucket))
    return out


def _merge(bucket: list[Candle]) -> Candle:
    return [
        bucket[0][0],
        float(bucket[0][1]),
        max(float(c[2]) for c in bucket),
        min(float(c[3]) for c in bucket),
        float(bucket[-1][4]),
        sum(float(c[5]) for c in bucket),
    ]


@dataclass
class BacktestTradeResult:
    symbol: str
    strategy: str
    side: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    quantity: float
    fees: float
    slippage: float
    funding: float
    pnl: float
    r_multiple: float
    regime: str
    signal_score: float
    exit_reason: str


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    candles: int
    starting_equity: float
    ending_equity: float
    trades: list[BacktestTradeResult] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    drawdown_curve: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _OpenTrade:
    side: Side
    strategy: str
    regime: str
    score: float
    entry_price: float
    quantity: float
    stop: float
    tp1: float
    tp2: float
    risk_amount: float
    entry_time: int
    fees: float
    slippage: float
    funding: float = 0.0
    tp1_done: bool = False
    realized: float = 0.0
    last_funding_time: int = 0


def _metrics(result: BacktestResult, settings: Settings) -> dict:
    trades = result.trades
    if not trades:
        return {
            "total_trades": 0,
            "note": "No trades were taken. With a regime-aware bot this is a normal outcome.",
        }

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    net = sum(t.pnl for t in trades)
    fees = sum(t.fees for t in trades)
    slippage = sum(t.slippage for t in trades)
    funding = sum(t.funding for t in trades)

    peak = result.starting_equity
    max_dd = 0.0
    for point in result.equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = max(max_dd, 100 * (peak - point["equity"]) / peak)

    returns = [t.pnl / result.starting_equity for t in trades] if result.starting_equity else []
    sharpe = 0.0
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        std = (sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
        sharpe = (mean / std) * (len(returns) ** 0.5) if std else 0.0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(trades), 2),
        "net_pnl": round(net, 4),
        "net_pnl_pct": round(100 * net / result.starting_equity, 4) if result.starting_equity else 0,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": (
            round(gross_profit / gross_loss, 3) if gross_loss else (999.0 if gross_profit else 0.0)
        ),
        "max_drawdown_pct": round(max_dd, 3),
        "sharpe": round(sharpe, 3),
        "average_trade": round(net / len(trades), 4),
        "average_win": round(gross_profit / len(wins), 4) if wins else 0.0,
        "average_loss": round(-gross_loss / len(losses), 4) if losses else 0.0,
        "expectancy_r": round(sum(t.r_multiple for t in trades) / len(trades), 3),
        "fees": round(fees, 4),
        "slippage_cost": round(slippage, 4),
        "funding_fees": round(funding, 4),
        "costs_as_pct_of_gross": (
            round(100 * (fees + slippage + funding) / gross_profit, 2) if gross_profit else None
        ),
        "assumptions": {
            "taker_fee_pct": settings.taker_fee_pct,
            "slippage_pct": settings.slippage_pct,
            "stop_before_target_in_same_candle": True,
            "funding_rate_pct_per_8h": FUNDING_RATE_PCT if settings.futures_enabled else 0.0,
        },
    }


async def fetch_history(symbol: str, timeframe: str, limit: int) -> list[Candle]:
    """Fetch history in pages (Binance caps a single call at 1000 candles)."""
    collected: list[Candle] = []
    remaining = limit
    while remaining > 0:
        page = await exchange.fetch_ohlcv(symbol, timeframe, min(remaining, 1000))
        if not page:
            break
        if collected and page[0][0] >= collected[0][0]:
            break
        collected = page + collected if collected else page
        remaining -= len(page)
        if len(page) < 1000:
            break
    return collected


def run_backtest(
    symbol: str,
    base_candles: list[Candle],
    settings: Settings | None = None,
    starting_equity: float | None = None,
) -> BacktestResult:
    settings = settings or get_settings()
    base_tf = settings.timeframe_entry
    setup_tf = settings.timeframe_setup
    primary_tf = settings.timeframe_primary
    equity = starting_equity or settings.starting_paper_equity_usdt

    result = BacktestResult(
        symbol=symbol,
        timeframe=base_tf,
        candles=len(base_candles),
        starting_equity=equity,
        ending_equity=equity,
    )
    if len(base_candles) < 400:
        result.warnings.append(
            "Fewer than 400 base candles: results are not statistically meaningful."
        )

    setup_all = aggregate(base_candles, base_tf, setup_tf)
    primary_all = aggregate(base_candles, base_tf, primary_tf)
    setup_s = TIMEFRAME_SECONDS[setup_tf] * 1000
    primary_s = TIMEFRAME_SECONDS[primary_tf] * 1000

    setup_cursor = 0
    primary_cursor = 0
    open_trade: _OpenTrade | None = None
    peak = equity
    consecutive_losses = 0
    day_pnl: dict[str, float] = {}
    trades_this_hour: list[int] = []

    warmup = 220
    for i in range(warmup, len(base_candles)):
        candle = base_candles[i]
        now_ms = int(candle[0])
        price = float(candle[4])
        high, low = float(candle[2]), float(candle[3])
        day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).strftime("%Y-%m-%d")

        # Advance the higher-timeframe cursors to candles that have already closed.
        while setup_cursor < len(setup_all) and int(setup_all[setup_cursor][0]) + setup_s <= now_ms:
            setup_cursor += 1
        while (
            primary_cursor < len(primary_all)
            and int(primary_all[primary_cursor][0]) + primary_s <= now_ms
        ):
            primary_cursor += 1

        # ---------------------------------------------------- manage the position
        if open_trade is not None:
            long = open_trade.side is Side.LONG

            if settings.futures_enabled:
                elapsed = now_ms - open_trade.last_funding_time
                if elapsed >= FUNDING_INTERVAL_SECONDS * 1000:
                    charge = open_trade.quantity * price * FUNDING_RATE_PCT / 100
                    open_trade.funding += charge
                    open_trade.last_funding_time = now_ms

            stop_hit = low <= open_trade.stop if long else high >= open_trade.stop
            tp2_hit = high >= open_trade.tp2 if long else low <= open_trade.tp2
            tp1_hit = high >= open_trade.tp1 if long else low <= open_trade.tp1

            exit_price: float | None = None
            reason = ""
            if stop_hit:  # pessimistic: the stop is assumed to fill first
                exit_price, reason = open_trade.stop, "stop_loss"
            elif tp2_hit:
                exit_price, reason = open_trade.tp2, "take_profit_2"
            elif tp1_hit and not open_trade.tp1_done:
                partial_qty = open_trade.quantity * settings.tp1_close_fraction
                pnl, fee, slip = _close_leg(open_trade, open_trade.tp1, partial_qty, settings)
                open_trade.realized += pnl
                open_trade.fees += fee
                open_trade.slippage += slip
                open_trade.quantity -= partial_qty
                open_trade.tp1_done = True
                if settings.move_stop_to_breakeven_after_tp1:
                    open_trade.stop = open_trade.entry_price
                equity += pnl
            elif open_trade.tp1_done and settings.trailing_enabled:
                snap_atr = _atr_at(base_candles, i)
                if snap_atr:
                    trailed = (
                        price - snap_atr * settings.trailing_atr_multiplier
                        if long
                        else price + snap_atr * settings.trailing_atr_multiplier
                    )
                    if (trailed > open_trade.stop) if long else (trailed < open_trade.stop):
                        open_trade.stop = trailed

            if exit_price is not None:
                pnl, fee, slip = _close_leg(open_trade, exit_price, open_trade.quantity, settings)
                total_pnl = open_trade.realized + pnl - open_trade.funding
                equity += pnl - open_trade.funding
                result.trades.append(
                    BacktestTradeResult(
                        symbol=symbol,
                        strategy=open_trade.strategy,
                        side=open_trade.side.value,
                        entry_time=open_trade.entry_time,
                        exit_time=now_ms,
                        entry_price=open_trade.entry_price,
                        exit_price=exit_price,
                        quantity=open_trade.quantity,
                        fees=open_trade.fees + fee,
                        slippage=open_trade.slippage + slip,
                        funding=open_trade.funding,
                        pnl=total_pnl,
                        r_multiple=total_pnl / open_trade.risk_amount if open_trade.risk_amount else 0.0,
                        regime=open_trade.regime,
                        signal_score=open_trade.score,
                        exit_reason=reason,
                    )
                )
                day_pnl[day] = day_pnl.get(day, 0.0) + total_pnl
                consecutive_losses = consecutive_losses + 1 if total_pnl < 0 else 0
                trades_this_hour.append(now_ms)
                open_trade = None

        peak = max(peak, equity)
        result.equity_curve.append({"t": now_ms, "equity": round(equity, 4)})
        result.drawdown_curve.append(
            {"t": now_ms, "dd_pct": round(100 * (peak - equity) / peak, 4) if peak else 0.0}
        )

        # ------------------------------------------------------- look for entries
        if open_trade is not None or setup_cursor < 60 or primary_cursor < 60:
            continue

        setup_snap = compute_snapshot(setup_tf, setup_all[:setup_cursor], settings.structure_lookback)
        primary_snap = compute_snapshot(
            primary_tf, primary_all[:primary_cursor], settings.structure_lookback
        )
        entry_snap = compute_snapshot(base_tf, base_candles[:i], settings.structure_lookback)
        regime = detect_regime(primary=primary_snap, secondary=setup_snap)
        ctx = MarketContext(
            symbol=symbol,
            price=price,
            primary=primary_snap,
            setup=setup_snap,
            entry=entry_snap,
            regime=regime,
            as_of=datetime.fromtimestamp(now_ms / 1000, timezone.utc),
        )
        evaluation = evaluate(ctx, settings)
        if evaluation.signal is None:
            continue

        trades_this_hour = [t for t in trades_this_hour if now_ms - t <= 3600_000]
        state = RiskState(
            equity=equity,
            open_positions=0,
            symbol_exposure=0.0,
            total_exposure=0.0,
            daily_pnl=day_pnl.get(day, 0.0),
            weekly_pnl=sum(day_pnl.values()),
            consecutive_losses=consecutive_losses,
            trades_last_hour=len(trades_this_hour),
            cooldown_until=None,
        )
        if not evaluate_limits(evaluation.signal, state, settings).approved:
            continue

        try:
            sized = size_position(evaluation.signal, equity, settings)
        except SizingError:
            continue

        fill_price = price * (
            1 + settings.slippage_pct / 100
            if evaluation.signal.side is Side.LONG
            else 1 - settings.slippage_pct / 100
        )
        entry_fee = fill_price * sized.quantity * settings.taker_fee_pct / 100
        equity -= entry_fee
        open_trade = _OpenTrade(
            side=evaluation.signal.side,
            strategy=evaluation.signal.strategy,
            regime=evaluation.signal.regime,
            score=evaluation.signal.score,
            entry_price=fill_price,
            quantity=sized.quantity,
            stop=sized.stop_price,
            tp1=sized.tp1_price,
            tp2=sized.tp2_price,
            risk_amount=sized.risk_amount,
            entry_time=now_ms,
            fees=entry_fee,
            slippage=abs(fill_price - price) * sized.quantity,
            last_funding_time=now_ms,
        )

    result.ending_equity = equity
    result.metrics = _metrics(result, settings)
    return result


def _close_leg(
    trade: _OpenTrade, target_price: float, quantity: float, settings: Settings
) -> tuple[float, float, float]:
    """P&L, fee and slippage cost for closing `quantity` at `target_price`."""
    long = trade.side is Side.LONG
    fill = target_price * (
        1 - settings.slippage_pct / 100 if long else 1 + settings.slippage_pct / 100
    )
    direction = 1 if long else -1
    gross = (fill - trade.entry_price) * quantity * direction
    fee = fill * quantity * settings.taker_fee_pct / 100
    slippage = abs(fill - target_price) * quantity
    return gross - fee, fee, slippage


def _atr_at(candles: list[Candle], index: int, period: int = 14) -> float | None:
    from app.services.indicators import atr as atr_series

    window = candles[max(0, index - period * 3) : index]
    if len(window) < period + 1:
        return None
    return atr_series(window, period)[-1]
