"""Alerting — Telegram, email and generic webhook, plus a persisted alert feed
the dashboard renders as browser notifications.

Delivery failures are recorded but never propagate: an alert must not be able to
take the trading engine down.
"""

from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Alert

logger = logging.getLogger(__name__)

CRITICAL_EVENTS = {
    "emergency_stop",
    "daily_loss_limit",
    "stop_order_failed",
    "api_failure",
    "websocket_failure",
    "position_mismatch",
    "consecutive_loss_halt",
}


async def _send_telegram(text: str) -> None:
    settings = get_settings()
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url, json={"chat_id": settings.telegram_chat_id, "text": text[:3500]}
        )
        response.raise_for_status()


async def _send_webhook(title: str, body: str) -> None:
    settings = get_settings()
    if not settings.alert_webhook_url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            settings.alert_webhook_url, json={"title": title, "text": body}
        )
        response.raise_for_status()


def _send_email_sync(title: str, body: str) -> None:
    import smtplib

    settings = get_settings()
    if not (settings.smtp_host and settings.alert_email_to):
        return
    message = EmailMessage()
    message["Subject"] = f"[{settings.app_name}] {title}"
    message["From"] = settings.smtp_user or "bot@localhost"
    message["To"] = settings.alert_email_to
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def notify(
    db: AsyncSession | None,
    event: str,
    title: str,
    body: str = "",
    severity: str = "info",
) -> None:
    settings = get_settings()
    text = f"[{settings.app_name}] {title}\n{body}".strip()
    errors: list[str] = []

    for channel, coro in (
        ("telegram", _send_telegram(text)),
        ("webhook", _send_webhook(title, body)),
    ):
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 - alerting must never raise
            errors.append(f"{channel}: {exc}")
            logger.warning("%s alert failed: %s", channel, exc)

    if settings.smtp_host and (severity == "critical" or event in CRITICAL_EVENTS):
        try:
            await asyncio.to_thread(_send_email_sync, title, body)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"email: {exc}")
            logger.warning("Email alert failed: %s", exc)

    if db is not None:
        db.add(
            Alert(
                channel="multi",
                event=event,
                title=title[:160],
                body=body,
                delivered=not errors,
                error="; ".join(errors),
            )
        )
        await db.flush()
