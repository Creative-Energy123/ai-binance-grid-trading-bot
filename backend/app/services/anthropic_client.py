from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


ANALYSIS_SCHEMA_HINT = """
Return ONLY valid JSON with keys:
trend (string),
volatility_regime (string: low|medium|high),
suggested_lower (number|null),
suggested_upper (number|null),
suggested_grid_count (integer|null),
risk_warnings (string),
commentary (string)
"""


def _api_key_configured(raw: str | None) -> bool:
    return bool(raw and raw.strip())


async def analyze_market(
    symbol: str,
    ohlcv: list[list],
    current_lower: float,
    current_upper: float,
    grid_count: int,
    realized_pnl: float,
) -> dict[str, Any]:
    """Call Anthropic Claude for advisory grid analysis. Never places orders."""
    settings = get_settings()
    if not _api_key_configured(settings.anthropic_api_key):
        closes = [c[4] for c in ohlcv[-20:]] if ohlcv else []
        vol = "medium"
        if len(closes) >= 2:
            span = max(closes) - min(closes)
            mid = sum(closes) / len(closes)
            pct = (span / mid) * 100 if mid else 0
            vol = "low" if pct < 1.5 else "high" if pct > 4 else "medium"
        logger.warning(
            "ANTHROPIC_API_KEY missing/empty — returning heuristic placeholder (key_len=%s)",
            len(settings.anthropic_api_key or ""),
        )
        return {
            "trend": "unknown (AI key not configured)",
            "volatility_regime": vol,
            "suggested_lower": current_lower,
            "suggested_upper": current_upper,
            "suggested_grid_count": grid_count,
            "risk_warnings": "Anthropic API key missing — heuristic placeholder only.",
            "commentary": "Configure ANTHROPIC_API_KEY for real analysis.",
            "_source": "heuristic",
        }

    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.strip())
    recent = ohlcv[-48:]
    prompt = (
        f"You are a risk-aware crypto grid trading advisor for {symbol}.\n"
        f"Current grid: lower={current_lower}, upper={current_upper}, grids={grid_count}.\n"
        f"Realized PnL (USDT): {realized_pnl}.\n"
        f"Recent OHLCV (ts,o,h,l,c,v) sample:\n{json.dumps(recent[-24:])}\n"
        f"{ANALYSIS_SCHEMA_HINT}\n"
        "Do not recommend increasing capital. Prefer conservative ranges in trends."
    )
    try:
        logger.info(
            "Calling Anthropic model=%s key_configured=True key_len=%s",
            settings.anthropic_model,
            len(settings.anthropic_api_key.strip()),
        )
        msg = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in msg.content if hasattr(block, "text"))
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
            data["_source"] = "anthropic"
            return data
        logger.warning("AI response not JSON: %s", text[:200])
        return {
            "trend": "error",
            "volatility_regime": "unknown",
            "suggested_lower": None,
            "suggested_upper": None,
            "suggested_grid_count": None,
            "risk_warnings": "AI returned non-JSON; keep current grid and review manually.",
            "commentary": text[:300],
            "_source": "error",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Anthropic analysis failed: %s", exc)
        err = str(exc)
        if "not_found_error" in err or "model:" in err.lower():
            detail = (
                f"Anthropic model unavailable ({settings.anthropic_model}). "
                "Set ANTHROPIC_MODEL to a current model id (e.g. claude-sonnet-4-6)."
            )
        elif "authentication" in err.lower() or "401" in err:
            detail = "Anthropic authentication failed — check ANTHROPIC_API_KEY."
        else:
            detail = f"AI analysis failed ({type(exc).__name__}); keep current grid and review manually."
        return {
            "trend": "error",
            "volatility_regime": "unknown",
            "suggested_lower": None,
            "suggested_upper": None,
            "suggested_grid_count": None,
            "risk_warnings": detail,
            "commentary": err[:300],
            "_source": "error",
        }
