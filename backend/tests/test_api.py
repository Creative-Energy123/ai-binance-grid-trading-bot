"""Integration tests over the HTTP API, against a file-backed SQLite database.

No network access: Binance is never reached because the tests stay in paper mode
and never start the engine loop.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import ensure_admin_user
from app.config import get_settings
from app.db import SessionLocal, engine, init_db
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    await init_db()
    async with SessionLocal() as db:
        await ensure_admin_user(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth(client: AsyncClient) -> AsyncClient:
    settings = get_settings()
    response = await client.post(
        "/api/auth/login",
        data={"username": settings.admin_email, "password": settings.admin_password},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.mark.asyncio
async def test_login_rejects_a_bad_password(client: AsyncClient):
    response = await client.post(
        "/api/auth/login", data={"username": get_settings().admin_email, "password": "wrong"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoints_require_a_token(client: AsyncClient):
    assert (await client.get("/api/bot/status")).status_code == 401
    assert (await client.get("/api/journal/trades")).status_code == 401


@pytest.mark.asyncio
async def test_login_returns_a_refresh_token_that_rotates(client: AsyncClient):
    settings = get_settings()
    first = await client.post(
        "/api/auth/login",
        data={"username": settings.admin_email, "password": settings.admin_password},
    )
    refresh = first.json()["refresh_token"]

    second = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert second.status_code == 200
    assert second.json()["refresh_token"] != refresh

    # The consumed token must not work twice.
    assert (await client.post("/api/auth/refresh", json={"refresh_token": refresh})).status_code == 401


@pytest.mark.asyncio
async def test_status_and_overview(auth: AsyncClient):
    status = await auth.get("/api/bot/status")
    assert status.status_code == 200
    body = status.json()
    assert body["running"] is False
    assert body["mode"] in ("backtest", "paper", "testnet", "live")

    overview = await auth.get("/api/bot/overview")
    assert overview.status_code == 200
    assert "equity" in overview.json()


@pytest.mark.asyncio
async def test_start_pause_and_stop_in_paper_mode(auth: AsyncClient):
    get_settings().trading_mode = "paper"
    assert (await auth.post("/api/bot/start")).json()["running"] is True
    assert (await auth.post("/api/bot/pause")).json()["paused"] is True
    assert (await auth.post("/api/bot/stop")).json()["running"] is False


@pytest.mark.asyncio
async def test_emergency_stop_records_the_event(auth: AsyncClient):
    response = await auth.post("/api/bot/emergency-stop", json={"close_positions": False})
    assert response.status_code == 200
    status = (await auth.get("/api/bot/status")).json()
    assert status["emergency_stopped"] is True

    events = (await auth.get("/api/journal/risk-events")).json()
    assert any(e["event_type"] == "emergency_stop" for e in events)


@pytest.mark.asyncio
async def test_live_confirmation_rejects_a_wrong_phrase(auth: AsyncClient):
    response = await auth.post(
        "/api/bot/confirm-live", json={"confirm_phrase": "yes please", "acknowledge_risk": True}
    )
    assert response.status_code == 400
    assert "phrase" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_live_mode_is_refused_until_confirmed(auth: AsyncClient):
    response = await auth.post("/api/bot/mode", json={"mode": "live"})
    assert response.status_code == 400
    assert "confirm" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_risk_config_round_trips(auth: AsyncClient):
    update = await auth.post("/api/bot/risk-config", json={"risk_per_trade_pct": 0.2})
    assert update.status_code == 200
    assert (await auth.get("/api/bot/risk-config")).json()["risk_per_trade_pct"] == 0.2
    await auth.post("/api/bot/risk-config", json={"risk_per_trade_pct": 0.35})


@pytest.mark.asyncio
async def test_journal_and_csv_export(auth: AsyncClient):
    assert (await auth.get("/api/journal/trades")).status_code == 200
    csv = await auth.get("/api/journal/trades.csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "symbol" in csv.text.splitlines()[0]


@pytest.mark.asyncio
async def test_health_reports_every_component(client: AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200
    names = {c["name"] for c in response.json()["components"]}
    assert {"DATABASE", "MARKET DATA", "BINANCE API", "RISK ENGINE", "EXECUTION ENGINE"} <= names


@pytest.mark.asyncio
async def test_metrics_are_prometheus_formatted(client: AsyncClient):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "scalper_equity_usdt" in response.text
    assert "# TYPE scalper_open_positions gauge" in response.text


@pytest.mark.asyncio
async def test_security_headers_are_present(client: AsyncClient):
    response = await client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.asyncio
async def test_the_ai_assistant_reports_unavailable_without_a_key(auth: AsyncClient):
    settings = get_settings()
    original = settings.anthropic_api_key
    settings.anthropic_api_key = ""
    try:
        response = await auth.post("/api/ai/ask", json={"question": "What is the regime?"})
        assert response.status_code == 503
    finally:
        settings.anthropic_api_key = original


@pytest.mark.asyncio
async def test_credentials_are_never_returned(auth: AsyncClient):
    response = await auth.get("/api/account/credentials")
    assert response.status_code == 200
    for row in response.json():
        assert "api_secret" not in row
        assert "api_key_encrypted" not in row


@pytest_asyncio.fixture(autouse=True, scope="module")
async def _dispose():
    yield
    await engine.dispose()
