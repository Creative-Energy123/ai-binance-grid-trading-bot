from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_alert(message: str) -> None:
    """Telegram and/or generic webhook alerts. Failures are logged, never raised."""
    if settings.telegram_bot_token and settings.telegram_chat_id:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    url,
                    json={"chat_id": settings.telegram_chat_id, "text": message[:3500]},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram alert failed: %s", exc)

    if settings.alert_webhook_url:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(settings.alert_webhook_url, json={"text": message})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Webhook alert failed: %s", exc)


def format_alert(title: str, detail: Optional[str] = None) -> str:
    base = f"[GridBot] {title}"
    if detail:
        return f"{base}\n{detail}"
    return base
