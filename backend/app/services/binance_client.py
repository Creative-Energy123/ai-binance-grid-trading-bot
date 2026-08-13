from __future__ import annotations

import logging
from typing import Any, Optional

import ccxt.async_support as ccxt
from ccxt.base.errors import ExchangeError

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BinanceClient:
    """Thin async wrapper around ccxt Binance (testnet or live)."""

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.binance] = None
        self._markets_loaded = False

    def _build(self) -> ccxt.binance:
        opts: dict[str, Any] = {
            "apiKey": settings.binance_api_key or None,
            "secret": settings.binance_api_secret or None,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                # Docker/VPS clocks often drift; Binance rejects skewed timestamps.
                "adjustForTimeDifference": True,
                "recvWindow": 10000,
            },
        }
        exchange = ccxt.binance(opts)
        if settings.binance_testnet:
            exchange.set_sandbox_mode(True)
        return exchange

    async def ensure(self) -> ccxt.binance:
        if self._exchange is None:
            self._exchange = self._build()
        return self._exchange

    async def _ensure_markets(self, ex: ccxt.binance) -> None:
        if not self._markets_loaded:
            await ex.load_markets()
            self._markets_loaded = True

    async def close(self) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None
        self._markets_loaded = False

    async def fetch_ticker_price(self, symbol: str) -> float:
        ex = await self.ensure()
        ticker = await ex.fetch_ticker(symbol)
        return float(ticker["last"])

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[list]:
        ex = await self.ensure()
        return await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def _prepare_order(self, ex: ccxt.binance, symbol: str, amount: float, price: float) -> tuple[float, float]:
        market = ex.market(symbol)
        amount = float(ex.amount_to_precision(symbol, amount))
        price = float(ex.price_to_precision(symbol, price))
        if amount <= 0:
            raise ExchangeError(f"Order amount rounds to zero for {symbol}")
        min_cost = (market.get("limits") or {}).get("cost", {}).get("min")
        if min_cost and amount * price < float(min_cost):
            raise ExchangeError(
                f"Order notional {amount * price:.4f} USDT below Binance minimum {min_cost} for {symbol}"
            )
        return amount, price

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
        await self._ensure_markets(ex)
        amount, price = self._prepare_order(ex, symbol, amount, price)
        try:
            return await ex.create_order(symbol, "limit", side, amount, price)
        except ExchangeError as exc:
            mode = "testnet" if settings.binance_testnet else "live"
            raise ExchangeError(
                f"Binance {mode} order failed ({side} {amount} {symbol} @ {price}): {exc}"
            ) from exc

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
