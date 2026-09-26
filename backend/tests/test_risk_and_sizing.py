from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.services.contracts import ScoreBreakdown, Side, TradeSignal
from app.services.risk import RiskState, evaluate_limits
from app.services.sizing import SizingError, liquidation_price, size_position


def make_signal(side: Side = Side.LONG, entry: float = 100.0, stop: float = 99.5) -> TradeSignal:
    return TradeSignal(
        symbol="BTC/USDT",
        side=side,
        strategy="trend_pullback",
        score=82.0,
        max_score=100.0,
        scores=ScoreBreakdown(),
        weighted={},
        entry_price=entry,
        stop_price=stop,
        tp1_price=entry + (entry - stop) * 1.5,
        tp2_price=entry + (entry - stop) * 2.5,
        risk_pct=0.35,
        reward_risk=1.5,
        regime="strong_bull",
    )


def clean_state(**overrides) -> RiskState:
    base = dict(
        equity=10_000.0,
        open_positions=0,
        symbol_exposure=0.0,
        total_exposure=0.0,
        daily_pnl=0.0,
        weekly_pnl=0.0,
        consecutive_losses=0,
        trades_last_hour=0,
        cooldown_until=None,
    )
    base.update(overrides)
    return RiskState(**base)


def test_a_clean_state_is_approved():
    assert evaluate_limits(make_signal(), clean_state()).approved


def test_max_positions_blocks_a_new_entry():
    settings = get_settings()
    decision = evaluate_limits(
        make_signal(), clean_state(open_positions=settings.max_positions)
    )
    assert not decision.approved
    assert decision.checks["max_positions"] is False


def test_daily_loss_limit_blocks_trading():
    settings = get_settings()
    breached = -10_000 * settings.max_daily_loss_pct / 100 - 1
    decision = evaluate_limits(make_signal(), clean_state(daily_pnl=breached))
    assert not decision.approved
    assert decision.checks["daily_loss"] is False


def test_consecutive_losses_block_trading():
    settings = get_settings()
    decision = evaluate_limits(
        make_signal(), clean_state(consecutive_losses=settings.max_consecutive_losses)
    )
    assert decision.checks["consecutive_losses"] is False


def test_cooldown_blocks_trading():
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    decision = evaluate_limits(make_signal(), clean_state(cooldown_until=future))
    assert decision.checks["cooldown"] is False


def test_exposure_caps_block_an_oversized_position():
    decision = evaluate_limits(make_signal(), clean_state(total_exposure=9_000.0))
    assert decision.checks["total_exposure"] is False


def test_a_signal_without_a_stop_is_rejected():
    signal = make_signal(stop=100.0)
    decision = evaluate_limits(signal, clean_state())
    assert not decision.approved
    assert decision.checks["stop_present"] is False


def test_shorts_are_rejected_on_spot():
    settings = get_settings()
    settings.futures_enabled = False
    signal = make_signal(side=Side.SHORT, entry=100.0, stop=100.5)
    decision = evaluate_limits(signal, clean_state(), settings)
    assert decision.checks["short_allowed"] is False


def test_position_size_risks_exactly_the_configured_fraction():
    settings = get_settings()
    settings.futures_enabled = False
    settings.risk_per_trade_pct = 0.5
    signal = make_signal(entry=100.0, stop=99.0)  # 1.0 of stop distance
    sized = size_position(signal, 10_000.0, settings)
    # 0.5% of 10,000 = 50 USDT of risk over a 1.0 stop distance -> 50 units.
    assert sized.quantity == pytest.approx(50.0)
    assert sized.risk_amount == pytest.approx(50.0)


def test_position_size_is_capped_by_spot_buying_power():
    settings = get_settings()
    settings.futures_enabled = False
    settings.risk_per_trade_pct = 0.5
    signal = make_signal(entry=100.0, stop=99.95)  # a very tight stop
    sized = size_position(signal, 1_000.0, settings)
    assert sized.notional <= 1_000.0
    assert any("capped" in note for note in sized.notes)


def test_dust_positions_are_refused():
    settings = get_settings()
    settings.risk_per_trade_pct = 0.35
    signal = make_signal(entry=100.0, stop=50.0)  # an absurdly wide stop
    with pytest.raises(SizingError):
        size_position(signal, 100.0, settings, min_notional=10.0)


def test_high_leverage_is_refused_when_liquidation_is_too_close():
    settings = get_settings()
    settings.futures_enabled = True
    settings.futures_leverage = 20
    settings.futures_max_leverage = 20
    settings.min_liquidation_distance_pct = 8.0
    try:
        with pytest.raises(SizingError, match="Liquidation"):
            size_position(make_signal(), 10_000.0, settings)
    finally:
        settings.futures_enabled = False
        settings.futures_leverage = 3
        settings.futures_max_leverage = 5


def test_liquidation_price_moves_the_right_way():
    long_liq = liquidation_price(100.0, Side.LONG, 5)
    short_liq = liquidation_price(100.0, Side.SHORT, 5)
    assert long_liq < 100.0 < short_liq
