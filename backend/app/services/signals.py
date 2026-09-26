"""SignalEngine — turns strategy candidates into scored, fully specified signals.

Scoring is a weighted sum of six components (weights are configurable and are
normalised to 100 so custom weights keep the threshold meaningful). Stops and
targets are attached here so the RiskEngine downstream can reason about a real
risk/reward rather than an abstract direction.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.services.strategies import ALL_STRATEGIES
from app.services.contracts import (
    MarketContext,
    ScoreBreakdown,
    Side,
    StrategyCandidate,
    TradeSignal,
)

MIN_REWARD_RISK = 1.2


@dataclass
class SignalEvaluation:
    """Full output of one evaluation pass, including why nothing was taken."""

    symbol: str
    regime: str
    tradable: bool
    signal: TradeSignal | None = None
    best_score: float = 0.0
    rejected_reason: str | None = None
    candidates: list[dict] | None = None


def _weighted_total(scores: ScoreBreakdown, settings: Settings) -> tuple[float, dict[str, float]]:
    weights = settings.score_weights
    total_weight = sum(weights.values()) or 1.0
    weighted = {
        key: getattr(scores, key) * weight * 100 / total_weight for key, weight in weights.items()
    }
    return sum(weighted.values()), weighted


def compute_stop(ctx: MarketContext, candidate: StrategyCandidate, settings: Settings) -> float | None:
    """Protective stop for a candidate, honouring the configured stop mode."""
    price = ctx.price
    atr = ctx.setup.atr or 0.0
    long = candidate.side is Side.LONG

    if settings.stop_mode == "fixed":
        delta = price * settings.fixed_stop_pct / 100
        return price - delta if long else price + delta

    if settings.stop_mode == "structure":
        ref = candidate.stop_reference
        if ref is None:
            return None
        buffer = 0.25 * atr
        return ref - buffer if long else ref + buffer

    if atr <= 0:
        return None
    delta = atr * settings.atr_stop_multiplier
    stop = price - delta if long else price + delta

    # Never place the stop on the wrong side of the setup's invalidation level.
    ref = candidate.stop_reference
    if ref is not None:
        stop = min(stop, ref - 0.1 * atr) if long else max(stop, ref + 0.1 * atr)
    return stop


def compute_targets(entry: float, stop: float, side: Side, settings: Settings) -> tuple[float, float]:
    risk = abs(entry - stop)
    if side is Side.LONG:
        return entry + risk * settings.tp1_r_multiple, entry + risk * settings.tp2_r_multiple
    return entry - risk * settings.tp1_r_multiple, entry - risk * settings.tp2_r_multiple


def evaluate(ctx: MarketContext, settings: Settings | None = None) -> SignalEvaluation:
    settings = settings or get_settings()
    regime = ctx.regime

    if not regime.tradable:
        return SignalEvaluation(
            symbol=ctx.symbol,
            regime=regime.regime.value,
            tradable=False,
            rejected_reason=regime.block_reason or "regime_not_tradable",
        )

    graded: list[tuple[float, dict[str, float], StrategyCandidate]] = []
    for strategy in ALL_STRATEGIES:
        if not strategy.is_enabled(settings):
            continue
        candidate = strategy.evaluate(ctx, settings)
        if candidate is None:
            continue
        total, weighted = _weighted_total(candidate.scores, settings)
        graded.append((total, weighted, candidate))

    if not graded:
        return SignalEvaluation(
            symbol=ctx.symbol,
            regime=regime.regime.value,
            tradable=True,
            rejected_reason="no_strategy_setup",
        )

    graded.sort(key=lambda item: item[0], reverse=True)
    summaries = [
        {"strategy": c.strategy, "side": c.side.value, "score": round(total, 2)}
        for total, _, c in graded
    ]
    total, weighted, candidate = graded[0]

    if total < settings.minimum_signal_score:
        return SignalEvaluation(
            symbol=ctx.symbol,
            regime=regime.regime.value,
            tradable=True,
            best_score=total,
            rejected_reason=f"score_below_threshold ({total:.1f} < {settings.minimum_signal_score})",
            candidates=summaries,
        )

    stop = compute_stop(ctx, candidate, settings)
    if stop is None or stop <= 0:
        return SignalEvaluation(
            symbol=ctx.symbol,
            regime=regime.regime.value,
            tradable=True,
            best_score=total,
            rejected_reason="no_valid_stop",
            candidates=summaries,
        )

    entry_price = ctx.price
    risk_pct = 100 * abs(entry_price - stop) / entry_price
    if risk_pct <= 0:
        return SignalEvaluation(
            symbol=ctx.symbol,
            regime=regime.regime.value,
            tradable=True,
            best_score=total,
            rejected_reason="zero_stop_distance",
            candidates=summaries,
        )

    tp1, tp2 = compute_targets(entry_price, stop, candidate.side, settings)

    # Reward/risk is measured *after* fees and slippage; an edge that only exists
    # gross of costs is not an edge.
    cost_pct = settings.round_trip_cost_pct
    gross_reward_pct = 100 * abs(tp1 - entry_price) / entry_price
    net_reward_pct = gross_reward_pct - cost_pct
    net_risk_pct = risk_pct + cost_pct
    reward_risk = net_reward_pct / net_risk_pct if net_risk_pct > 0 else 0.0
    if reward_risk < MIN_REWARD_RISK:
        return SignalEvaluation(
            symbol=ctx.symbol,
            regime=regime.regime.value,
            tradable=True,
            best_score=total,
            rejected_reason=(
                f"reward_risk_after_costs {reward_risk:.2f} below minimum {MIN_REWARD_RISK}"
            ),
            candidates=summaries,
        )

    signal = TradeSignal(
        symbol=ctx.symbol,
        side=candidate.side,
        strategy=candidate.strategy,
        score=total,
        max_score=100.0,
        scores=candidate.scores,
        weighted=weighted,
        entry_price=entry_price,
        stop_price=stop,
        tp1_price=tp1,
        tp2_price=tp2,
        risk_pct=settings.risk_per_trade_pct,
        reward_risk=reward_risk,
        regime=regime.regime.value,
        reasons=[*regime.reasons, *candidate.reasons],
        as_of=ctx.as_of,
    )
    return SignalEvaluation(
        symbol=ctx.symbol,
        regime=regime.regime.value,
        tradable=True,
        signal=signal,
        best_score=total,
        candidates=summaries,
    )
