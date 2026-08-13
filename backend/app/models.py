from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Actor(str, Enum):
    SYSTEM = "system"
    USER = "user"
    AI = "ai"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GridConfig(Base):
    __tablename__ = "grid_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), default="BTC/USDT")
    lower_price: Mapped[float] = mapped_column(Float)
    upper_price: Mapped[float] = mapped_column(Float)
    grid_count: Mapped[int] = mapped_column(Integer, default=10)
    grid_type: Mapped[str] = mapped_column(String(16), default="arithmetic")  # arithmetic|geometric
    capital_usdt: Mapped[float] = mapped_column(Float, default=100.0)
    take_profit_pct: Mapped[float] = mapped_column(Float, default=0.5)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=10.0)
    daily_loss_limit_usdt: Mapped[float] = mapped_column(Float, default=50.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BotState(Base):
    __tablename__ = "bot_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    killed: Mapped[bool] = mapped_column(Boolean, default=False)
    live_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status_message: Mapped[str] = mapped_column(String(255), default="idle")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GridLevel(Base):
    __tablename__ = "grid_levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("grid_configs.id"))
    level_index: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(8), default="buy")  # buy|sell
    order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|open|filled|cancelled
    quantity: Mapped[float] = mapped_column(Float, default=0.0)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(16), default=Actor.SYSTEM.value)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trend: Mapped[str] = mapped_column(String(64), default="")
    volatility_regime: Mapped[str] = mapped_column(String(64), default="")
    suggested_lower: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    suggested_upper: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    suggested_grid_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    risk_warnings: Mapped[str] = mapped_column(Text, default="")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
