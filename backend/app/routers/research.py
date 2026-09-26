from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_trader
from app.config import get_settings
from app.db import get_db
from app.models import Backtest, BacktestTrade, User
from app.schemas import BacktestIn, BacktestOut, BacktestSegmentOut
from app.services.backtest import BacktestResult, fetch_history, run_backtest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/research", tags=["research"])

DISCLAIMER = (
    "Backtested and simulated results are not evidence of future returns. They depend on the fee, "
    "slippage and fill assumptions shown in the metrics, and real execution will differ."
)

# Train / validation / out-of-sample split. Parameters must only ever be tuned on
# the train segment; the other two exist to detect overfitting.
SPLIT = (0.6, 0.2, 0.2)


def _segment(result: BacktestResult, name: str) -> BacktestSegmentOut:
    return BacktestSegmentOut(
        segment=name,
        candles=result.candles,
        starting_equity=result.starting_equity,
        ending_equity=round(result.ending_equity, 4),
        metrics=result.metrics,
        equity_curve=result.equity_curve[::5],  # thinned for transport
        drawdown_curve=result.drawdown_curve[::5],
        warnings=result.warnings,
    )


async def _persist(db: AsyncSession, label: str, symbol: str, name: str, result: BacktestResult) -> None:
    settings = get_settings()
    row = Backtest(
        label=label,
        symbol=symbol,
        timeframe=result.timeframe,
        segment=name,
        candles=result.candles,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        parameters={
            "minimum_signal_score": settings.minimum_signal_score,
            "risk_per_trade_pct": settings.risk_per_trade_pct,
            "stop_mode": settings.stop_mode,
            "atr_stop_multiplier": settings.atr_stop_multiplier,
            "tp1_r_multiple": settings.tp1_r_multiple,
            "tp2_r_multiple": settings.tp2_r_multiple,
            "futures_enabled": settings.futures_enabled,
        },
        metrics=result.metrics,
        equity_curve=result.equity_curve[::5],
    )
    db.add(row)
    await db.flush()
    for trade in result.trades:
        db.add(
            BacktestTrade(
                backtest_id=row.id,
                symbol=trade.symbol,
                strategy=trade.strategy,
                side=trade.side,
                entry_time=int(trade.entry_time / 1000),
                exit_time=int(trade.exit_time / 1000),
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                quantity=trade.quantity,
                fees=trade.fees,
                slippage=trade.slippage,
                pnl=trade.pnl,
                r_multiple=trade.r_multiple,
                regime=trade.regime,
                signal_score=trade.signal_score,
                exit_reason=trade.exit_reason,
            )
        )


def _overfitting_warning(segments: list[BacktestSegmentOut]) -> str | None:
    by_name = {s.segment: s.metrics for s in segments}
    train = by_name.get("train", {})
    oos = by_name.get("out_of_sample", {})
    if not train.get("total_trades") or not oos.get("total_trades"):
        return "Not enough trades in every segment to judge robustness."
    train_pf = train.get("profit_factor") or 0
    oos_pf = oos.get("profit_factor") or 0
    if train_pf > 1 and oos_pf < train_pf * 0.6:
        return (
            f"Out-of-sample profit factor ({oos_pf}) is well below the training value ({train_pf}). "
            "Treat these parameters as overfitted rather than validated."
        )
    if oos.get("net_pnl", 0) < 0 <= train.get("net_pnl", 0):
        return "The strategy is profitable in training but loses money out of sample."
    return None


@router.post("/backtest", response_model=BacktestOut)
async def backtest(
    payload: BacktestIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_trader),
):
    settings = get_settings()
    try:
        candles = await asyncio.wait_for(
            fetch_history(payload.symbol, settings.timeframe_entry, payload.candles), timeout=120
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="History download timed out") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if len(candles) < 600:
        raise HTTPException(
            status_code=400,
            detail=f"Only {len(candles)} candles available; at least 600 are needed for a run",
        )

    label = f"{payload.symbol} {datetime.now(timezone.utc):%Y-%m-%d %H:%M}"
    segments: list[BacktestSegmentOut] = []

    if payload.split:
        n = len(candles)
        train_end = int(n * SPLIT[0])
        val_end = train_end + int(n * SPLIT[1])
        slices = [
            ("train", candles[:train_end]),
            ("validation", candles[train_end - 220 : val_end]),
            ("out_of_sample", candles[val_end - 220 :]),
        ]
    else:
        slices = [("full", candles)]

    for name, data in slices:
        result = await asyncio.to_thread(
            run_backtest, payload.symbol, data, settings, payload.starting_equity
        )
        await _persist(db, label, payload.symbol, name, result)
        segments.append(_segment(result, name))

    await db.commit()
    return BacktestOut(
        symbol=payload.symbol,
        segments=segments,
        overfitting_warning=_overfitting_warning(segments) if payload.split else None,
        disclaimer=DISCLAIMER,
    )


@router.get("/backtests")
async def list_backtests(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    rows = (
        (await db.execute(select(Backtest).order_by(Backtest.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "label": r.label,
            "symbol": r.symbol,
            "segment": r.segment,
            "candles": r.candles,
            "metrics": r.metrics,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
