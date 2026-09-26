from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# ------------------------------------------------------------------ auth --
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    role: str

    model_config = {"from_attributes": True}


class CredentialsIn(BaseModel):
    """Keys are encrypted before storage and are never returned."""

    label: str = "binance"
    api_key: str = Field(min_length=8)
    api_secret: str = Field(min_length=8)
    market: str = Field(default="spot", pattern="^(spot|futures)$")
    environment: str = Field(default="testnet", pattern="^(testnet|live)$")


class CredentialsOut(BaseModel):
    id: int
    label: str
    market: str
    environment: str
    api_key_masked: str
    withdrawals_enabled: bool
    validated_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ------------------------------------------------------------------- bot --
class BotStatusOut(BaseModel):
    mode: str
    running: bool
    paused: bool
    emergency_stopped: bool
    live_confirmed: bool
    status_message: str
    equity: float
    peak_equity: float
    realized_pnl: float
    consecutive_losses: int
    cooldown_until: Optional[datetime]
    last_tick_at: Optional[datetime]
    symbols: list[str]
    futures_enabled: bool
    leverage: int
    places_real_orders: bool


class ModeChangeIn(BaseModel):
    mode: str = Field(pattern="^(backtest|paper|testnet|live)$")


class LiveConfirmIn(BaseModel):
    confirm_phrase: str = Field(description='Must match LIVE_CONFIRM_PHRASE exactly')
    acknowledge_risk: bool = Field(description="Must be true; confirms the operator accepts loss risk")


class EmergencyStopIn(BaseModel):
    close_positions: bool = True


class RiskConfigIn(BaseModel):
    risk_per_trade_pct: Optional[float] = Field(default=None, gt=0, le=2)
    max_daily_loss_pct: Optional[float] = Field(default=None, gt=0, le=20)
    max_weekly_loss_pct: Optional[float] = Field(default=None, gt=0, le=40)
    max_positions: Optional[int] = Field(default=None, ge=1, le=10)
    max_consecutive_losses: Optional[int] = Field(default=None, ge=1, le=20)
    max_trades_per_hour: Optional[int] = Field(default=None, ge=1, le=60)
    minimum_signal_score: Optional[float] = Field(default=None, ge=0, le=100)
    futures_leverage: Optional[int] = Field(default=None, ge=1, le=20)


# --------------------------------------------------------------- trading --
class PositionOut(BaseModel):
    id: int
    symbol: str
    side: str
    strategy: str
    mode: str
    entry_price: float
    quantity: float
    remaining_quantity: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    tp1_filled: bool
    leverage: int
    liquidation_price: Optional[float]
    unrealized_pnl: float
    realized_pnl: float
    risk_amount: float
    signal_score: float
    entry_reason: str
    opened_at: Optional[datetime]

    model_config = {"from_attributes": True}


class TradeOut(BaseModel):
    id: int
    symbol: str
    strategy: str
    side: str
    mode: str
    entry_price: float
    exit_price: float
    stop_price: float
    tp_price: float
    quantity: float
    risk_amount: float
    fees: float
    slippage: float
    funding_fees: float
    pnl: float
    pnl_pct: float
    r_multiple: float
    duration_seconds: int
    regime: str
    signal_score: float
    entry_reason: str
    exit_reason: str
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class SignalOut(BaseModel):
    id: int
    symbol: str
    side: str
    strategy: str
    regime: str
    score: float
    score_breakdown: dict[str, Any]
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    reward_risk: float
    accepted: bool
    rejection_reason: str
    reasons: list[Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class RiskEventOut(BaseModel):
    id: int
    event_type: str
    severity: str
    symbol: str
    detail: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertOut(BaseModel):
    id: int
    event: str
    title: str
    body: str
    delivered: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# -------------------------------------------------------------- research --
class BacktestIn(BaseModel):
    symbol: str = "BTC/USDT"
    candles: int = Field(default=3000, ge=500, le=20000)
    starting_equity: float = Field(default=10_000.0, gt=0)
    split: bool = Field(
        default=True,
        description="Split the history into train / validation / out-of-sample segments",
    )


class BacktestSegmentOut(BaseModel):
    segment: str
    candles: int
    starting_equity: float
    ending_equity: float
    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    drawdown_curve: list[dict[str, Any]]
    warnings: list[str]


class BacktestOut(BaseModel):
    symbol: str
    segments: list[BacktestSegmentOut]
    overfitting_warning: Optional[str] = None
    disclaimer: str


# --------------------------------------------------------------------- ai --
class AiAskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    symbol: Optional[str] = None


class AiAnalysisOut(BaseModel):
    id: int
    symbol: str
    question: str
    summary: str
    model: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ------------------------------------------------------------------ misc --
class HealthComponentOut(BaseModel):
    name: str
    status: str
    detail: str = ""


class HealthOut(BaseModel):
    status: str
    mode: str
    ok: bool
    components: list[HealthComponentOut]
    ai_configured: bool


class OverviewOut(BaseModel):
    equity: float
    balance: float
    available: float
    today_pnl: float
    week_pnl: float
    month_pnl: float
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    trades: int
    open_positions: int
    unrealized_pnl: float


class MessageOut(BaseModel):
    message: str
    data: Optional[dict[str, Any]] = None
