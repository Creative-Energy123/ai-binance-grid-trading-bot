"""Strategy C — Range Scalping.

Only runs when the regime engine reports a range (ADX below the trend gate). Buys
validated support, sells/shorts validated resistance, and never enters in the
middle third of the range. Automatically disabled the moment a trend appears,
because `regime.allow_range_strategy` is false outside ranging conditions.
"""

from __future__ import annotations

from app.config import Settings
from app.services.contracts import MarketContext, ScoreBreakdown, Side, StrategyCandidate
from app.services.indicators import IndicatorSnapshot
from app.services.regime import ADX_TREND
from app.services.strategies.base import Strategy, clamp, scaled

EDGE_FRACTION = 0.25  # entries must sit in the outer quarter of the range
MIN_TOUCHES = 2


def _touches(snap: IndicatorSnapshot, level: float, tolerance: float, low_side: bool) -> int:
    count = 0
    for c in snap.candles[-40:]:
        value = float(c[3]) if low_side else float(c[2])
        if abs(value - level) <= tolerance:
            count += 1
    return count


class RangeScalpingStrategy(Strategy):
    name = "range_scalping"
    enabled_setting = "strategy_range_scalping"

    def evaluate(self, ctx: MarketContext, settings: Settings) -> StrategyCandidate | None:
        if not ctx.regime.allow_range_strategy:
            return None
        if (ctx.primary.adx or 0) >= ADX_TREND:
            return None  # belt and braces: a trend disables range scalping

        setup, entry = ctx.setup, ctx.entry
        if not (setup.ready and entry.ready):
            return None

        atr = setup.atr or 0.0
        support, resistance = setup.swing_low, setup.swing_high
        if atr <= 0 or support is None or resistance is None:
            return None
        band = resistance - support
        if band <= 0:
            return None

        # The range has to be wide enough that one leg pays for the round trip.
        band_pct = 100 * band / setup.close
        min_band_pct = settings.round_trip_cost_pct * 3
        if band_pct < min_band_pct:
            return None

        price = setup.close
        position_in_range = (price - support) / band

        if position_in_range <= EDGE_FRACTION:
            side = Side.LONG
            level, target = support, resistance
        elif position_in_range >= 1 - EDGE_FRACTION:
            side = Side.SHORT
            level, target = resistance, support
        else:
            return None  # mid-range: no edge

        if side is Side.SHORT and not settings.futures_enabled:
            return None

        tolerance = 0.4 * atr
        touches = _touches(setup, level, tolerance, low_side=side is Side.LONG)
        if touches < MIN_TOUCHES:
            return None  # level is not validated yet
        reasons = [
            f"Range {support:.6g}–{resistance:.6g} ({band_pct:.2f}% wide)",
            f"Price in the outer {int(EDGE_FRACTION * 100)}% near "
            f"{'support' if side is Side.LONG else 'resistance'}",
            f"Level tested {touches} times",
        ]

        scores = ScoreBreakdown()
        # In a range, "trend" scores the *absence* of trend.
        scores.trend = clamp(scaled(ctx.primary.adx or 0, ADX_TREND, 10))

        rsi = entry.rsi or 50.0
        hist, hist_prev = entry.macd_hist, entry.macd_hist_prev
        if side is Side.LONG:
            rejecting = hist is not None and hist_prev is not None and hist > hist_prev
            scores.momentum = clamp(0.5 * scaled(rsi, 45, 25) + 0.5 * float(rejecting))
        else:
            rejecting = hist is not None and hist_prev is not None and hist < hist_prev
            scores.momentum = clamp(0.5 * scaled(rsi, 55, 75) + 0.5 * float(rejecting))
        if not rejecting:
            return None
        reasons.append(f"{entry.timeframe} momentum rejecting the level (RSI {rsi:.0f})")

        ratio = entry.volume_ratio or 0.0
        scores.volume = clamp(scaled(ratio, 0.5, 1.4))
        scores.volatility = clamp(scaled(band_pct, min_band_pct, settings.round_trip_cost_pct * 12))
        distance_to_target = abs(price - target) / band
        scores.structure = clamp(
            0.5 * scaled(touches, MIN_TOUCHES, 5) + 0.5 * scaled(distance_to_target, 0.5, 1.0)
        )
        edge_distance = position_in_range if side is Side.LONG else 1 - position_in_range
        scores.entry = clamp(scaled(edge_distance, EDGE_FRACTION, 0.0))

        stop_ref = level - 0.5 * atr if side is Side.LONG else level + 0.5 * atr
        return StrategyCandidate(
            strategy=self.name,
            side=side,
            scores=scores,
            reasons=reasons,
            stop_reference=stop_ref,
        )
