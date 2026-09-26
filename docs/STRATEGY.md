# Trading strategy

This document describes what the bot actually does, in the order the code does it.
Implementation lives in `backend/app/services/`.

## 1. Market data

Three timeframes per symbol, fetched every tick (default 20s):

| Role | Default | Purpose |
| --- | --- | --- |
| Primary | 15m | Direction of the market |
| Setup | 5m | The setup itself: pullback, breakout, range edge |
| Entry | 1m | Entry timing |

The most recent candle Binance returns is still forming, so it is **dropped**
before indicators run. Its close is kept separately as the current price. Every
indicator value at index *i* depends only on candles 0..*i*, which is what makes
the live engine and the backtester agree (`test_indicators_never_look_ahead`).

## 2. Regime detection (`regime.py`)

Inputs: EMA 9/21/50/200, RSI, MACD, ADX, +DI/-DI, ATR, Bollinger width, volume
and its moving average, VWAP, and both the primary and setup timeframes.

Order of evaluation — volatility gates come first because they can veto
everything:

1. **ATR ≥ 1.6% of price** → `high_volatility`, not tradable. Stops get run over
   in this environment.
2. **ATR ≤ 0.15% of price** → `low_volatility`, not tradable. The move is smaller
   than the round-trip cost.
3. **Primary and setup timeframes disagree on direction** → `unclear`, not
   tradable.
4. **ADX ≥ 22 with EMA 9/21/50 stacked and price on the right side** →
   `strong_bull` / `weak_bull` / `strong_bear` / `weak_bear`. Strong requires
   ADX ≥ 30 and no contradiction from the setup timeframe.
5. **ADX < 22** → `ranging`.
6. Anything else → `unclear`, not tradable.

The regime decides which strategies may even be evaluated:

| Regime | Trend | Breakout | Range | Long | Short |
| --- | --- | --- | --- | --- | --- |
| strong/weak bull | ✅ | ✅ | ❌ | ✅ | ❌ |
| strong/weak bear | ✅ | ✅ | ❌ | ❌ | ✅ |
| ranging | ❌ | ✅ | ✅ | ✅ | ✅ |
| high/low volatility, unclear | ❌ | ❌ | ❌ | ❌ | ❌ |

Shorts are additionally gated on `FUTURES_ENABLED`: a spot market is never shorted.

## 3. Strategies

### A — Trend pullback (`strategies/trend_pullback.py`)

Only in a trending regime. Requires all of:

- price has pulled back to within 0.5 ATR of the setup EMA21 (and is not more
  than 2.5 ATR beyond it, which would mean the trend has broken),
- the entry-timeframe MACD histogram is turning back in the trend direction,
- RSI is on the correct side of neutral,
- a usable swing low (long) or swing high (short) exists for the stop.

VWAP proximity and volume improve the score but are not hard gates.

### B — Breakout (`strategies/breakout.py`)

Support and resistance are taken from the consolidation *before* the breakout
candle. The strategy requires:

- price beyond the level,
- **no more than 1.2 ATR beyond it** — chasing an extended candle is refused,
- volume at least 1.0× its 20-period average — a break without volume is usually
  a trap.

A tight pre-break range relative to ATR (a squeeze) raises the score.

### C — Range scalping (`strategies/range_scalping.py`)

Only in a ranging regime, and it re-checks ADX itself as a second guard. It
requires:

- a range at least 3× the round-trip cost wide,
- price in the outer 25% of the range (mid-range entries are refused),
- the level tested at least twice,
- entry-timeframe momentum rejecting the level.

## 4. Scoring (`signals.py`)

Each strategy returns six components in 0..1. They are weighted (weights are
configurable and normalised to 100):

| Component | Default weight |
| --- | --- |
| Trend | 25 |
| Momentum | 20 |
| Volume | 15 |
| Volatility | 15 |
| Structure (S/R) | 15 |
| Entry timing | 10 |

A signal is only produced when the total reaches `MINIMUM_SIGNAL_SCORE`
(default 75) **and** the reward/risk *after* fees and slippage is at least 1.2:

```
net_reward = |TP1 − entry| / entry × 100 − round_trip_cost
net_risk   = stop_distance_pct + round_trip_cost
```

A setup whose edge disappears once costs are subtracted is not taken.

## 5. Stops

`STOP_MODE` selects the method:

- **atr** (default) — `entry ∓ ATR × ATR_STOP_MULTIPLIER`, then clamped so it
  never sits on the wrong side of the setup's invalidation level.
- **structure** — just beyond the recent swing low/high, with a 0.25 ATR buffer.
- **fixed** — a fixed percentage.

In live and testnet modes a protective stop order is placed on the exchange
immediately after entry. **If the stop cannot be placed, the position is closed
at once** — a live position without a stop is not allowed to exist.

## 6. Targets and trailing

- TP1 at `TP1_R_MULTIPLE` × risk (default 1.5R), closing `TP1_CLOSE_FRACTION`
  (default 50%) of the position.
- After TP1, the stop moves to breakeven if `MOVE_STOP_TO_BREAKEVEN_AFTER_TP1`.
- TP2 at `TP2_R_MULTIPLE` × risk (default 2.5R) closes the remainder.
- After TP1, an ATR trailing stop (`TRAILING_ATR_MULTIPLIER`) follows the move and
  only ever ratchets in the favourable direction.

When one candle touches both the stop and a target, the **stop is assumed to have
filled first** — in the live engine and in the backtester alike.

## 7. Costs

Fees and slippage are not an afterthought. They enter:

- the reward/risk gate that decides whether a signal exists at all,
- the volatility score, which is scaled against the round-trip cost,
- the minimum range width for range scalping,
- every simulated fill in paper mode and in the backtester,
- funding, charged every 8 hours in futures backtests.

Defaults are 0.04% taker per side and 0.02% assumed slippage per side, i.e. a
0.12% round trip. Set them to your actual VIP tier and observed slippage.

## 8. Backtesting (`backtest.py`)

Event-driven, walking the 1m series one candle at a time. Higher timeframes are
rebuilt from candles that had already closed at that moment, so a signal can
never see its own future. Runs split into train (60%) / validation (20%) /
out-of-sample (20%), and the API returns an explicit warning when out-of-sample
performance collapses relative to training.

Tune parameters on the training segment only. If the out-of-sample segment
disagrees, the parameters are overfitted — not validated.
