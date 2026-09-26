"""Pure-function technical indicators over OHLCV series.

Every function takes plain lists and returns plain lists/floats so the same code
runs identically in the live engine and in the backtester. No look-ahead: a value
at index i only depends on candles 0..i.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt

Candle = list[float]  # [timestamp_ms, open, high, low, close, volume]


def closes(candles: list[Candle]) -> list[float]:
    return [float(c[4]) for c in candles]


def highs(candles: list[Candle]) -> list[float]:
    return [float(c[2]) for c in candles]


def lows(candles: list[Candle]) -> list[float]:
    return [float(c[3]) for c in candles]


def volumes(candles: list[Candle]) -> list[float]:
    return [float(c[5]) for c in candles]


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rma(values: list[float], period: int) -> list[float | None]:
    """Wilder smoothing, used by RSI/ATR/ADX."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    if len(values) < period + 1:
        return [None] * len(values)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = rma(gains, period)
    avg_loss = rma(losses, period)
    out: list[float | None] = [None]
    for g, loss in zip(avg_gain, avg_loss):
        if g is None or loss is None:
            out.append(None)
        elif loss == 0:
            out.append(100.0)
        else:
            out.append(100 - (100 / (1 + g / loss)))
    return out


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_line = ema(values, fast)
    slow_line = ema(values, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_line, slow_line)
    ]
    defined = [v for v in macd_line if v is not None]
    signal_vals = ema(defined, signal)
    signal_line: list[float | None] = [None] * len(macd_line)
    offset = len(macd_line) - len(defined)
    for i, v in enumerate(signal_vals):
        signal_line[offset + i] = v
    hist: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def true_range(candles: list[Candle]) -> list[float]:
    if not candles:
        return []
    tr = [float(candles[0][2]) - float(candles[0][3])]
    for i in range(1, len(candles)):
        high, low = float(candles[i][2]), float(candles[i][3])
        prev_close = float(candles[i - 1][4])
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return tr


def atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    return rma(true_range(candles), period)


def adx(candles: list[Candle], period: int = 14) -> list[float | None]:
    n = len(candles)
    if n < period * 2:
        return [None] * n
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, n):
        up = float(candles[i][2]) - float(candles[i - 1][2])
        down = float(candles[i - 1][3]) - float(candles[i][3])
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    tr_s = rma(true_range(candles), period)
    plus_s = rma(plus_dm, period)
    minus_s = rma(minus_dm, period)

    dx: list[float] = []
    dx_index: list[int] = []
    for i in range(n):
        t, p, m = tr_s[i], plus_s[i], minus_s[i]
        if not t or p is None or m is None:
            continue
        pdi = 100 * p / t
        mdi = 100 * m / t
        denom = pdi + mdi
        dx.append(0.0 if denom == 0 else 100 * abs(pdi - mdi) / denom)
        dx_index.append(i)

    out: list[float | None] = [None] * n
    for pos, value in enumerate(rma(dx, period)):
        if value is not None:
            out[dx_index[pos]] = value
    return out


def directional_index(candles: list[Candle], period: int = 14) -> tuple[float | None, float | None]:
    """Latest (+DI, -DI)."""
    n = len(candles)
    if n < period + 1:
        return None, None
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, n):
        up = float(candles[i][2]) - float(candles[i - 1][2])
        down = float(candles[i - 1][3]) - float(candles[i][3])
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    tr_s = rma(true_range(candles), period)[-1]
    p = rma(plus_dm, period)[-1]
    m = rma(minus_dm, period)[-1]
    if not tr_s or p is None or m is None:
        return None, None
    return 100 * p / tr_s, 100 * m / tr_s


def bollinger(
    values: list[float], period: int = 20, std_mult: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        mean = mid[i]
        if mean is None:
            continue
        window = values[i - period + 1 : i + 1]
        sd = sqrt(sum((v - mean) ** 2 for v in window) / period)
        upper[i] = mean + std_mult * sd
        lower[i] = mean - std_mult * sd
    return upper, mid, lower


def vwap(candles: list[Candle], period: int = 60) -> float | None:
    """Rolling volume-weighted average price over the last `period` candles."""
    window = candles[-period:]
    if not window:
        return None
    pv = 0.0
    vol = 0.0
    for c in window:
        typical = (float(c[2]) + float(c[3]) + float(c[4])) / 3
        v = float(c[5])
        pv += typical * v
        vol += v
    if vol <= 0:
        return None
    return pv / vol


def swing_low(candles: list[Candle], lookback: int = 20) -> float | None:
    return min((float(c[3]) for c in candles[-lookback:]), default=None)


def swing_high(candles: list[Candle], lookback: int = 20) -> float | None:
    return max((float(c[2]) for c in candles[-lookback:]), default=None)


@dataclass
class IndicatorSnapshot:
    """All indicator values for the most recent closed candle of one timeframe."""

    timeframe: str
    close: float
    ema9: float | None = None
    ema21: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    macd_hist_prev: float | None = None
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    bb_width_pct: float | None = None
    volume: float | None = None
    volume_ma: float | None = None
    volume_ratio: float | None = None
    vwap: float | None = None
    swing_high: float | None = None
    swing_low: float | None = None
    ready: bool = False
    candles: list[Candle] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "candles"}


def compute_snapshot(
    timeframe: str, candles: list[Candle], structure_lookback: int = 20
) -> IndicatorSnapshot:
    """Indicator snapshot for the last candle. The caller passes only *closed*
    candles when look-ahead must be avoided (the engine and backtester both do)."""
    c = closes(candles)
    snap = IndicatorSnapshot(timeframe=timeframe, close=c[-1] if c else 0.0, candles=candles)
    if len(candles) < 30:
        return snap

    snap.ema9 = ema(c, 9)[-1]
    snap.ema21 = ema(c, 21)[-1]
    snap.ema50 = ema(c, 50)[-1] if len(c) >= 50 else None
    snap.ema200 = ema(c, 200)[-1] if len(c) >= 200 else None
    snap.rsi = rsi(c, 14)[-1]

    macd_line, signal_line, hist = macd(c)
    snap.macd, snap.macd_signal, snap.macd_hist = macd_line[-1], signal_line[-1], hist[-1]
    snap.macd_hist_prev = hist[-2] if len(hist) > 1 else None

    snap.adx = adx(candles, 14)[-1]
    snap.plus_di, snap.minus_di = directional_index(candles, 14)

    snap.atr = atr(candles, 14)[-1]
    if snap.atr and snap.close:
        snap.atr_pct = 100 * snap.atr / snap.close

    upper, mid, lower = bollinger(c, 20, 2.0)
    snap.bb_upper, snap.bb_mid, snap.bb_lower = upper[-1], mid[-1], lower[-1]
    if snap.bb_upper and snap.bb_lower and snap.bb_mid:
        snap.bb_width_pct = 100 * (snap.bb_upper - snap.bb_lower) / snap.bb_mid

    vols = volumes(candles)
    snap.volume = vols[-1]
    snap.volume_ma = sma(vols, 20)[-1]
    if snap.volume_ma:
        snap.volume_ratio = snap.volume / snap.volume_ma

    snap.vwap = vwap(candles, 60)
    snap.swing_high = swing_high(candles, structure_lookback)
    snap.swing_low = swing_low(candles, structure_lookback)
    snap.ready = snap.ema21 is not None and snap.atr is not None and snap.rsi is not None
    return snap
