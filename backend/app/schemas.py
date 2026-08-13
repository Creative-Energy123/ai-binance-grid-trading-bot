from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GridConfigIn(BaseModel):
    symbol: str = "BTC/USDT"
    lower_price: float = Field(gt=0)
    upper_price: float = Field(gt=0)
    grid_count: int = Field(default=10, ge=2, le=100)
    grid_type: str = Field(default="arithmetic", pattern="^(arithmetic|geometric)$")
    capital_usdt: float = Field(gt=0)
    take_profit_pct: float = Field(default=0.5, ge=0)
    stop_loss_pct: float = Field(default=5.0, ge=0)
    max_drawdown_pct: float = Field(default=10.0, ge=0)
    daily_loss_limit_usdt: float = Field(default=50.0, ge=0)


class GridConfigOut(GridConfigIn):
    id: int
    is_active: bool
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class BotStatusOut(BaseModel):
    running: bool
    killed: bool
    live_confirmed: bool
    testnet: bool
    last_price: Optional[float]
    realized_pnl: float
    unrealized_pnl: float
    peak_equity: float
    daily_pnl: float
    status_message: str
    max_capital_usdt: float
    symbol: Optional[str] = None


class TradeOut(BaseModel):
    id: int
    symbol: str
    side: str
    price: float
    quantity: float
    fee: float
    pnl: float
    exchange_order_id: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditOut(BaseModel):
    id: int
    actor: str
    action: str
    detail: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AiAnalysisOut(BaseModel):
    id: int
    trend: str
    volatility_regime: str
    suggested_lower: Optional[float]
    suggested_upper: Optional[float]
    suggested_grid_count: Optional[int]
    risk_warnings: str
    approved: bool
    applied: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class LiveConfirmIn(BaseModel):
    confirm_phrase: str = Field(description='Must be exactly "ENABLE LIVE TRADING"')


class BacktestIn(BaseModel):
    symbol: str = "BTC/USDT"
    lower_price: float
    upper_price: float
    grid_count: int = 10
    grid_type: str = "arithmetic"
    capital_usdt: float = 100.0
    candles: int = Field(default=200, ge=50, le=1000)


class BacktestOut(BaseModel):
    trades: int
    realized_pnl: float
    max_drawdown_pct: float
    win_rate: float
    ending_equity: float
    notes: str


class HealthOut(BaseModel):
    status: str
    site_url: str
    testnet: bool
    anthropic_configured: bool = False


class MessageOut(BaseModel):
    message: str
    data: Optional[dict[str, Any]] = None
