from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    as_utc,
    audit,
    consume_refresh_token,
    create_access_token,
    get_current_user,
    issue_refresh_token,
    verify_password,
)
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.schemas import RefreshRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    settings = get_settings()
    user = (
        await db.execute(select(User).where(User.email == form_data.username))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)

    locked_until = as_utc(user.locked_until) if user else None
    if locked_until and locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account locked until {locked_until.isoformat()}",
        )

    if user is None or not verify_password(form_data.password, user.password_hash):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= settings.max_login_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_logins = 0
            await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.failed_logins = 0
    user.locked_until = None
    refresh = await issue_refresh_token(db, user)
    await audit(db, user, "login")
    await db.commit()
    return TokenResponse(
        access_token=create_access_token(user.email, user.role), refresh_token=refresh
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    payload: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    user = await consume_refresh_token(db, payload.refresh_token)
    new_refresh = await issue_refresh_token(db, user)
    await db.commit()
    return TokenResponse(
        access_token=create_access_token(user.email, user.role), refresh_token=new_refresh
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
