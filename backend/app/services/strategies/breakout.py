"""Strategy B — Breakout.

Looks for a consolidation (Bollinger squeeze / narrow range), a level being taken
out on expanding volume and ATR, and refuses to chase a candle that has already
travelled most of the move.
"""

from __future__ import annotations

from app.config import Settings
from app.services.contracts import MarketContext, ScoreBreakdown, Side, StrategyCandidate
from app.services.indicators import IndicatorSnapshot
from app.services.strategies.base import Strategy, clamp, scaled

MAX_EXTENSION_ATR = 1.2  # how far past the level we will still enter


def _range_levels(snap: IndicatorSnapshot, lookback: int = 20) -> tuple[float, float] | None:
    """Support/resistance from the consolidation *before* the breakout candle."""
    window = snap.candles[-(lookback + 1) : -1]
    if len(window) < lookback // 2:
        return None
    return min(float(c[3]) for c in window), max(float(c[2]) for c in window)


class BreakoutStrategy(Strategy):
    name = "breakout"
    enabled_setting = "strategy_breakout"

    def evaluate(self, ctx: MarketContext, settings: Settings) -> StrategyCandidate | None:
        if not ctx.regime.allow_breakout_strategy:
            return None
        setup, entry = ctx.setup, ctx.entry
        if not (setup.ready and entry.ready):
            return None

        atr = setup.atr or 0.0
        levels = _range_levels(setup, settings.structure_lookback)
        if atr <= 0 or levels is None:
            return None
        support, resistance = levels
        band = resistance - support
        if band <= 0:
            return None

        price = setup.close
        if price > resistance:
            side = Side.LONG
            level = resistance
            opposite = support
        elif price < support:
            side = Side.SHORT
            level = support
            opposite = resistance
        else:
            return None  # no break yet

        if side is Side.SHORT and not settings.futures_enabled:
            return None
        if side is Side.LONG and not ctx.regime.allow_long:
            return None
        if side is Side.SHORT and not ctx.regime.allow_short:
            return None

        extension_atr = abs(price - level) / atr
        if extension_atr > MAX_EXTENSION_ATR:
            return None  # already moved too far; chasing pays the worst price

        reasons = [
            f"{setup.timeframe} broke {'resistance' if side is Side.LONG else 'support'} at {level:.6g}",
            f"Only {extension_atr:.2f} ATR past the level, not chasing",
        ]

        # --- consolidation quality: tight band relative to ATR is a real squeeze ---
        squeeze = clamp(scaled(band / atr, 6.0, 1.5))
        if squeeze > 0.5:
            reasons.append("Preceded by a tight consolidation")

        scores = ScoreBreakdown()
        scores.trend = clamp(0.4 * scaled(ctx.primary.adx or 0, 15, 32) + 0.6 * squeeze)

        rsi = entry.rsi or 50.0
        hist = entry.macd_hist or 0.0
        if side is Side.LONG:
            scores.momentum = clamp(0.5 * scaled(rsi, 48, 68) + 0.5 * float(hist > 0))
        else:
            scores.momentum = clamp(0.5 * scaled(rsi, 52, 32) + 0.5 * float(hist < 0))

        ratio = setup.volume_ratio or 0.0
        if ratio < 1.0:
            return None  # a break without volume is usually a trap
        scores.volume = clamp(scaled(ratio, 1.0, 2.2))
        reasons.append(f"Breakout volume {ratio:.2f}x average")

        atr_pct = setup.atr_pct or 0.0
        cost = settings.round_trip_cost_pct
        scores.volatility = clamp(scaled(atr_pct, cost, cost * 6))

        scores.structure = clamp(0.5 * squeeze + 0.5 * scaled(abs(price - opposite) / atr, 1.0, 5.0))
        scores.entry = clamp(scaled(extension_atr, MAX_EXTENSION_ATR, 0.1))

        stop_ref = level - 0.25 * atr if side is Side.LONG else level + 0.25 * atr
        return StrategyCandidate(
            strategy=self.name,
            side=side,
            scores=scores,
            reasons=reasons,
            stop_reference=stop_ref,
        )
