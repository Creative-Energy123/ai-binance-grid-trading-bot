"""The exchange client must not sign requests it does not need to sign.

Binance blocks signed `sapi` calls from restricted regions with HTTP 451, so a
paper-mode deployment that only needs public candles must not attach API keys.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.exchange import BinanceExchange


@pytest.fixture
def client() -> BinanceExchange:
    return BinanceExchange()


def build(client: BinanceExchange, **overrides):
    settings = get_settings()
    saved = {k: getattr(settings, k) for k in overrides}
    for k, v in overrides.items():
        setattr(settings, k, v)
    try:
        return client._build()
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)


def test_paper_mode_attaches_no_credentials(client):
    ex = build(client, trading_mode="paper", binance_api_key="key", binance_api_secret="secret")
    assert not ex.apiKey
    assert not ex.secret


def test_backtest_mode_attaches_no_credentials(client):
    ex = build(client, trading_mode="backtest", binance_api_key="key", binance_api_secret="secret")
    assert not ex.apiKey


def test_live_mode_attaches_credentials(client):
    ex = build(client, trading_mode="live", binance_api_key="key", binance_api_secret="secret")
    assert ex.apiKey == "key"
    assert ex.secret == "secret"


def test_testnet_mode_attaches_credentials(client):
    ex = build(client, trading_mode="testnet", binance_api_key="key", binance_api_secret="secret")
    assert ex.apiKey == "key"


def test_currency_metadata_is_never_fetched(client):
    """fetchCurrencies hits the signed sapi endpoint that 451s in blocked regions."""
    for mode in ("paper", "testnet", "live"):
        ex = build(client, trading_mode=mode, binance_api_key="key", binance_api_secret="secret")
        assert ex.options["fetchCurrencies"] is False, mode


def test_futures_selects_the_futures_market(client):
    ex = build(client, trading_mode="paper", futures_enabled=True)
    assert ex.options["defaultType"] == "future"
    ex = build(client, trading_mode="paper", futures_enabled=False)
    assert ex.options["defaultType"] == "spot"


@pytest.mark.asyncio
async def test_validation_without_keys_reports_clearly(client):
    settings = get_settings()
    saved = (settings.binance_api_key, settings.binance_api_secret)
    settings.binance_api_key = ""
    settings.binance_api_secret = ""
    try:
        result = await client.validate_credentials()
        assert result["valid"] is False
        assert "No Binance API key" in result["error"]
    finally:
        settings.binance_api_key, settings.binance_api_secret = saved
