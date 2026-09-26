"""AI trading assistant — explanation only.

The assistant reads market state, signals, risk decisions and recent performance
and explains them in plain language. It has no tools, no order placement path and
no ability to change configuration: the dashboard sends its text straight to the
user. The system prompt forbids profit claims.
"""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AiAnalysis, RiskEvent, Signal, Trade
from app.services import portfolio, scanner

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the analysis assistant inside a Binance scalping bot.

Your job is to explain what the bot is doing and why, using only the JSON state
you are given. Rules you must follow:

- You never place, approve, size or cancel trades. If asked to, say that order
  placement is deliberately outside your reach and point to the dashboard controls.
- You never claim a trade will be profitable and never imply any outcome is
  guaranteed. Describe probabilities and conditions, not promises.
- Distinguish clearly between what the data shows and what is interpretation.
- If the bot declined to trade, explain that doing nothing is a valid, intended
  outcome and name the specific gate that blocked it.
- Be concise and concrete. Quote the actual numbers from the state.
- If the state does not contain something you were asked about, say so."""


class AiUnavailable(RuntimeError):
    pass


def _client() -> AsyncAnthropic:
    settings = get_settings()
    if not settings.ai_enabled:
        raise AiUnavailable("The AI assistant is disabled (AI_ENABLED=false).")
    if not settings.anthropic_api_key.strip():
        raise AiUnavailable("ANTHROPIC_API_KEY is not configured.")
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


async def gather_state(db: AsyncSession, symbol: str | None = None) -> dict:
    settings = get_settings()
    bot = await portfolio.get_bot(db)
    perf = await portfolio.performance(db, mode=bot.mode)
    positions = await portfolio.open_positions(db)

    recent_signals = list(
        (await db.execute(select(Signal).order_by(Signal.created_at.desc()).limit(10)))
        .scalars()
        .all()
    )
    recent_trades = list(
        (await db.execute(select(Trade).order_by(Trade.closed_at.desc()).limit(10))).scalars().all()
    )
    recent_risk = list(
        (await db.execute(select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(10)))
        .scalars()
        .all()
    )
    market = await scanner.scan([symbol] if symbol else None)

    return {
        "mode": bot.mode,
        "bot": {
            "running": bot.running,
            "paused": bot.paused,
            "emergency_stopped": bot.emergency_stopped,
            "status": bot.status_message,
            "equity": bot.equity,
            "consecutive_losses": bot.consecutive_losses,
        },
        "risk_configuration": {
            "risk_per_trade_pct": settings.risk_per_trade_pct,
            "max_daily_loss_pct": settings.max_daily_loss_pct,
            "max_positions": settings.max_positions,
            "max_consecutive_losses": settings.max_consecutive_losses,
            "minimum_signal_score": settings.minimum_signal_score,
            "futures_enabled": settings.futures_enabled,
            "leverage": settings.futures_leverage if settings.futures_enabled else 1,
        },
        "cost_assumptions": {
            "taker_fee_pct": settings.taker_fee_pct,
            "slippage_pct": settings.slippage_pct,
            "round_trip_cost_pct": settings.round_trip_cost_pct,
        },
        "market": market,
        "open_positions": [
            {
                "symbol": p.symbol,
                "side": p.side,
                "entry": p.entry_price,
                "stop": p.stop_price,
                "tp1": p.tp1_price,
                "tp2": p.tp2_price,
                "quantity": p.remaining_quantity,
                "unrealized_pnl": p.unrealized_pnl,
                "strategy": p.strategy,
                "score": p.signal_score,
            }
            for p in positions
        ],
        "recent_signals": [
            {
                "symbol": s.symbol,
                "side": s.side,
                "strategy": s.strategy,
                "score": s.score,
                "accepted": s.accepted,
                "rejection_reason": s.rejection_reason,
                "reasons": s.reasons,
                "at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in recent_signals
        ],
        "recent_trades": [
            {
                "symbol": t.symbol,
                "side": t.side,
                "pnl": t.pnl,
                "r_multiple": t.r_multiple,
                "exit_reason": t.exit_reason,
                "strategy": t.strategy,
            }
            for t in recent_trades
        ],
        "recent_risk_events": [
            {"type": e.event_type, "severity": e.severity, "detail": e.detail} for e in recent_risk
        ],
        "performance": perf.as_dict(),
    }


async def ask(db: AsyncSession, question: str, symbol: str | None = None) -> AiAnalysis:
    settings = get_settings()
    client = _client()
    state = await gather_state(db, symbol)

    import json

    try:
        response = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1400,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Bot state (JSON):\n{json.dumps(state, default=str)[:60000]}\n\n"
                        f"Question: {question}"
                    ),
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - surface the real reason to the operator
        raise AiUnavailable(f"Anthropic request failed: {exc}") from exc

    text = "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    record = AiAnalysis(
        symbol=symbol or "",
        question=question,
        summary=text,
        raw_json={"state_keys": sorted(state.keys())},
        model=settings.anthropic_model,
    )
    db.add(record)
    await db.flush()
    return record


async def explain_symbol(db: AsyncSession, symbol: str) -> AiAnalysis:
    return await ask(
        db,
        (
            f"Explain the current situation for {symbol}: the market regime, which strategies are "
            "enabled or disabled and why, whether there is a signal, the score breakdown, the "
            "risk/reward after fees, and — if no trade was taken — exactly which gate stopped it."
        ),
        symbol=symbol,
    )
