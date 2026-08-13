import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Binance Grid Trading Bot"
    site_url: str = "https://trade.creativeenergy.pk"
    cors_origins: str = "https://trade.creativeenergy.pk,http://localhost:5173,http://localhost:8000"
    database_url: str = "postgresql+asyncpg://gridbot:gridbot@db:5432/gridbot"
    redis_url: str = "redis://redis:6379/0"

    admin_email: str = "admin@trade.creativeenergy.pk"
    admin_password: str = "change-me-now"
    jwt_secret: str = "change-me-jwt-secret-min-32-chars!!"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    # Optional HTTP(S) proxy for Binance API (geo-restricted VPS regions). Falls back to HTTPS_PROXY.
    binance_proxy: str = ""
    max_capital_usdt: float = 500.0
    default_symbol: str = "BTC/USDT"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    ai_analysis_interval_minutes: int = 30
    ai_auto_apply: bool = False

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    alert_webhook_url: str = ""

    price_poll_seconds: int = 15
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_binance_proxy(self) -> str | None:
        """Proxy URL for ccxt Binance calls (BINANCE_PROXY, then HTTPS_PROXY / HTTP_PROXY)."""
        for candidate in (
            self.binance_proxy,
            os.getenv("HTTPS_PROXY"),
            os.getenv("HTTP_PROXY"),
        ):
            if candidate and candidate.strip():
                return candidate.strip()
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
