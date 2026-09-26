from __future__ import annotations

import pytest

from app.services.indicators import (
    adx,
    atr,
    bollinger,
    compute_snapshot,
    ema,
    macd,
    rsi,
    sma,
    vwap,
)
from tests.conftest import synth_candles


def test_sma_matches_manual_average():
    values = [1, 2, 3, 4, 5]
    assert sma(values, 3)[-1] == pytest.approx(4.0)
    assert sma(values, 3)[1] is None


def test_ema_is_seeded_with_sma_and_tracks_price():
    values = [float(i) for i in range(1, 30)]
    series = ema(values, 5)
    assert series[3] is None
    assert series[4] == pytest.approx(3.0)  # sma of 1..5
    # An EMA lags a linear ramp by roughly (period - 1) / 2 candles.
    assert series[-1] == pytest.approx(values[-1] - 2, rel=0.02)


def test_rsi_is_100_for_a_monotonic_rise_and_low_for_a_fall():
    rising = [float(i) for i in range(1, 60)]
    falling = list(reversed(rising))
    assert rsi(rising, 14)[-1] == pytest.approx(100.0)
    assert rsi(falling, 14)[-1] < 5


def test_macd_histogram_is_line_minus_signal():
    values = [float(i % 20) for i in range(200)]
    line, signal, hist = macd(values)
    assert hist[-1] == pytest.approx(line[-1] - signal[-1])


def test_atr_is_positive_and_bounded_by_range():
    candles = synth_candles(200)
    value = atr(candles, 14)[-1]
    assert value is not None and value > 0


def test_adx_is_high_in_a_strong_trend_and_low_in_chop():
    trend = adx(synth_candles(400, drift=0.002, noise=0.0015), 14)[-1]
    chop = adx(synth_candles(400, drift=0.0, wave=0.002, noise=0.004), 14)[-1]
    assert trend is not None and chop is not None
    assert trend > chop


def test_bollinger_bands_bracket_the_mean():
    values = [float(i % 10) for i in range(100)]
    upper, mid, lower = bollinger(values, 20)
    assert lower[-1] < mid[-1] < upper[-1]


def test_vwap_sits_inside_the_price_range():
    candles = synth_candles(120)
    value = vwap(candles, 60)
    window = candles[-60:]
    assert min(c[3] for c in window) <= value <= max(c[2] for c in window)


def test_snapshot_is_not_ready_without_enough_history():
    assert compute_snapshot("5m", synth_candles(10)).ready is False


def test_snapshot_populates_every_component():
    snap = compute_snapshot("5m", synth_candles(300, drift=0.001))
    assert snap.ready
    for field in ("ema9", "ema21", "ema50", "rsi", "macd", "adx", "atr", "bb_upper", "vwap"):
        assert getattr(snap, field) is not None, field


def test_indicators_never_look_ahead():
    """A value computed on a prefix must equal the value computed on the full
    series at that same index."""
    candles = synth_candles(300)
    closes = [c[4] for c in candles]
    full = ema(closes, 21)
    prefix = ema(closes[:200], 21)
    assert prefix[-1] == pytest.approx(full[199])
