from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("CREDENTIALS_ENCRYPTION_KEY", "")

MINUTE_MS = 60_000


def synth_candles(
    count: int = 1200,
    start_price: float = 30_000.0,
    drift: float = 0.0,
    noise: float = 0.0006,
    wave: float = 0.0,
    wave_period: int = 12,
    seed: int = 7,
) -> list[list[float]]:
    """Deterministic OHLCV series.

    `drift` is the per-candle return, `wave` adds a mean-reverting sine oscillation of `wave_period` candles (for ranges)
    and `noise` sets the random component.
    """
    rng = random.Random(seed)
    price = start_price
    candles: list[list[float]] = []
    for i in range(count):
        open_price = price
        if wave:
            # Mean-reverting oscillation around a fixed level, so the series
            # genuinely ranges instead of drifting.
            centre = start_price * (1 + wave * math.sin(i / wave_period))
            close = max(price + (centre - price) * 0.3 + price * rng.gauss(0, noise), 1e-6)
        else:
            close = max(price * (1 + drift + rng.gauss(0, noise)), 1e-6)
        high = max(open_price, close) * (1 + abs(rng.gauss(0, noise)))
        low = min(open_price, close) * (1 - abs(rng.gauss(0, noise)))
        volume = 100 + rng.random() * 50
        candles.append([i * MINUTE_MS, open_price, high, low, close, volume])
        price = close
    return candles


@pytest.fixture
def trending_up() -> list[list[float]]:
    return synth_candles(drift=0.0004, noise=0.0004)


@pytest.fixture
def ranging() -> list[list[float]]:
    return synth_candles(drift=0.0, wave=0.0009, noise=0.0002)


@pytest.fixture
def settings():
    from app.config import get_settings

    s = get_settings()
    s.trading_mode = "paper"
    s.futures_enabled = False
    return s
