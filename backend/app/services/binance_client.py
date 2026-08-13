from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import urlparse

import ccxt.async_support as ccxt
from ccxt.base.errors import BaseError, NetworkError

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def is_geo_restricted_error(exc: BaseException) -> bool:
    """True when Binance returns HTTP 451 / regional eligibility block."""
    text = str(exc).lower()
    raw = str(exc)
    return (
        "451" in raw
        or "restricted location" in text
        or "service unavailable from a restricted location" in text
        or ("eligibility" in text and "binance.com" in text)
    )


def binance_error_hint(exc: BaseException) -> str:
    """Actionable suffix for Binance/ccxt failures shown to operators."""
    if is_geo_restricted_error(exc):
        proxy = settings.effective_binance_proxy
        if proxy:
            host = urlparse(proxy).hostname or "configured proxy"
            return (
                f" Binance geo-blocked direct API access from this server (HTTP 451). "
                f"Proxy is set ({host}) but Binance still rejected the request — verify the proxy "
                "egress is in an allowed region and supports HTTPS CONNECT."
            )
        return (
            " Binance geo-blocked API access from this server region (HTTP 451 — restricted "
            "location per Binance Terms). Options: (1) set BINANCE_PROXY or HTTPS_PROXY to an "
            "HTTP(S) proxy in an allowed region, (2) move the VPS to an allowed country, or "
            "(3) use compliant proxy/VPN egress and redeploy."
        )
    if isinstance(exc, NetworkError):
        hint = " Check VPS clock sync / network connectivity, or retry shortly."
        if settings.binance_testnet:
            hint += " Confirm SPOT testnet keys when BINANCE_TESTNET=true."
        return hint
    if settings.binance_testnet:
        return " Use Binance SPOT testnet API keys when BINANCE_TESTNET=true."
    return ""


def wrap_binance_failure(context: str, exc: BaseError) -> RuntimeError:
    return RuntimeError(f"{context}: {exc}.{binance_error_hint(exc)}")


class BinanceClient:
    """Thin async wrapper around ccxt Binance (testnet or live)."""

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.binance] = None
        self._markets_loaded = False
        # ccxt async exchange instances are not safe for concurrent awaits
        self._lock = asyncio.Lock()

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
        proxy = settings.effective_binance_proxy
        if proxy:
            opts["proxies"] = {"http": proxy, "https": proxy}
            opts["aiohttp_proxy"] = proxy
            host = urlparse(proxy).hostname or "proxy"
            logger.info("Binance client routing API traffic via proxy (%s)", host)
        exchange = ccxt.binance(opts)
        if settings.binance_testnet:
            exchange.set_sandbox_mode(True)
        return exchange

    async def ensure(self) -> ccxt.binance:
        if self._exchange is None:
            self._exchange = self._build()
            if settings.binance_api_key and settings.binance_api_secret:
                try:
                    await self._exchange.load_time_difference()
                except BaseError as exc:
                    logger.warning(
                        "Binance time sync failed (will retry on request): %s%s",
                        exc,
                        binance_error_hint(exc),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Binance time sync failed (will retry on request): %s", exc)
        return self._exchange

    async def _ensure_markets(self, ex: ccxt.binance) -> None:
        if not self._markets_loaded:
            try:
                await ex.load_markets()
                self._markets_loaded = True
            except BaseError as exc:
                raise wrap_binance_failure("Binance market load failed", exc) from exc

    async def close(self) -> None:
        async with self._lock:
            if self._exchange is not None:
                await self._exchange.close()
                self._exchange = None
            self._markets_loaded = False

    async def fetch_ticker_price(self, symbol: str) -> float:
        async with self._lock:
            ex = await self.ensure()
            try:
                ticker = await ex.fetch_ticker(symbol)
                return float(ticker["last"])
            except BaseError as exc:
                raise wrap_binance_failure(f"Binance ticker fetch failed for {symbol}", exc) from exc

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[list]:
        async with self._lock:
            ex = await self.ensure()
            try:
                return await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            except BaseError as exc:
                raise wrap_binance_failure(f"Binance OHLCV fetch failed for {symbol}", exc) from exc

    def _prepare_order(self, ex: ccxt.binance, symbol: str, amount: float, price: float) -> tuple[float, float]:
        market = ex.market(symbol)
        amount = float(ex.amount_to_precision(symbol, amount))
        price = float(ex.price_to_precision(symbol, price))
        if amount <= 0:
            raise RuntimeError(f"Order amount rounds to zero for {symbol}")
        min_cost = (market.get("limits") or {}).get("cost", {}).get("min")
        if min_cost and amount * price < float(min_cost):
            raise RuntimeError(
                f"Order notional {amount * price:.4f} USDT below Binance minimum {min_cost} for {symbol}"
            )
        return amount, price

    def _wrap_exchange_failure(self, side: str, amount: float, symbol: str, price: float, exc: BaseError) -> RuntimeError:
        mode = "testnet" if settings.binance_testnet else "live"
        return RuntimeError(
            f"Binance {mode} order failed ({side} {amount} {symbol} @ {price}): {exc}.{binance_error_hint(exc)}"
        )

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
        async with self._lock:
            ex = await self.ensure()
            await self._ensure_markets(ex)
            amount, price = self._prepare_order(ex, symbol, amount, price)
            try:
                return await ex.create_order(symbol, "limit", side, amount, price)
            except BaseError as exc:
                raise self._wrap_exchange_failure(side, amount, symbol, price, exc) from exc

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if order_id.startswith("sim-"):
            return {"id": order_id, "status": "canceled", "simulated": True}
        async with self._lock:
            ex = await self.ensure()
            try:
                return await ex.cancel_order(order_id, symbol)
            except BaseError as exc:
                raise wrap_binance_failure(f"Binance cancel failed for order {order_id}", exc) from exc

    async def cancel_all(self, symbol: str) -> list[dict[str, Any]]:
        if not settings.binance_api_key:
            return []
        async with self._lock:
            ex = await self.ensure()
            try:
                return await ex.cancel_all_orders(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.exception("cancel_all failed: %s", exc)
                return []

    async def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        if not settings.binance_api_key:
            return []
        async with self._lock:
            ex = await self.ensure()
            try:
                return await ex.fetch_open_orders(symbol)
            except BaseError as exc:
                raise wrap_binance_failure(f"Binance open orders fetch failed for {symbol}", exc) from exc


binance_client = BinanceClient()
