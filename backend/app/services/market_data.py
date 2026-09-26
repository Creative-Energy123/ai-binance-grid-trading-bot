"""MarketDataService — candle cache, freshness tracking and MarketContext assembly.

The last candle returned by Binance is still forming, so it is dropped before
indicators are computed: the engine only ever reasons about closed candles. The
still-forming candle's close is kept separately as the current price.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import get_settings
from app.services.contracts import MarketContext
from app.services.exchange import exchange
from app.services.indicators import Candle, compute_snapshot
from app.services.regime import detect_regime

logger = logging.getLogger(__name__)


class StaleMarketDataError(RuntimeError):
    pass


@dataclass
class CacheEntry:
    candles: list[Candle]
    fetched_at: datetime


@dataclass
class MarketDataService:
    _cache: dict[tuple[str, str], CacheEntry] = field(default_factory=dict)
    _last_success: datetime | None = None

    @property
    def last_success(self) -> datetime | None:
        return self._last_success

    def is_stale(self) -> bool:
        settings = get_settings()
        if self._last_success is None:
            return True
        age = (datetime.now(timezone.utc) - self._last_success).total_seconds()
        return age > settings.market_data_staleness_seconds

    async def candles(self, symbol: str, timeframe: str, limit: int | None = None) -> list[Candle]:
        settings = get_settings()
        limit = limit or settings.candle_limit
        raw = await exchange.fetch_ohlcv(symbol, timeframe, limit)
        self._cache[(symbol, timeframe)] = CacheEntry(candles=raw, fetched_at=datetime.now(timezone.utc))
        self._last_success = datetime.now(timezone.utc)
        return raw

    def cached(self, symbol: str, timeframe: str) -> list[Candle] | None:
        entry = self._cache.get((symbol, timeframe))
        return entry.candles if entry else None

    async def build_context(self, symbol: str, with_spread: bool = False) -> MarketContext:
        """Fetch all three timeframes and assemble the decision context."""
        settings = get_settings()
        primary_tf = settings.timeframe_primary
        setup_tf = settings.timeframe_setup
        entry_tf = settings.timeframe_entry

        primary_raw, setup_raw, entry_raw = await asyncio.gather(
            self.candles(symbol, primary_tf),
            self.candles(symbol, setup_tf),
            self.candles(symbol, entry_tf),
        )

        if not entry_raw:
            raise StaleMarketDataError(f"No candles returned for {symbol}")

        # Current price comes from the forming candle; indicators use closed ones.
        price = float(entry_raw[-1][4])

        primary = compute_snapshot(primary_tf, primary_raw[:-1], settings.structure_lookback)
        setup = compute_snapshot(setup_tf, setup_raw[:-1], settings.structure_lookback)
        entry = compute_snapshot(entry_tf, entry_raw[:-1], settings.structure_lookback)

        regime = detect_regime(primary=primary, secondary=setup)

        spread = await exchange.fetch_order_book_spread(symbol) if with_spread else None

        return MarketContext(
            symbol=symbol,
            price=price,
            primary=primary,
            setup=setup,
            entry=entry,
            regime=regime,
            spread_pct=spread,
        )


market_data = MarketDataService()
