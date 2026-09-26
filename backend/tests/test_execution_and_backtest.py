from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import Base
from app.models import BotInstance, Position, PositionStatus, Trade
from app.services.backtest import aggregate, run_backtest
from app.services.contracts import ScoreBreakdown, Side, SizedOrder, TradeSignal
from app.services.execution import apply_slippage, execution_engine, fee_for
from app.services.risk import register_trade_result
from tests.conftest import synth_candles


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def bot(db: AsyncSession) -> BotInstance:
    row = BotInstance(id=1, mode="paper", equity=10_000.0, peak_equity=10_000.0)
    db.add(row)
    await db.flush()
    return row


def signal(entry=100.0, stop=99.0) -> TradeSignal:
    return TradeSignal(
        symbol="BTC/USDT",
        side=Side.LONG,
        strategy="trend_pullback",
        score=80.0,
        max_score=100.0,
        scores=ScoreBreakdown(),
        weighted={},
        entry_price=entry,
        stop_price=stop,
        tp1_price=entry + (entry - stop) * 1.5,
        tp2_price=entry + (entry - stop) * 2.5,
        risk_pct=0.35,
        reward_risk=1.5,
        regime="strong_bull",
    )


def sized(quantity=10.0, entry=100.0, stop=99.0) -> SizedOrder:
    sig = signal(entry, stop)
    return SizedOrder(
        symbol=sig.symbol,
        side=Side.LONG,
        quantity=quantity,
        notional=quantity * entry,
        entry_price=entry,
        stop_price=stop,
        tp1_price=sig.tp1_price,
        tp2_price=sig.tp2_price,
        risk_amount=quantity * (entry - stop),
    )


def test_slippage_always_works_against_the_trader():
    assert apply_slippage(100, Side.LONG, closing=False, slippage_pct=0.1) > 100  # buying
    assert apply_slippage(100, Side.LONG, closing=True, slippage_pct=0.1) < 100  # selling
    assert apply_slippage(100, Side.SHORT, closing=False, slippage_pct=0.1) < 100
    assert apply_slippage(100, Side.SHORT, closing=True, slippage_pct=0.1) > 100


@pytest.mark.asyncio
async def test_paper_entry_records_a_position_with_fees(db, bot):
    settings = get_settings()
    settings.trading_mode = "paper"
    position = await execution_engine.open_position(db, bot, sized(), signal())
    assert position.status == PositionStatus.OPEN.value
    assert position.entry_price > 100.0  # slippage paid on entry
    assert position.fees_paid == pytest.approx(fee_for(position.entry_price * 10.0))


@pytest.mark.asyncio
async def test_a_second_position_on_the_same_symbol_is_refused(db, bot):
    get_settings().trading_mode = "paper"
    await execution_engine.open_position(db, bot, sized(), signal())
    with pytest.raises(Exception):
        await execution_engine.open_position(db, bot, sized(), signal())


@pytest.mark.asyncio
async def test_stop_hit_closes_the_position_and_journals_a_loss(db, bot):
    get_settings().trading_mode = "paper"
    position = await execution_engine.open_position(db, bot, sized(), signal())
    await execution_engine.manage_position(db, bot, position, 99.0, high=99.5, low=98.5, atr=0.5)
    await db.refresh(position)
    assert position.status == PositionStatus.CLOSED.value
    assert position.exit_reason == "stop_loss"
    trade = (await db.execute(Trade.__table__.select())).first()
    assert trade is not None
    assert trade.pnl < 0


@pytest.mark.asyncio
async def test_stop_is_assumed_to_fill_before_a_target_in_the_same_candle(db, bot):
    get_settings().trading_mode = "paper"
    position = await execution_engine.open_position(db, bot, sized(), signal())
    # This candle touches both the stop (99.0) and TP2 (102.5).
    await execution_engine.manage_position(db, bot, position, 101.0, high=103.0, low=98.0, atr=0.5)
    await db.refresh(position)
    assert position.exit_reason == "stop_loss"


@pytest.mark.asyncio
async def test_tp1_closes_part_and_moves_the_stop_to_breakeven(db, bot):
    settings = get_settings()
    settings.trading_mode = "paper"
    settings.move_stop_to_breakeven_after_tp1 = True
    position = await execution_engine.open_position(db, bot, sized(), signal())
    entry = position.entry_price
    await execution_engine.manage_position(db, bot, position, 101.5, high=101.6, low=100.5, atr=0.5)
    await db.refresh(position)
    assert position.status == PositionStatus.OPEN.value
    assert position.tp1_filled is True
    assert position.remaining_quantity == pytest.approx(10.0 * (1 - settings.tp1_close_fraction))
    assert position.stop_price == pytest.approx(entry)


@pytest.mark.asyncio
async def test_a_losing_trade_starts_a_cooldown_and_a_loss_streak(db, bot):
    await register_trade_result(db, bot, -25.0)
    assert bot.consecutive_losses == 1
    assert bot.cooldown_until is not None


@pytest.mark.asyncio
async def test_the_daily_loss_limit_pauses_the_bot(db, bot):
    settings = get_settings()
    limit = bot.equity * settings.max_daily_loss_pct / 100
    await register_trade_result(db, bot, -(limit + 1))
    assert bot.paused is True
    assert "daily loss limit" in bot.status_message


@pytest.mark.asyncio
async def test_a_winning_trade_clears_the_loss_streak(db, bot):
    await register_trade_result(db, bot, -10.0)
    await register_trade_result(db, bot, 30.0)
    assert bot.consecutive_losses == 0


def test_aggregate_rolls_candles_up_correctly():
    base = synth_candles(60)
    rolled = aggregate(base, "1m", "5m")
    assert len(rolled) == 12
    first = rolled[0]
    assert first[1] == base[0][1]  # open of the first minute
    assert first[4] == base[4][4]  # close of the fifth
    assert first[2] == max(c[2] for c in base[:5])
    assert first[3] == min(c[3] for c in base[:5])
    assert first[5] == pytest.approx(sum(c[5] for c in base[:5]))


def test_aggregate_drops_an_incomplete_final_bucket():
    assert len(aggregate(synth_candles(62), "1m", "5m")) == 12


def test_backtest_runs_and_reports_cost_inclusive_metrics():
    settings = get_settings()
    settings.trading_mode = "paper"
    settings.futures_enabled = False
    result = run_backtest("BTC/USDT", synth_candles(1500, drift=0.0004, noise=0.002), settings, 10_000.0)
    assert result.candles == 1500
    assert result.equity_curve
    assert "total_trades" in result.metrics
    if result.metrics["total_trades"]:
        assert result.metrics["fees"] > 0
        assert result.metrics["slippage_cost"] > 0


def test_a_backtest_with_no_setups_is_a_valid_outcome():
    settings = get_settings()
    result = run_backtest("BTC/USDT", synth_candles(900, noise=0.05), settings, 10_000.0)
    assert result.metrics["total_trades"] == 0
    assert result.ending_equity == pytest.approx(10_000.0)
