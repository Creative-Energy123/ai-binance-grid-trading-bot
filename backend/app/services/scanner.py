"""Market scanner — per-symbol regime, signal and liquidity snapshot for the dashboard."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.services import signals
from app.services.market_data import market_data

logger = logging.getLogger(__name__)

# A wide spread eats a scalp before it starts.
MAX_ACCEPTABLE_SPREAD_PCT = 0.05


async def scan_symbol(symbol: str) -> dict:
    try:
        ctx = await market_data.build_context(symbol, with_spread=True)
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "error": str(exc)}

    evaluation = signals.evaluate(ctx)
    setup = ctx.setup
    liquid = ctx.spread_pct is None or ctx.spread_pct <= MAX_ACCEPTABLE_SPREAD_PCT

    return {
        "symbol": symbol,
        "price": ctx.price,
        "regime": ctx.regime.regime.value,
        "regime_confidence": round(ctx.regime.confidence, 2),
        "tradable": ctx.regime.tradable and liquid,
        "signal": evaluation.signal.side.value if evaluation.signal else None,
        "strategy": evaluation.signal.strategy if evaluation.signal else None,
        "score": round(evaluation.best_score, 1),
        "rejected_reason": evaluation.rejected_reason,
        "rsi": round(setup.rsi, 1) if setup.rsi else None,
        "adx": round(setup.adx, 1) if setup.adx else None,
        "atr_pct": round(setup.atr_pct, 3) if setup.atr_pct else None,
        "volume_ratio": round(setup.volume_ratio, 2) if setup.volume_ratio else None,
        "trend": ctx.regime.trend_bias,
        "spread_pct": round(ctx.spread_pct, 4) if ctx.spread_pct is not None else None,
        "liquid": liquid,
        "strategies_enabled": {
            "trend": ctx.regime.allow_trend_strategy,
            "breakout": ctx.regime.allow_breakout_strategy,
            "range": ctx.regime.allow_range_strategy,
            "long": ctx.regime.allow_long,
            "short": ctx.regime.allow_short and get_settings().futures_enabled,
        },
    }


async def scan(symbols: list[str] | None = None) -> list[dict]:
    symbols = symbols or get_settings().symbol_list
    results = await asyncio.gather(*(scan_symbol(s) for s in symbols))
    return list(results)
