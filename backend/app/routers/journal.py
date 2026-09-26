from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_db
from app.models import Alert, RiskEvent, Signal, Trade, User
from app.schemas import AlertOut, RiskEventOut, SignalOut, TradeOut
from app.services import portfolio

router = APIRouter(prefix="/api/journal", tags=["journal"])

CSV_COLUMNS = [
    "id",
    "closed_at",
    "symbol",
    "strategy",
    "side",
    "mode",
    "regime",
    "signal_score",
    "entry_price",
    "exit_price",
    "stop_price",
    "tp_price",
    "quantity",
    "risk_amount",
    "fees",
    "slippage",
    "funding_fees",
    "pnl",
    "pnl_pct",
    "r_multiple",
    "duration_seconds",
    "entry_reason",
    "exit_reason",
]


@router.get("/trades", response_model=list[TradeOut])
async def trades(
    limit: int = Query(default=200, ge=1, le=2000),
    symbol: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Trade).order_by(Trade.closed_at.desc()).limit(limit)
    if symbol:
        query = query.where(Trade.symbol == symbol)
    return list((await db.execute(query)).scalars().all())


@router.get("/trades.csv")
async def trades_csv(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    rows = list(
        (await db.execute(select(Trade).order_by(Trade.closed_at.desc()))).scalars().all()
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for trade in rows:
        writer.writerow([getattr(trade, column, "") for column in CSV_COLUMNS])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="trade-journal.csv"'},
    )


@router.get("/signals", response_model=list[SignalOut])
async def signals_feed(
    limit: int = Query(default=100, ge=1, le=1000),
    accepted: bool | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    if accepted is not None:
        query = query.where(Signal.accepted == accepted)
    return list((await db.execute(query)).scalars().all())


@router.get("/risk-events", response_model=list[RiskEventOut])
async def risk_events(
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit)
    return list((await db.execute(query)).scalars().all())


@router.get("/alerts", response_model=list[AlertOut])
async def alerts(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    return list((await db.execute(query)).scalars().all())


@router.get("/performance")
async def performance(
    mode: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    perf = await portfolio.performance(db, mode=mode)
    return perf.as_dict()
