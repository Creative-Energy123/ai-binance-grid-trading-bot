"""Encrypted storage for third-party credentials set from the dashboard.

Values live in `integration_secrets`, AES-256-GCM encrypted with the same key as
the exchange credentials. They are applied to the running settings object on
save and again at startup, and are never returned in full by the API.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import IntegrationSecret
from app.security import decrypt_secret, encrypt_secret, mask_key

logger = logging.getLogger(__name__)

# Only these settings may be written through the secrets endpoint. Anything not
# listed here cannot be set remotely, whatever the request body says.
SECRET_FIELDS: dict[str, str] = {
    "anthropic_api_key": "Anthropic API key (AI assistant)",
    "telegram_bot_token": "Telegram bot token",
    "alert_webhook_url": "Alert webhook URL",
    "smtp_password": "SMTP password",
    "binance_proxy": "Binance HTTP(S) proxy URL",
}


async def load_into_settings(db: AsyncSession) -> list[str]:
    """Apply stored secrets to the running settings. Called at startup."""
    settings = get_settings()
    applied: list[str] = []
    rows = (await db.execute(select(IntegrationSecret))).scalars().all()
    for row in rows:
        if row.name not in SECRET_FIELDS:
            continue
        try:
            setattr(settings, row.name, decrypt_secret(row.value_encrypted))
            applied.append(row.name)
        except Exception as exc:  # noqa: BLE001
            # A key rotation makes old values unreadable; that must not stop boot.
            logger.error("Could not decrypt %s: %s", row.name, exc)
    return applied


async def set_secret(db: AsyncSession, name: str, value: str) -> IntegrationSecret:
    if name not in SECRET_FIELDS:
        raise ValueError(f"{name} is not a settable integration secret")

    row = (
        await db.execute(select(IntegrationSecret).where(IntegrationSecret.name == name))
    ).scalar_one_or_none()
    if row is None:
        row = IntegrationSecret(name=name)
        db.add(row)
    row.value_encrypted = encrypt_secret(value)
    row.masked = mask_key(value) if "url" not in name and "proxy" not in name else _mask_url(value)
    setattr(get_settings(), name, value)
    await db.flush()
    return row


async def clear_secret(db: AsyncSession, name: str) -> bool:
    row = (
        await db.execute(select(IntegrationSecret).where(IntegrationSecret.name == name))
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    setattr(get_settings(), name, "")
    await db.flush()
    return True


def _mask_url(value: str) -> str:
    """Show the host of a URL but never any embedded credentials."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(value)
        if parsed.hostname:
            return f"{parsed.scheme}://{parsed.hostname}/…"
    except Exception:  # noqa: BLE001
        pass
    return mask_key(value)


async def status(db: AsyncSession) -> list[dict]:
    rows = {
        r.name: r for r in (await db.execute(select(IntegrationSecret))).scalars().all()
    }
    settings = get_settings()
    out = []
    for name, label in SECRET_FIELDS.items():
        row = rows.get(name)
        from_env = bool(getattr(settings, name, "")) and row is None
        out.append(
            {
                "name": name,
                "label": label,
                "configured": bool(row) or from_env,
                "source": "database" if row else ("environment" if from_env else "unset"),
                "masked": row.masked if row else "",
                "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            }
        )
    return out
