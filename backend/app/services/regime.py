"""Market regime detection.

Classifies a symbol from multiple indicators across the primary timeframe, with
the higher timeframe used as a tie-breaker. The regime decides which strategies
are allowed to fire; "unclear" is a first-class outcome and disables trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.services.indicators import IndicatorSnapshot


class Regime(str, Enum):
    STRONG_BULL = "strong_bull"
    WEAK_BULL = "weak_bull"
    STRONG_BEAR = "strong_bear"
    WEAK_BEAR = "weak_bear"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNCLEAR = "unclear"


TRENDING = {Regime.STRONG_BULL, Regime.WEAK_BULL, Regime.STRONG_BEAR, Regime.WEAK_BEAR}
BULLISH = {Regime.STRONG_BULL, Regime.WEAK_BULL}
BEARISH = {Regime.STRONG_BEAR, Regime.WEAK_BEAR}

# Thresholds tuned for 5m/15m crypto scalping; overridable per deployment.
ADX_TREND = 22.0
ADX_STRONG = 30.0
ATR_PCT_HIGH = 1.6
ATR_PCT_LOW = 0.15


@dataclass
class RegimeAssessment:
    regime: Regime
    confidence: float  # 0..1
    reasons: list[str] = field(default_factory=list)
    trend_bias: str = "neutral"  # long | short | neutral
    allow_long: bool = False
    allow_short: bool = False
    allow_trend_strategy: bool = False
    allow_breakout_strategy: bool = False
    allow_range_strategy: bool = False
    tradable: bool = False
    block_reason: str | None = None

    def as_dict(self) -> dict:
        data = dict(self.__dict__)
        data["regime"] = self.regime.value
        return data


def _trend_direction(snap: IndicatorSnapshot) -> int:
    """+1 bullish stack, -1 bearish stack, 0 mixed."""
    stack = [snap.ema9, snap.ema21, snap.ema50 or snap.ema21]
    if any(v is None for v in stack):
        return 0
    if stack[0] > stack[1] > stack[2] and snap.close > stack[1]:
        return 1
    if stack[0] < stack[1] < stack[2] and snap.close < stack[1]:
        return -1
    return 0


def detect_regime(
    primary: IndicatorSnapshot,
    secondary: IndicatorSnapshot | None = None,
) -> RegimeAssessment:
    """`primary` is the direction timeframe (15m by default); `secondary` is the
    setup timeframe, used to confirm and to detect timeframe conflict."""
    reasons: list[str] = []

    if not primary.ready:
        return RegimeAssessment(
            regime=Regime.UNCLEAR,
            confidence=0.0,
            reasons=["Not enough candle history for indicators"],
            block_reason="insufficient_data",
        )

    adx = primary.adx or 0.0
    atr_pct = primary.atr_pct or 0.0
    direction = _trend_direction(primary)
    secondary_direction = _trend_direction(secondary) if secondary and secondary.ready else 0

    # --- volatility gates come first: they can veto everything ---
    if atr_pct >= ATR_PCT_HIGH:
        reasons.append(f"ATR {atr_pct:.2f}% of price is above the {ATR_PCT_HIGH}% high-volatility gate")
        return RegimeAssessment(
            regime=Regime.HIGH_VOLATILITY,
            confidence=min(1.0, atr_pct / (ATR_PCT_HIGH * 2)),
            reasons=reasons,
            tradable=False,
            block_reason="volatility_too_high",
        )
    if atr_pct and atr_pct <= ATR_PCT_LOW:
        reasons.append(f"ATR {atr_pct:.2f}% of price is below the {ATR_PCT_LOW}% floor; edge dies in fees")
        return RegimeAssessment(
            regime=Regime.LOW_VOLATILITY,
            confidence=0.5,
            reasons=reasons,
            tradable=False,
            block_reason="volatility_too_low",
        )

    # --- conflicting timeframes: do nothing ---
    if direction != 0 and secondary_direction != 0 and direction != secondary_direction:
        reasons.append("Primary and setup timeframe trends disagree")
        return RegimeAssessment(
            regime=Regime.UNCLEAR,
            confidence=0.3,
            reasons=reasons,
            tradable=False,
            block_reason="conflicting_timeframes",
        )

    reasons.append(f"ADX {adx:.1f}")
    reasons.append(f"ATR {atr_pct:.2f}% of price")

    if adx >= ADX_TREND and direction != 0:
        strong = adx >= ADX_STRONG and secondary_direction in (direction, 0)
        if direction > 0:
            regime = Regime.STRONG_BULL if strong else Regime.WEAK_BULL
            reasons.append("EMA 9/21/50 stacked bullish, price above EMA21")
        else:
            regime = Regime.STRONG_BEAR if strong else Regime.WEAK_BEAR
            reasons.append("EMA 9/21/50 stacked bearish, price below EMA21")
        confidence = min(1.0, adx / 40)
        assessment = RegimeAssessment(
            regime=regime,
            confidence=confidence,
            reasons=reasons,
            trend_bias="long" if direction > 0 else "short",
            allow_long=direction > 0,
            allow_short=direction < 0,
            allow_trend_strategy=True,
            allow_breakout_strategy=True,
            allow_range_strategy=False,
            tradable=True,
        )
        if secondary_direction == direction:
            assessment.reasons.append("Setup timeframe confirms the same direction")
            assessment.confidence = min(1.0, assessment.confidence + 0.1)
        return assessment

    if adx < ADX_TREND:
        reasons.append(f"ADX below {ADX_TREND}: no directional conviction, treating as a range")
        return RegimeAssessment(
            regime=Regime.RANGING,
            confidence=min(1.0, (ADX_TREND - adx) / ADX_TREND + 0.3),
            reasons=reasons,
            trend_bias="neutral",
            allow_long=True,
            allow_short=True,
            allow_trend_strategy=False,
            allow_breakout_strategy=True,
            allow_range_strategy=True,
            tradable=True,
        )

    reasons.append("Trend strength present but EMA structure is mixed")
    return RegimeAssessment(
        regime=Regime.UNCLEAR,
        confidence=0.2,
        reasons=reasons,
        tradable=False,
        block_reason="mixed_structure",
    )
