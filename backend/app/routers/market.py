from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.services import scanner, signals
from app.services.market_data import market_data

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/scan")
async def scan(_: User = Depends(get_current_user)) -> list[dict]:
    return await scanner.scan()


@router.get("/candles")
async def candles(
    symbol: str = Query(default="BTC/USDT"),
    timeframe: str = Query(default="5m"),
    limit: int = Query(default=300, ge=50, le=1000),
    _: User = Depends(get_current_user),
) -> list[dict]:
    try:
        raw = await market_data.candles(symbol, timeframe, limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        {"time": int(c[0] / 1000), "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
        for c in raw
    ]


@router.get("/analysis/{symbol:path}")
async def analysis(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Full decision trace for one symbol: indicators, regime, score, verdict."""
    settings = get_settings()
    try:
        ctx = await market_data.build_context(symbol, with_spread=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    evaluation = signals.evaluate(ctx, settings)
    return {
        "symbol": symbol,
        "price": ctx.price,
        "spread_pct": ctx.spread_pct,
        "timeframes": {
            "primary": ctx.primary.as_dict(),
            "setup": ctx.setup.as_dict(),
            "entry": ctx.entry.as_dict(),
        },
        "regime": ctx.regime.as_dict(),
        "evaluation": {
            "tradable": evaluation.tradable,
            "best_score": round(evaluation.best_score, 2),
            "minimum_score": settings.minimum_signal_score,
            "rejected_reason": evaluation.rejected_reason,
            "candidates": evaluation.candidates,
            "signal": evaluation.signal.as_dict() if evaluation.signal else None,
        },
    }
