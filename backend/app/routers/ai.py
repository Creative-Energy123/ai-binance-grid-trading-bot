from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db import get_db
from app.models import AiAnalysis, User
from app.schemas import AiAnalysisOut, AiAskIn
from app.services.ai_assistant import AiUnavailable, ask, explain_symbol

router = APIRouter(prefix="/api/ai", tags=["ai"])

NOTICE = (
    "The assistant explains the bot's own state. It cannot place, size or cancel orders, and it "
    "does not predict outcomes."
)


@router.post("/ask", response_model=AiAnalysisOut)
async def ask_assistant(
    payload: AiAskIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        record = await ask(db, payload.question, payload.symbol)
    except AiUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await db.commit()
    return record


@router.post("/explain/{symbol:path}", response_model=AiAnalysisOut)
async def explain(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        record = await explain_symbol(db, symbol)
    except AiUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await db.commit()
    return record


@router.get("/history", response_model=list[AiAnalysisOut])
async def history(
    limit: int = 25,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    rows = (
        (await db.execute(select(AiAnalysis).order_by(AiAnalysis.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/notice")
async def notice() -> dict:
    return {"notice": NOTICE}
