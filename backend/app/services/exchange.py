"""Binance access layer (ccxt, async).

Covers spot and USDT-M futures, market/limit/stop/take-profit orders, balances,
positions and OHLCV, with retry-with-backoff around transient network errors.
A ccxt async exchange instance is not safe for concurrent awaits, so every call
goes through one lock.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional
from urllib.parse import urlparse

import ccxt.async_support as ccxt
from ccxt.base.errors import (
    BaseError,
    DDoSProtection,
    ExchangeNotAvailable,
    InvalidOrder,
    NetworkError,
    RateLimitExceeded,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

RETRYABLE = (NetworkError, ExchangeNotAvailable, RateLimitExceeded, DDoSProtection)
MAX_ATTEMPTS = 4


def is_geo_restricted_error(exc: BaseException) -> bool:
    """True when Binance returns HTTP 451 / regional eligibility block."""
    text = str(exc).lower()
    return (
        "451" in str(exc)
        or "restricted location" in text
        or ("eligibility" in text and "binance.com" in text)
    )


def binance_error_hint(exc: BaseException) -> str:
    settings = get_settings()
    if is_geo_restricted_error(exc):
        proxy = settings.effective_binance_proxy
        if proxy:
            host = urlparse(proxy).hostname or "configured proxy"
            return (
                f" Binance geo-blocked this server (HTTP 451). A proxy is configured ({host}) but "
                "was still rejected — check that its egress is in an allowed region and supports "
                "HTTPS CONNECT."
            )
        return (
            " Binance geo-blocked API access from this server region (HTTP 451). Set BINANCE_PROXY "
            "or HTTPS_PROXY to a compliant proxy in an allowed region, or move the host."
        )
    if isinstance(exc, NetworkError):
        hint = " Check host clock sync and network connectivity, then retry."
        if settings.use_testnet:
            hint += " Testnet mode requires Binance testnet API keys."
        return hint
    return ""


def wrap_failure(context: str, exc: BaseException) -> RuntimeError:
    return RuntimeError(f"{context}: {exc}.{binance_error_hint(exc)}")


class BinanceExchange:
    """Thin async wrapper around ccxt Binance for spot and USDT-M futures."""

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.binance] = None
        self._markets_loaded = False
        self._lock = asyncio.Lock()
        self._leverage_set: set[str] = set()

    # ---------------------------------------------------------------- setup --
    def _build(self) -> ccxt.binance:
        settings = get_settings()
        opts: dict[str, Any] = {
            "apiKey": settings.binance_api_key or None,
            "secret": settings.binance_api_secret or None,
            "enableRateLimit": True,
            "options": {
                "defaultType": "future" if settings.futures_enabled else "spot",
                "adjustForTimeDifference": True,
                "recvWindow": 10000,
            },
        }
        proxy = settings.effective_binance_proxy
        if proxy:
            opts["proxies"] = {"http": proxy, "https": proxy}
            opts["aiohttp_proxy"] = proxy
            logger.info("Binance traffic routed via proxy %s", urlparse(proxy).hostname or "proxy")
        exchange = ccxt.binance(opts)
        if settings.use_testnet:
            exchange.set_sandbox_mode(True)
        return exchange

    async def ensure(self) -> ccxt.binance:
        if self._exchange is None:
            self._exchange = self._build()
        return self._exchange

    async def reset(self) -> None:
        """Rebuild the client after a mode/market switch."""
        async with self._lock:
            if self._exchange is not None:
                await self._exchange.close()
            self._exchange = None
            self._markets_loaded = False
            self._leverage_set.clear()

    async def close(self) -> None:
        await self.reset()

    async def _markets(self, ex: ccxt.binance) -> None:
        if not self._markets_loaded:
            await ex.load_markets()
            self._markets_loaded = True

    async def _call(self, context: str, fn, *args, **kwargs) -> Any:
        """Run a ccxt call under the lock, retrying transient failures."""
        delay = 0.5
        last: BaseException | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            async with self._lock:
                ex = await self.ensure()
                try:
                    await self._markets(ex)
                    return await fn(ex, *args, **kwargs)
                except RETRYABLE as exc:
                    last = exc
                    logger.warning("%s failed (attempt %d/%d): %s", context, attempt, MAX_ATTEMPTS, exc)
                except BaseError as exc:
                    raise wrap_failure(context, exc) from exc
            await asyncio.sleep(delay + random.uniform(0, 0.25))
            delay *= 2
        raise wrap_failure(context, last or RuntimeError("unknown error"))

    # ----------------------------------------------------------- market data --
    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return await self._call(
            f"Binance ticker fetch for {symbol}", lambda ex: ex.fetch_ticker(symbol)
        )

    async def fetch_price(self, symbol: str) -> float:
        ticker = await self.fetch_ticker(symbol)
        return float(ticker["last"])

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 300) -> list[list]:
        return await self._call(
            f"Binance OHLCV fetch for {symbol} {timeframe}",
            lambda ex: ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
        )

    async def fetch_order_book_spread(self, symbol: str) -> float | None:
        try:
            book = await self._call(
                f"Binance order book fetch for {symbol}",
                lambda ex: ex.fetch_order_book(symbol, 5),
            )
        except RuntimeError:
            return None
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            return None
        bid, ask = float(bids[0][0]), float(asks[0][0])
        mid = (bid + ask) / 2
        return 100 * (ask - bid) / mid if mid else None

    # --------------------------------------------------------------- account --
    def has_credentials(self) -> bool:
        settings = get_settings()
        return bool(settings.binance_api_key and settings.binance_api_secret)

    async def fetch_balance(self) -> dict[str, Any]:
        return await self._call("Binance balance fetch", lambda ex: ex.fetch_balance())

    async def fetch_equity_usdt(self) -> float:
        balance = await self.fetch_balance()
        total = (balance.get("total") or {}).get("USDT")
        return float(total or 0.0)

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        if not get_settings().futures_enabled:
            return []
        return await self._call("Binance positions fetch", lambda ex: ex.fetch_positions(symbols))

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return await self._call(
            "Binance open orders fetch", lambda ex: ex.fetch_open_orders(symbol)
        )

    async def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return await self._call(
            f"Binance order fetch for {order_id}", lambda ex: ex.fetch_order(order_id, symbol)
        )

    async def validate_credentials(self) -> dict[str, Any]:
        """Check keys work and report the permissions Binance grants them."""
        result: dict[str, Any] = {"valid": False, "can_trade": False, "can_withdraw": None}
        try:
            balance = await self.fetch_balance()
        except RuntimeError as exc:
            result["error"] = str(exc)
            return result
        result["valid"] = True
        info = balance.get("info") or {}
        if "canTrade" in info:
            result["can_trade"] = bool(info.get("canTrade"))
            result["can_withdraw"] = bool(info.get("canWithdraw"))
        else:
            result["can_trade"] = True
        return result

    # -------------------------------------------------------------- trading --
    async def set_leverage(self, symbol: str, leverage: int, margin_type: str) -> None:
        if not get_settings().futures_enabled or symbol in self._leverage_set:
            return
        try:
            await self._call(
                f"Binance margin type for {symbol}",
                lambda ex: ex.set_margin_mode(margin_type, symbol),
            )
        except RuntimeError as exc:
            # Binance errors when the mode is already what we asked for.
            logger.info("set_margin_mode for %s: %s", symbol, exc)
        await self._call(
            f"Binance leverage for {symbol}", lambda ex: ex.set_leverage(leverage, symbol)
        )
        self._leverage_set.add(symbol)

    async def market_constraints(self, symbol: str) -> dict[str, float]:
        def _read(ex: ccxt.binance) -> dict[str, float]:
            market = ex.market(symbol)
            limits = market.get("limits") or {}
            return {
                "min_notional": float((limits.get("cost") or {}).get("min") or 5.0),
                "min_amount": float((limits.get("amount") or {}).get("min") or 0.0),
                "amount_step": float((market.get("precision") or {}).get("amount") or 0) or 0.0,
            }

        return await self._call(f"Binance market info for {symbol}", lambda ex: _read(ex))

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Place an order. `side` is the ccxt side ("buy"/"sell")."""

        async def _place(ex: ccxt.binance) -> dict[str, Any]:
            qty = float(ex.amount_to_precision(symbol, amount))
            if qty <= 0:
                raise InvalidOrder(f"Order amount rounds to zero for {symbol}")
            limit_price = float(ex.price_to_precision(symbol, price)) if price else None
            return await ex.create_order(symbol, order_type, side, qty, limit_price, params or {})

        return await self._call(
            f"Binance {order_type} {side} order for {symbol}", lambda ex: _place(ex)
        )

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return await self._call(
            f"Binance cancel for {order_id}", lambda ex: ex.cancel_order(order_id, symbol)
        )

    async def cancel_all(self, symbol: str) -> list[dict[str, Any]]:
        try:
            return await self._call(
                f"Binance cancel-all for {symbol}", lambda ex: ex.cancel_all_orders(symbol)
            )
        except RuntimeError as exc:
            logger.warning("cancel_all failed for %s: %s", symbol, exc)
            return []


exchange = BinanceExchange()
