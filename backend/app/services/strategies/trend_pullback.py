"""Strategy A — Trend Pullback.

Higher timeframe decides direction, the setup timeframe must show a pullback into
value (EMA21 / VWAP), and the entry timeframe must show momentum turning back in
the trend direction. Shorts are only produced for futures; spot never shorts.
"""

from __future__ import annotations

from app.config import Settings
from app.services.contracts import MarketContext, ScoreBreakdown, Side, StrategyCandidate
from app.services.regime import BEARISH, BULLISH
from app.services.strategies.base import Strategy, clamp, scaled


class TrendPullbackStrategy(Strategy):
    name = "trend_pullback"
    enabled_setting = "strategy_trend_pullback"

    def evaluate(self, ctx: MarketContext, settings: Settings) -> StrategyCandidate | None:
        regime = ctx.regime
        if not regime.allow_trend_strategy:
            return None

        if regime.regime in BULLISH:
            side = Side.LONG
        elif regime.regime in BEARISH:
            side = Side.SHORT
        else:
            return None

        if side is Side.SHORT and not settings.futures_enabled:
            return None  # never short a spot market

        setup, entry = ctx.setup, ctx.entry
        if not (setup.ready and entry.ready):
            return None

        anchor = setup.ema21
        if anchor is None:
            return None
        vwap = setup.vwap or anchor
        atr = setup.atr or 0.0
        if atr <= 0:
            return None

        reasons: list[str] = [f"{ctx.primary.timeframe} regime {regime.regime.value}"]
        scores = ScoreBreakdown()

        # --- pullback depth: price should be near value, not extended away from it ---
        distance_atr = abs(setup.close - anchor) / atr
        if side is Side.LONG:
            pulled_back = setup.close <= anchor + 0.5 * atr
            extended = setup.close < anchor - 2.5 * atr  # trend likely broken
        else:
            pulled_back = setup.close >= anchor - 0.5 * atr
            extended = setup.close > anchor + 2.5 * atr
        if not pulled_back or extended:
            return None
        reasons.append(f"{setup.timeframe} pulled back to within {distance_atr:.2f} ATR of EMA21")

        near_vwap = abs(setup.close - vwap) / atr <= 1.5
        if near_vwap:
            reasons.append(f"{setup.timeframe} price near VWAP")

        # --- trend component ---
        adx = ctx.primary.adx or 0.0
        scores.trend = clamp(0.5 * scaled(adx, 18, 35) + 0.5 * regime.confidence)

        # --- momentum recovery on the entry timeframe ---
        hist, hist_prev = entry.macd_hist, entry.macd_hist_prev
        rsi = entry.rsi or 50.0
        if side is Side.LONG:
            turning = hist is not None and hist_prev is not None and hist > hist_prev
            momentum_ok = turning and rsi > 42
            scores.momentum = clamp(0.6 * float(turning) + 0.4 * scaled(rsi, 40, 62))
        else:
            turning = hist is not None and hist_prev is not None and hist < hist_prev
            momentum_ok = turning and rsi < 58
            scores.momentum = clamp(0.6 * float(turning) + 0.4 * scaled(rsi, 60, 38))
        if not momentum_ok:
            return None
        reasons.append(f"{entry.timeframe} MACD histogram turning back with the trend (RSI {rsi:.0f})")

        # --- volume confirmation ---
        ratio = entry.volume_ratio or 0.0
        scores.volume = scaled(ratio, 0.7, 1.6)
        if ratio >= 1.0:
            reasons.append(f"Entry volume {ratio:.2f}x its 20-period average")

        # --- volatility: enough range to pay for fees, not so much it is chaos ---
        atr_pct = setup.atr_pct or 0.0
        cost = settings.round_trip_cost_pct
        scores.volatility = clamp(scaled(atr_pct, cost, cost * 6))

        # --- structure: distance to the invalidation level ---
        stop_ref = setup.swing_low if side is Side.LONG else setup.swing_high
        if stop_ref is None:
            return None
        stop_gap_pct = 100 * abs(setup.close - stop_ref) / setup.close
        scores.structure = clamp(0.5 * float(near_vwap) + 0.5 * scaled(2.0 - stop_gap_pct, -1.0, 1.5))

        # --- entry timing quality ---
        fast, slow = entry.ema9 or 0, entry.ema21 or 0
        aligned = fast > slow if side is Side.LONG else fast < slow
        scores.entry = clamp(0.6 * float(aligned) + 0.4 * scaled(distance_atr, 2.0, 0.2))

        return StrategyCandidate(
            strategy=self.name,
            side=side,
            scores=scores,
            reasons=reasons,
            stop_reference=stop_ref,
        )
