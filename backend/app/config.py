from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

TRADING_MODES = ("backtest", "paper", "testnet", "live")


class Settings(BaseSettings):
    """Runtime configuration. Everything here is overridable via environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Binance Adaptive Scalping Bot"
    site_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:5173,http://localhost:8000"
    database_url: str = "postgresql+asyncpg://scalper:scalper@db:5432/scalper"
    redis_url: str = "redis://redis:6379/0"

    # --- auth ---
    admin_email: str = "admin@localhost"
    admin_password: str = "change-me-now"
    jwt_secret: str = "change-me-jwt-secret-min-32-chars!!"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15
    # 32-byte urlsafe-base64 key used to AES-256-GCM encrypt exchange credentials at rest.
    credentials_encryption_key: str = ""

    # --- exchange ---
    exchange_market: str = "spot"  # spot | futures
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_proxy: str = ""
    taker_fee_pct: float = 0.04  # % per side, Binance futures taker default
    maker_fee_pct: float = 0.02
    slippage_pct: float = 0.02  # assumed adverse slippage per side, %

    # --- trading mode ---
    # Conservative default: never place a real order until explicitly switched.
    trading_mode: str = "paper"
    live_confirm_phrase: str = "ENABLE LIVE TRADING"
    starting_paper_equity_usdt: float = 10_000.0

    symbols: str = "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT"
    timeframe_primary: str = "15m"
    timeframe_setup: str = "5m"
    timeframe_entry: str = "1m"
    candle_limit: int = 300
    market_data_staleness_seconds: int = 180

    # --- risk ---
    risk_per_trade_pct: float = 0.35
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 5.0
    max_positions: int = 2
    max_consecutive_losses: int = 4
    max_trades_per_hour: int = 6
    # Exposure caps are notional as a percentage of equity. Scalping stops are
    # tight (0.3-0.6%), so a 0.35% risk budget implies a notional of roughly
    # 60-100% of equity: caps below that silently shrink every position and the
    # real risk control is risk_per_trade_pct, not these.
    max_symbol_exposure_pct: float = 100.0
    max_total_exposure_pct: float = 150.0
    loss_cooldown_minutes: int = 20

    # --- stops / targets ---
    stop_mode: str = "atr"  # atr | structure | fixed
    atr_stop_multiplier: float = 1.5
    fixed_stop_pct: float = 0.5
    structure_lookback: int = 20
    tp1_r_multiple: float = 1.5
    tp2_r_multiple: float = 2.5
    tp1_close_fraction: float = 0.5
    move_stop_to_breakeven_after_tp1: bool = True
    trailing_enabled: bool = True
    trailing_atr_multiplier: float = 2.0

    # --- futures ---
    futures_enabled: bool = False
    futures_leverage: int = 3
    futures_max_leverage: int = 5
    futures_margin_type: str = "isolated"
    min_liquidation_distance_pct: float = 8.0

    # --- strategies / scoring ---
    strategy_trend_pullback: bool = True
    strategy_breakout: bool = True
    strategy_range_scalping: bool = True
    minimum_signal_score: float = 75.0
    weight_trend: float = 25.0
    weight_momentum: float = 20.0
    weight_volume: float = 15.0
    weight_volatility: float = 15.0
    weight_structure: float = 15.0
    weight_entry: float = 10.0

    # --- AI assistant (analysis only; never places orders) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    ai_enabled: bool = True

    # --- alerts ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    alert_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""

    # --- runtime ---
    engine_interval_seconds: int = 20
    reconcile_interval_seconds: int = 120
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @property
    def places_real_orders(self) -> bool:
        return self.trading_mode in ("testnet", "live")

    @property
    def use_testnet(self) -> bool:
        return self.trading_mode == "testnet"

    @property
    def round_trip_cost_pct(self) -> float:
        """Fees plus slippage for an entry and an exit, in percent of notional."""
        return 2 * (self.taker_fee_pct + self.slippage_pct)

    @property
    def effective_binance_proxy(self) -> str | None:
        for candidate in (self.binance_proxy, os.getenv("HTTPS_PROXY"), os.getenv("HTTP_PROXY")):
            if candidate and candidate.strip():
                return candidate.strip()
        return None

    @property
    def score_weights(self) -> dict[str, float]:
        return {
            "trend": self.weight_trend,
            "momentum": self.weight_momentum,
            "volume": self.weight_volume,
            "volatility": self.weight_volatility,
            "structure": self.weight_structure,
            "entry": self.weight_entry,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
