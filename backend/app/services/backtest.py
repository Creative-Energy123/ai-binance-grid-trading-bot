from __future__ import annotations

from dataclasses import dataclass


def build_grid_prices(
    lower: float, upper: float, count: int, grid_type: str = "arithmetic"
) -> list[float]:
    if upper <= lower:
        raise ValueError("upper_price must be greater than lower_price")
    if count < 2:
        raise ValueError("grid_count must be at least 2")
    if grid_type == "geometric":
        ratio = (upper / lower) ** (1 / (count - 1))
        return [lower * (ratio**i) for i in range(count)]
    step = (upper - lower) / (count - 1)
    return [lower + step * i for i in range(count)]


@dataclass
class BacktestResult:
    trades: int
    realized_pnl: float
    max_drawdown_pct: float
    win_rate: float
    ending_equity: float
    notes: str


def backtest_grid(
    closes: list[float],
    lower: float,
    upper: float,
    grid_count: int,
    capital: float,
    grid_type: str = "arithmetic",
) -> BacktestResult:
    """Simple replay: buy below mid levels, sell above; inventory + cash tracking."""
    if not closes:
        return BacktestResult(0, 0.0, 0.0, 0.0, capital, "No candle data")

    levels = build_grid_prices(lower, upper, grid_count, grid_type)
    cash = capital
    inventory = 0.0
    entry_cost = 0.0
    trades = 0
    wins = 0
    equity_curve = [capital]
    peak = capital
    max_dd = 0.0

    last_side = None
    for price in closes:
        # Find nearest levels crossed conceptually
        for lvl in levels:
            qty = (capital / grid_count) / lvl if lvl > 0 else 0
            if qty <= 0:
                continue
            if price <= lvl and cash >= qty * lvl and last_side != "buy":
                cash -= qty * lvl
                inventory += qty
                entry_cost += qty * lvl
                trades += 1
                last_side = "buy"
                break
            if price >= lvl and inventory >= qty and last_side != "sell":
                proceeds = qty * lvl
                avg = (entry_cost / inventory) if inventory else lvl
                pnl = proceeds - qty * avg
                cash += proceeds
                inventory -= qty
                entry_cost = max(0.0, entry_cost - qty * avg)
                trades += 1
                if pnl > 0:
                    wins += 1
                last_side = "sell"
                break

        mark = cash + inventory * price
        equity_curve.append(mark)
        peak = max(peak, mark)
        dd = ((peak - mark) / peak * 100) if peak else 0
        max_dd = max(max_dd, dd)

    ending = equity_curve[-1]
    realized = ending - capital
    win_rate = (wins / trades * 100) if trades else 0.0
    return BacktestResult(
        trades=trades,
        realized_pnl=round(realized, 4),
        max_drawdown_pct=round(max_dd, 4),
        win_rate=round(win_rate, 2),
        ending_equity=round(ending, 4),
        notes="Simulated spot grid replay — not a guarantee of live results.",
    )
