from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import audit, get_current_user, require_admin
from app.config import get_settings
from app.db import get_db
from app.models import ExchangeAccount, User
from app.schemas import (
    CredentialsIn,
    CredentialsOut,
    IntegrationSecretIn,
    IntegrationSecretOut,
    MessageOut,
)
from app.security import CredentialCipherError, encrypt_secret, mask_key
from app.services import secrets as secret_store
from app.services.exchange import exchange

router = APIRouter(prefix="/api/account", tags=["account"])

WITHDRAWAL_WARNING = (
    "Never enable withdrawal permission on the Binance API key used by this bot. Enable trading "
    "only, and restrict the key to this server's IP address."
)


@router.get("/credentials", response_model=list[CredentialsOut])
async def list_credentials(
    db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)
):
    rows = (
        (await db.execute(select(ExchangeAccount).where(ExchangeAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/credentials", response_model=CredentialsOut)
async def save_credentials(
    payload: CredentialsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Store API keys encrypted. Secrets are write-only: no endpoint returns them."""
    settings = get_settings()
    try:
        key_blob = encrypt_secret(payload.api_key)
        secret_blob = encrypt_secret(payload.api_secret)
    except CredentialCipherError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    row = (
        await db.execute(
            select(ExchangeAccount).where(
                ExchangeAccount.user_id == user.id, ExchangeAccount.label == payload.label
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ExchangeAccount(user_id=user.id, label=payload.label)
        db.add(row)

    row.market = payload.market
    row.environment = payload.environment
    row.api_key_encrypted = key_blob
    row.api_secret_encrypted = secret_blob
    row.api_key_masked = mask_key(payload.api_key)

    # Load into the running process so the exchange client can use them, then
    # validate against Binance and refuse keys that can withdraw.
    settings.binance_api_key = payload.api_key
    settings.binance_api_secret = payload.api_secret
    settings.futures_enabled = payload.market == "futures"
    await exchange.reset()

    validation = await exchange.validate_credentials()
    row.permissions = validation
    row.withdrawals_enabled = bool(validation.get("can_withdraw"))
    row.validated_at = datetime.now(timezone.utc) if validation.get("valid") else None

    await audit(db, user, "credentials_saved", f"label={payload.label} market={payload.market}")
    await db.commit()

    if not validation.get("valid"):
        raise HTTPException(
            status_code=400,
            detail=f"Keys stored but validation failed: {validation.get('error')}",
        )
    if row.withdrawals_enabled:
        raise HTTPException(
            status_code=400,
            detail=f"Keys stored but withdrawals are ENABLED on this key. {WITHDRAWAL_WARNING}",
        )
    return row


@router.delete("/credentials/{credential_id}", response_model=MessageOut)
async def delete_credentials(
    credential_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    row = (
        await db.execute(
            select(ExchangeAccount).where(
                ExchangeAccount.id == credential_id, ExchangeAccount.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    await db.delete(row)
    await audit(db, user, "credentials_deleted", f"id={credential_id}")
    await db.commit()
    return MessageOut(message="Credential deleted")


@router.get("/balance")
async def balance(_: User = Depends(get_current_user)) -> dict:
    if not exchange.has_credentials():
        return {"configured": False, "equity_usdt": None, "warning": WITHDRAWAL_WARNING}
    try:
        equity = await exchange.fetch_equity_usdt()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"configured": True, "equity_usdt": equity, "warning": WITHDRAWAL_WARNING}


@router.get("/integrations", response_model=list[IntegrationSecretOut])
async def list_integrations(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
):
    """Which third-party credentials are set, and where they came from. Values
    are never returned — only a masked fragment."""
    return await secret_store.status(db)


@router.post("/integrations", response_model=MessageOut)
async def set_integration(
    payload: IntegrationSecretIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        row = await secret_store.set_secret(db, payload.name, payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialCipherError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload.name == "binance_proxy":
        await exchange.reset()  # the proxy is applied when the client is built

    await audit(db, user, "integration_set", payload.name)
    await db.commit()
    return MessageOut(message=f"{payload.name} saved", data={"masked": row.masked})


@router.delete("/integrations/{name}", response_model=MessageOut)
async def delete_integration(
    name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    if name not in secret_store.SECRET_FIELDS:
        raise HTTPException(status_code=404, detail=f"{name} is not an integration secret")
    removed = await secret_store.clear_secret(db, name)
    if name == "binance_proxy":
        await exchange.reset()
    await audit(db, user, "integration_cleared", name)
    await db.commit()
    return MessageOut(message=f"{name} cleared" if removed else f"{name} was not set")
