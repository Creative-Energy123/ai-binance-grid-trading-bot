from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_current_user, verify_password
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.schemas import TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account locked until {user.locked_until.isoformat()}",
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
    await db.commit()
    token = create_access_token(user.email)
    return TokenResponse(access_token=token)


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {"email": user.email, "id": user.id}
