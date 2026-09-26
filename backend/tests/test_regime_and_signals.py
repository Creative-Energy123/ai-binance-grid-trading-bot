from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.contracts import MarketContext, ScoreBreakdown, Side, StrategyCandidate
from app.services.indicators import compute_snapshot
from app.services.regime import BULLISH, Regime, detect_regime
from app.services.signals import compute_stop, compute_targets, evaluate
from app.services.strategies.range_scalping import RangeScalpingStrategy
from tests.conftest import synth_candles


def context(candles_primary, candles_setup, candles_entry, price=None) -> MarketContext:
    primary = compute_snapshot("15m", candles_primary)
    setup = compute_snapshot("5m", candles_setup)
    entry = compute_snapshot("1m", candles_entry)
    return MarketContext(
        symbol="BTC/USDT",
        price=price or entry.close,
        primary=primary,
        setup=setup,
        entry=entry,
        regime=detect_regime(primary, setup),
    )


def test_uptrend_is_classified_bullish_and_enables_trend_strategies():
    candles = synth_candles(400, drift=0.0022, noise=0.0020)
    assessment = detect_regime(compute_snapshot("15m", candles))
    assert assessment.regime in BULLISH
    assert assessment.allow_trend_strategy and assessment.allow_long
    assert assessment.allow_range_strategy is False


def test_downtrend_is_classified_bearish_and_blocks_longs():
    candles = synth_candles(400, drift=-0.0022, noise=0.0020)
    assessment = detect_regime(compute_snapshot("15m", candles))
    assert assessment.regime.value.endswith("bear")
    assert assessment.allow_short and not assessment.allow_long


def test_chop_is_classified_ranging_and_disables_the_trend_strategy():
    candles = synth_candles(400, drift=0.0, wave=0.002, noise=0.004)
    assessment = detect_regime(compute_snapshot("15m", candles))
    assert assessment.regime is Regime.RANGING
    assert assessment.allow_range_strategy and not assessment.allow_trend_strategy


def test_extreme_volatility_blocks_all_trading():
    candles = synth_candles(400, noise=0.02)
    assessment = detect_regime(compute_snapshot("15m", candles))
    assert assessment.regime is Regime.HIGH_VOLATILITY
    assert assessment.tradable is False
    assert assessment.block_reason == "volatility_too_high"


def test_conflicting_timeframes_produce_unclear_and_no_trade():
    up = compute_snapshot("15m", synth_candles(400, drift=0.0022, noise=0.0020))
    down = compute_snapshot("5m", synth_candles(400, drift=-0.0022, noise=0.0020, seed=11))
    assessment = detect_regime(primary=up, secondary=down)
    assert assessment.regime is Regime.UNCLEAR
    assert assessment.tradable is False
    assert assessment.block_reason == "conflicting_timeframes"


def test_insufficient_history_is_not_tradable():
    assessment = detect_regime(compute_snapshot("15m", synth_candles(10)))
    assert assessment.tradable is False
    assert assessment.block_reason == "insufficient_data"


def test_signal_engine_declines_when_the_regime_is_untradable():
    candles = synth_candles(400, noise=0.02)
    ctx = context(candles, candles, candles)
    evaluation = evaluate(ctx)
    assert evaluation.signal is None
    assert evaluation.tradable is False


def test_range_strategy_refuses_to_run_in_a_trend():
    candles = synth_candles(400, drift=0.0022, noise=0.0020)
    ctx = context(candles, candles, candles)
    assert RangeScalpingStrategy().evaluate(ctx, get_settings()) is None


def test_spot_mode_never_produces_a_short():
    settings = get_settings()
    settings.futures_enabled = False
    candles = synth_candles(600, drift=-0.0015, noise=0.0020)
    ctx = context(candles, candles, candles)
    evaluation = evaluate(ctx, settings)
    assert evaluation.signal is None or evaluation.signal.side is Side.LONG


def test_atr_stop_sits_below_entry_for_a_long_and_targets_respect_r_multiples():
    settings = get_settings()
    settings.stop_mode = "atr"
    candles = synth_candles(400, drift=0.0012, noise=0.0020)
    ctx = context(candles, candles, candles)
    candidate = StrategyCandidate(
        strategy="test",
        side=Side.LONG,
        scores=ScoreBreakdown(),
        stop_reference=ctx.setup.swing_low,
    )
    stop = compute_stop(ctx, candidate, settings)
    assert stop is not None and stop < ctx.price

    tp1, tp2 = compute_targets(ctx.price, stop, Side.LONG, settings)
    risk = ctx.price - stop
    assert tp1 == pytest.approx(ctx.price + risk * settings.tp1_r_multiple)
    assert tp2 == pytest.approx(ctx.price + risk * settings.tp2_r_multiple)


def test_score_threshold_blocks_weak_setups():
    settings = get_settings()
    original = settings.minimum_signal_score
    settings.minimum_signal_score = 99.9
    try:
        candles = synth_candles(600, drift=0.0012, noise=0.0020)
        ctx = context(candles, candles, candles)
        evaluation = evaluate(ctx, settings)
        assert evaluation.signal is None
    finally:
        settings.minimum_signal_score = original
