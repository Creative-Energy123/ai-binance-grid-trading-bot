# Risk management

The RiskEngine (`backend/app/services/risk.py`) is the only component allowed to
veto a signal, and it is the component to read first if you change anything.

Every check is evaluated rather than short-circuited, so the dashboard and the AI
assistant can name exactly which limit blocked a trade.

## Pre-trade checks

| Check | Setting | Default | Blocks when |
| --- | --- | --- | --- |
| Equity known | — | — | Equity is zero or unreadable |
| Max positions | `MAX_POSITIONS` | 2 | Already at the limit |
| Daily loss | `MAX_DAILY_LOSS_PCT` | 2% | Today's realized P&L breaches it |
| Weekly loss | `MAX_WEEKLY_LOSS_PCT` | 5% | The trailing 7 days breach it |
| Loss streak | `MAX_CONSECUTIVE_LOSSES` | 4 | The streak reaches the limit |
| Trade rate | `MAX_TRADES_PER_HOUR` | 6 | Too many closes in the last hour |
| Cooldown | `LOSS_COOLDOWN_MINUTES` | 20 | A losing trade closed recently |
| Per-symbol exposure | `MAX_SYMBOL_EXPOSURE_PCT` | 100% of equity | The new notional would exceed it |
| Total exposure | `MAX_TOTAL_EXPOSURE_PCT` | 150% of equity | The new notional would exceed it |
| Stop present | — | — | The signal has no usable stop |
| Stop sanity | — | 0–5% | The stop distance is outside the scalping band |
| Short allowed | `FUTURES_ENABLED` | false | A short is proposed on spot |

**On the exposure caps.** Scalping stops are tight, so a small risk budget implies
a large notional: risking 0.35% of equity behind a 0.4% stop is roughly 88% of
equity in notional. Caps set below that do not reduce risk — they silently shrink
every position and quietly make the realized risk smaller than configured. The
real risk control is `RISK_PER_TRADE_PCT`; the exposure caps exist to stop
concentration and runaway leverage, which is why they default to 100% / 150%.

## Position sizing

```
risk_amount = equity × RISK_PER_TRADE_PCT / 100
quantity    = risk_amount / |entry − stop|
```

Sizing is never a fixed notional. The result is then clamped by, in order:
available buying power (equity on spot, equity × leverage on futures), the
per-symbol exposure cap, and the exchange's amount step. If the result falls
below the exchange's minimum notional, the trade is refused rather than rounded
up — rounding up would break the risk budget.

When a cap binds, the resulting order carries a note saying so, and the realized
risk is *below* the configured target, never above it.

## Futures safety

- Isolated margin by default, leverage default 3x and hard-capped at
  `FUTURES_MAX_LEVERAGE`.
- The estimated liquidation price must sit at least
  `MIN_LIQUIDATION_DISTANCE_PCT` (default 8%) away, or the trade is refused.
- A stop that would sit beyond the liquidation price is refused outright.
- The dashboard shows entry, mark, liquidation price and the distance to it.

The liquidation estimate is deliberately conservative and is only a safety gate;
the exchange's own value is authoritative.

## What halts trading

`register_trade_result` and the engine's health loop pause the bot automatically when:

- the daily loss limit is reached (the day's statistics row is marked `halted`),
- consecutive losses reach the limit,
- account synchronisation fails,
- market data goes stale beyond `MARKET_DATA_STALENESS_SECONDS`,
- the Binance API is unreachable in a mode that places orders,
- the database is unreachable,
- a position appears on the exchange that the bot does not track, or vice versa,
- a protective stop cannot be placed (that position is also closed immediately).

A pause stops **new entries** while still managing open positions. The health
strip in the dashboard header shows each component's state, and `/api/health`
and `/metrics` expose the same information for monitoring.

## Emergency stop

`POST /api/bot/emergency-stop` (the red button in the dashboard):

1. stops new orders,
2. cancels every resting exchange order across all configured symbols,
3. optionally closes all open positions at market,
4. records a critical `risk_events` row and fires alerts on every channel.

The bot stays emergency-stopped until an operator explicitly starts it again.

## What the bot never does

- No martingale, no unlimited averaging down, no position without a stop.
- No re-entry during a cooldown or after a halt without operator action.
- No resuming automatically after a restart — it always comes up stopped.
- No trade whose edge disappears after fees and slippage.

## Choosing your numbers

The defaults are deliberately conservative. Before changing them:

1. Backtest with your real fee tier and a slippage estimate you have measured.
2. Run the same configuration in paper mode for long enough to see a losing
   streak, not just a good day.
3. Change one limit at a time, and keep `RISK_PER_TRADE_PCT` at or below 0.5%.

A higher risk per trade does not raise expectancy; it raises the size of the
drawdown you have to survive to find out whether the edge was real.
