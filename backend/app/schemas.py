from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


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


TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d")
TIMEFRAME_RANK = {tf: i for i, tf in enumerate(TIMEFRAMES)}


class ConfigIn(BaseModel):
    """Every operator-editable setting. All fields optional: only what is sent
    is changed, and the values are bounded so the UI cannot push the engine into
    a configuration the risk documentation does not cover."""

    # --- risk ---
    risk_per_trade_pct: Optional[float] = Field(default=None, gt=0, le=2)
    max_daily_loss_pct: Optional[float] = Field(default=None, gt=0, le=20)
    max_weekly_loss_pct: Optional[float] = Field(default=None, gt=0, le=40)
    max_positions: Optional[int] = Field(default=None, ge=1, le=10)
    max_consecutive_losses: Optional[int] = Field(default=None, ge=1, le=20)
    max_trades_per_hour: Optional[int] = Field(default=None, ge=1, le=60)
    max_symbol_exposure_pct: Optional[float] = Field(default=None, gt=0, le=500)
    max_total_exposure_pct: Optional[float] = Field(default=None, gt=0, le=1000)
    loss_cooldown_minutes: Optional[int] = Field(default=None, ge=0, le=1440)

    # --- symbols and timeframes ---
    symbols: Optional[str] = Field(default=None, min_length=3, max_length=400)
    timeframe_primary: Optional[str] = None
    timeframe_setup: Optional[str] = None
    timeframe_entry: Optional[str] = None

    # --- strategies and scoring ---
    strategy_trend_pullback: Optional[bool] = None
    strategy_breakout: Optional[bool] = None
    strategy_range_scalping: Optional[bool] = None
    minimum_signal_score: Optional[float] = Field(default=None, ge=0, le=100)
    weight_trend: Optional[float] = Field(default=None, ge=0, le=100)
    weight_momentum: Optional[float] = Field(default=None, ge=0, le=100)
    weight_volume: Optional[float] = Field(default=None, ge=0, le=100)
    weight_volatility: Optional[float] = Field(default=None, ge=0, le=100)
    weight_structure: Optional[float] = Field(default=None, ge=0, le=100)
    weight_entry: Optional[float] = Field(default=None, ge=0, le=100)

    # --- stops and targets ---
    stop_mode: Optional[str] = Field(default=None, pattern="^(atr|structure|fixed)$")
    atr_stop_multiplier: Optional[float] = Field(default=None, gt=0, le=10)
    fixed_stop_pct: Optional[float] = Field(default=None, gt=0, le=10)
    structure_lookback: Optional[int] = Field(default=None, ge=5, le=200)
    tp1_r_multiple: Optional[float] = Field(default=None, gt=0, le=20)
    tp2_r_multiple: Optional[float] = Field(default=None, gt=0, le=50)
    tp1_close_fraction: Optional[float] = Field(default=None, gt=0, le=1)
    move_stop_to_breakeven_after_tp1: Optional[bool] = None
    trailing_enabled: Optional[bool] = None
    trailing_atr_multiplier: Optional[float] = Field(default=None, gt=0, le=20)

    # --- costs ---
    taker_fee_pct: Optional[float] = Field(default=None, ge=0, le=1)
    maker_fee_pct: Optional[float] = Field(default=None, ge=0, le=1)
    slippage_pct: Optional[float] = Field(default=None, ge=0, le=1)

    # --- futures ---
    futures_enabled: Optional[bool] = None
    futures_leverage: Optional[int] = Field(default=None, ge=1, le=20)
    futures_max_leverage: Optional[int] = Field(default=None, ge=1, le=20)
    min_liquidation_distance_pct: Optional[float] = Field(default=None, ge=0, le=90)

    # --- runtime ---
    engine_interval_seconds: Optional[int] = Field(default=None, ge=5, le=600)
    market_data_staleness_seconds: Optional[int] = Field(default=None, ge=30, le=3600)
    starting_paper_equity_usdt: Optional[float] = Field(default=None, gt=0)

    # --- AI ---
    ai_enabled: Optional[bool] = None
    anthropic_model: Optional[str] = Field(default=None, max_length=64)

    # --- alert routing (non-secret parts; tokens go through /integrations) ---
    telegram_chat_id: Optional[str] = Field(default=None, max_length=64)
    smtp_host: Optional[str] = Field(default=None, max_length=128)
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_user: Optional[str] = Field(default=None, max_length=128)
    alert_email_to: Optional[str] = Field(default=None, max_length=255)

    @field_validator("timeframe_primary", "timeframe_setup", "timeframe_entry")
    @classmethod
    def _known_timeframe(cls, value: str | None) -> str | None:
        if value is not None and value not in TIMEFRAMES:
            raise ValueError(f"Timeframe must be one of {', '.join(TIMEFRAMES)}")
        return value

    @field_validator("symbols")
    @classmethod
    def _valid_symbols(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = [s.strip().upper() for s in value.split(",") if s.strip()]
        if not parsed:
            raise ValueError("At least one symbol is required")
        if len(parsed) > 20:
            raise ValueError("At most 20 symbols; each one costs API weight every tick")
        for symbol in parsed:
            if "/" not in symbol:
                raise ValueError(f"'{symbol}' must be in BASE/QUOTE form, e.g. BTC/USDT")
        return ",".join(parsed)

    @model_validator(mode="after")
    def _timeframes_ordered(self) -> "ConfigIn":
        """entry < setup < primary. A setup timeframe below the entry timeframe
        inverts the whole multi-timeframe logic."""
        chain = [self.timeframe_entry, self.timeframe_setup, self.timeframe_primary]
        if all(tf is None for tf in chain):
            return self
        if any(tf is None for tf in chain):
            raise ValueError("Change all three timeframes together so their ordering can be checked")
        ranks = [TIMEFRAME_RANK[tf] for tf in chain]
        if not ranks[0] < ranks[1] < ranks[2]:
            raise ValueError(
                f"Timeframes must increase: entry ({chain[0]}) < setup ({chain[1]}) "
                f"< primary ({chain[2]})"
            )
        return self

    @model_validator(mode="after")
    def _weights_not_all_zero(self) -> "ConfigIn":
        weights = [
            self.weight_trend,
            self.weight_momentum,
            self.weight_volume,
            self.weight_volatility,
            self.weight_structure,
            self.weight_entry,
        ]
        supplied = [w for w in weights if w is not None]
        if supplied and len(supplied) == len(weights) and sum(supplied) <= 0:
            raise ValueError("Scoring weights cannot all be zero")
        return self

    @model_validator(mode="after")
    def _targets_ordered(self) -> "ConfigIn":
        if self.tp1_r_multiple is not None and self.tp2_r_multiple is not None:
            if self.tp2_r_multiple <= self.tp1_r_multiple:
                raise ValueError("TP2 must be further away than TP1")
        return self

    @model_validator(mode="after")
    def _leverage_within_cap(self) -> "ConfigIn":
        if self.futures_leverage is not None and self.futures_max_leverage is not None:
            if self.futures_leverage > self.futures_max_leverage:
                raise ValueError("Leverage cannot exceed the configured maximum")
        return self


# Kept so the older, risk-only endpoint keeps working.
RiskConfigIn = ConfigIn


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
class IntegrationSecretIn(BaseModel):
    name: str = Field(description="One of the settable integration secrets")
    value: str = Field(min_length=1, max_length=2000)


class IntegrationSecretOut(BaseModel):
    name: str
    label: str
    configured: bool
    source: str
    masked: str
    updated_at: Optional[str]


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
