from __future__ import annotations

import logging
from typing import Any, Optional

import ccxt.async_support as ccxt

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BinanceClient:
    """Thin async wrapper around ccxt Binance (testnet or live)."""

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.binance] = None

    def _build(self) -> ccxt.binance:
        opts: dict[str, Any] = {
            "apiKey": settings.binance_api_key or None,
            "secret": settings.binance_api_secret or None,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        exchange = ccxt.binance(opts)
        if settings.binance_testnet:
            exchange.set_sandbox_mode(True)
        return exchange

    async def ensure(self) -> ccxt.binance:
        if self._exchange is None:
            self._exchange = self._build()
        return self._exchange

    async def close(self) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    async def fetch_ticker_price(self, symbol: str) -> float:
        ex = await self.ensure()
        ticker = await ex.fetch_ticker(symbol)
        return float(ticker["last"])

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[list]:
        ex = await self.ensure()
        return await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    async def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict[str, Any]:
        if not settings.binance_api_key or not settings.binance_api_secret:
            # Paper mode when keys missing — useful for local smoke tests
            logger.warning("No Binance keys; simulating limit %s order", side)
            return {
                "id": f"sim-{side}-{price}",
                "symbol": symbol,
                "side": side,
                "price": price,
                "amount": amount,
                "status": "open",
                "simulated": True,
            }
        ex = await self.ensure()
        return await ex.create_order(symbol, "limit", side, amount, price)

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if order_id.startswith("sim-"):
            return {"id": order_id, "status": "canceled", "simulated": True}
        ex = await self.ensure()
        return await ex.cancel_order(order_id, symbol)

    async def cancel_all(self, symbol: str) -> list[dict[str, Any]]:
        if not settings.binance_api_key:
            return []
        ex = await self.ensure()
        try:
            return await ex.cancel_all_orders(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.exception("cancel_all failed: %s", exc)
            return []

    async def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        if not settings.binance_api_key:
            return []
        ex = await self.ensure()
        return await ex.fetch_open_orders(symbol)


binance_client = BinanceClient()
