from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import AuditLog, RefreshToken, Role, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ROLE_ORDER = {Role.VIEWER.value: 0, Role.TRADER.value: 1, Role.ADMIN.value: 2}


def as_utc(value: datetime | None) -> datetime | None:
    """Timestamps read back from the database can be naive (SQLite, and any
    column stored without a timezone); compare them as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": subject, "role": role, "type": "access", "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh_token(db: AsyncSession, user: User) -> str:
    settings = get_settings()
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    await db.flush()
    return raw


async def consume_refresh_token(db: AsyncSession, raw: str) -> User:
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw)))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    expires_at = as_utc(row.expires_at) if row else None
    if row is None or row.revoked or (expires_at and expires_at <= now):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    row.revoked = True  # rotate on every use
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    await db.flush()
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    settings = get_settings()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        email: str | None = payload.get("sub")
        if not email or payload.get("type") != "access":
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_role(minimum: Role) -> Callable:
    """Dependency factory: viewer < trader < admin."""

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if ROLE_ORDER.get(user.role, 0) < ROLE_ORDER[minimum.value]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {minimum.value} role",
            )
        return user

    return dependency


require_trader = require_role(Role.TRADER)
require_admin = require_role(Role.ADMIN)


async def audit(db: AsyncSession, user: User | None, action: str, detail: str = "") -> None:
    db.add(
        AuditLog(
            actor="user" if user else "system",
            actor_email=user.email if user else "",
            action=action,
            detail=detail,
        )
    )
    await db.flush()


async def ensure_admin_user(db: AsyncSession) -> None:
    settings = get_settings()
    user = (
        await db.execute(select(User).where(User.email == settings.admin_email))
    ).scalar_one_or_none()
    if user is None:
        db.add(
            User(
                email=settings.admin_email,
                password_hash=hash_password(settings.admin_password),
                role=Role.ADMIN.value,
            )
        )
        await db.commit()
