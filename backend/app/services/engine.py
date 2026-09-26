"""TradingEngine — the tick loop that wires every stage together.

MarketData -> Indicators -> Regime -> Signals -> Risk -> Sizing -> Execution.

The loop is defensive by construction: any failed health check pauses new entries
while still managing open positions, and an emergency stop cancels orders and can
flatten everything.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    BotInstance,
    Order,
    OrderStatus,
    RiskEvent,
    Signal,
    SystemLog,
)
from app.services import portfolio, risk, signals
from app.services.alerts import notify
from app.services.contracts import MarketContext
from app.services.exchange import exchange
from app.services.execution import ExecutionError, execution_engine
from app.services.market_data import market_data
from app.services.sizing import SizingError, size_position

logger = logging.getLogger(__name__)


@dataclass
class ComponentHealth:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class HealthReport:
    components: list[ComponentHealth] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.components)

    @property
    def failures(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.components if not c.ok]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "components": [
                {"name": c.name, "status": "OK" if c.ok else "FAIL", "detail": c.detail}
                for c in self.components
            ],
        }


class TradingEngine:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self.last_tick_at: datetime | None = None
        self.last_health: HealthReport = HealthReport()
        self.last_evaluations: dict[str, dict] = {}

    # ---------------------------------------------------------------- health --
    async def health(self, db: AsyncSession, bot: BotInstance) -> HealthReport:
        settings = get_settings()
        components: list[ComponentHealth] = []

        try:
            await db.execute(select(BotInstance.id).limit(1))
            components.append(ComponentHealth("DATABASE", True))
        except Exception as exc:  # noqa: BLE001
            components.append(ComponentHealth("DATABASE", False, str(exc)))

        stale = market_data.is_stale()
        components.append(
            ComponentHealth(
                "MARKET DATA",
                not stale,
                "" if not stale else (
                    f"No successful candle fetch in the last "
                    f"{settings.market_data_staleness_seconds}s"
                ),
            )
        )

        if settings.places_real_orders:
            if not exchange.has_credentials():
                components.append(
                    ComponentHealth("BINANCE API", False, "Mode requires API keys but none are set")
                )
            else:
                try:
                    await exchange.fetch_equity_usdt()
                    components.append(ComponentHealth("BINANCE API", True))
                except RuntimeError as exc:
                    components.append(ComponentHealth("BINANCE API", False, str(exc)))
        else:
            components.append(
                ComponentHealth("BINANCE API", True, f"{settings.trading_mode} mode (no live orders)")
            )

        components.append(
            ComponentHealth(
                "RISK ENGINE",
                not bot.emergency_stopped,
                "Emergency stop engaged" if bot.emergency_stopped else "",
            )
        )
        components.append(
            ComponentHealth(
                "EXECUTION ENGINE",
                self.last_error is None,
                self.last_error or "",
            )
        )

        drift_ok, drift_detail = await self._check_position_drift(db)
        components.append(ComponentHealth("POSITION SYNC", drift_ok, drift_detail))

        report = HealthReport(components=components)
        self.last_health = report
        return report

    async def _check_position_drift(self, db: AsyncSession) -> tuple[bool, str]:
        """Compare our open positions against the exchange's view."""
        settings = get_settings()
        if not (settings.places_real_orders and settings.futures_enabled and exchange.has_credentials()):
            return True, ""
        try:
            remote = await exchange.fetch_positions()
        except RuntimeError as exc:
            return False, f"Could not read exchange positions: {exc}"

        remote_open = {
            p["symbol"] for p in remote if abs(float(p.get("contracts") or 0)) > 0
        }
        local_open = {p.symbol for p in await portfolio.open_positions(db)}
        unexpected = remote_open - local_open
        missing = local_open - remote_open
        if unexpected or missing:
            detail = (
                f"Exchange has {sorted(unexpected)} we do not track; "
                f"we track {sorted(missing)} the exchange does not show"
            )
            return False, detail
        return True, ""

    # ------------------------------------------------------------------ tick --
    async def tick(self) -> None:
        """One engine iteration. Never raises — failures become risk events."""
        if self._lock.locked():
            logger.debug("Previous tick still running; skipping")
            return
        async with self._lock:
            try:
                async with SessionLocal() as db:
                    await self._tick(db)
                    await db.commit()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                self.last_error = str(exc)
                logger.exception("Engine tick failed")
                async with SessionLocal() as db:
                    db.add(
                        RiskEvent(
                            event_type="engine_tick_failed",
                            severity="critical",
                            detail=str(exc),
                        )
                    )
                    await notify(db, "api_failure", "Engine tick failed", str(exc), "critical")
                    await db.commit()

    async def _tick(self, db: AsyncSession) -> None:
        settings = get_settings()
        bot = await portfolio.get_bot(db)
        self.last_tick_at = datetime.now(timezone.utc)
        bot.last_tick_at = self.last_tick_at

        if not bot.running or bot.emergency_stopped:
            return

        contexts: dict[str, MarketContext] = {}
        prices: dict[str, float] = {}
        for symbol in settings.symbol_list:
            try:
                ctx = await market_data.build_context(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Market data failed for %s: %s", symbol, exc)
                continue
            contexts[symbol] = ctx
            prices[symbol] = ctx.price

        # Manage what is already open before considering anything new.
        await self._manage_open_positions(db, bot, contexts)

        try:
            await portfolio.refresh_equity(db, bot, prices)
        except RuntimeError as exc:
            bot.status_message = f"Account sync failed: {exc}"
            db.add(RiskEvent(event_type="account_sync_failed", severity="critical", detail=str(exc)))
            return

        health = await self.health(db, bot)
        if not health.ok:
            bot.status_message = "Holding: " + "; ".join(health.failures)
            db.add(
                RiskEvent(
                    event_type="health_check_failed",
                    severity="warning",
                    detail=bot.status_message,
                    context=health.as_dict(),
                )
            )
            return

        if bot.paused:
            bot.status_message = bot.status_message or "Paused: no new entries"
            return

        opened = 0
        evaluations: dict[str, dict] = {}
        for symbol, ctx in contexts.items():
            evaluation = signals.evaluate(ctx, settings)
            evaluations[symbol] = {
                "regime": evaluation.regime,
                "tradable": evaluation.tradable,
                "score": round(evaluation.best_score, 2),
                "rejected_reason": evaluation.rejected_reason,
                "signal": evaluation.signal.as_dict() if evaluation.signal else None,
            }
            if evaluation.signal is None:
                continue
            if await self._try_open(db, bot, evaluation.signal):
                opened += 1

        self.last_evaluations = evaluations
        bot.status_message = (
            f"Running — {len(contexts)} symbols scanned, {opened} entries this tick"
            if opened
            else f"Running — {len(contexts)} symbols scanned, no qualifying setup"
        )

    async def _manage_open_positions(
        self, db: AsyncSession, bot: BotInstance, contexts: dict[str, MarketContext]
    ) -> None:
        for position in await portfolio.open_positions(db):
            ctx = contexts.get(position.symbol)
            if ctx is None:
                continue
            candles = ctx.entry.candles
            last = candles[-1] if candles else None
            high = float(last[2]) if last else ctx.price
            low = float(last[3]) if last else ctx.price
            try:
                await execution_engine.manage_position(
                    db, bot, position, ctx.price, high, low, ctx.setup.atr
                )
            except ExecutionError as exc:
                logger.error("Position management failed for %s: %s", position.symbol, exc)
                db.add(
                    RiskEvent(
                        event_type="position_management_failed",
                        severity="critical",
                        symbol=position.symbol,
                        detail=str(exc),
                    )
                )

    async def _try_open(self, db: AsyncSession, bot: BotInstance, signal) -> bool:
        settings = get_settings()
        decision = await risk.approve(db, bot, signal)

        row = Signal(
            symbol=signal.symbol,
            side=signal.side.value,
            strategy=signal.strategy,
            regime=signal.regime,
            score=signal.score,
            score_breakdown={"components": signal.scores.as_dict(), "weighted": signal.weighted},
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            reward_risk=signal.reward_risk,
            accepted=decision.approved,
            rejection_reason="" if decision.approved else decision.reason[:255],
            reasons=signal.reasons,
        )
        db.add(row)
        await db.flush()

        if not decision.approved:
            logger.info("Risk rejected %s: %s", signal.symbol, decision.reason)
            return False

        constraints = {"min_notional": 5.0, "amount_step": None}
        if settings.places_real_orders:
            try:
                info = await exchange.market_constraints(signal.symbol)
                constraints["min_notional"] = info["min_notional"]
                constraints["amount_step"] = info["amount_step"] or None
            except RuntimeError as exc:
                logger.warning("Could not read market constraints for %s: %s", signal.symbol, exc)

        try:
            sized = size_position(
                signal,
                bot.equity,
                settings,
                min_notional=constraints["min_notional"],
                quantity_step=constraints["amount_step"],
            )
        except SizingError as exc:
            row.accepted = False
            row.rejection_reason = str(exc)[:255]
            db.add(
                RiskEvent(
                    event_type="sizing_rejected",
                    severity="info",
                    symbol=signal.symbol,
                    detail=str(exc),
                )
            )
            return False

        try:
            await execution_engine.open_position(db, bot, sized, signal, signal_id=row.id)
        except ExecutionError as exc:
            row.accepted = False
            row.rejection_reason = str(exc)[:255]
            db.add(
                RiskEvent(
                    event_type="execution_failed",
                    severity="critical",
                    symbol=signal.symbol,
                    detail=str(exc),
                )
            )
            await notify(db, "api_failure", f"Entry failed for {signal.symbol}", str(exc), "critical")
            return False
        return True

    # -------------------------------------------------------------- controls --
    async def start(self, db: AsyncSession, bot: BotInstance) -> None:
        settings = get_settings()
        if settings.is_live and not bot.live_confirmed:
            raise RuntimeError(
                "Live trading has not been confirmed. POST /api/bot/confirm-live with the "
                "confirmation phrase first."
            )
        bot.running = True
        bot.paused = False
        bot.emergency_stopped = False
        bot.status_message = f"Started in {bot.mode} mode"
        db.add(SystemLog(level="info", component="engine", message=bot.status_message))
        await notify(db, "bot_started", "Bot started", bot.status_message)

    async def pause(self, db: AsyncSession, bot: BotInstance) -> None:
        bot.paused = True
        bot.status_message = "Paused: no new entries, open positions still managed"
        db.add(SystemLog(level="info", component="engine", message=bot.status_message))
        await notify(db, "bot_paused", "Bot paused", bot.status_message)

    async def stop(self, db: AsyncSession, bot: BotInstance) -> None:
        bot.running = False
        bot.status_message = "Stopped"
        db.add(SystemLog(level="info", component="engine", message="Bot stopped"))
        await notify(db, "bot_stopped", "Bot stopped", "No further ticks will run.")

    async def close_all(self, db: AsyncSession, bot: BotInstance, reason: str = "manual_close") -> int:
        closed = 0
        for position in await portfolio.open_positions(db):
            price = position.entry_price
            try:
                price = await exchange.fetch_price(position.symbol)
            except RuntimeError as exc:
                logger.warning("Price fetch failed while closing %s: %s", position.symbol, exc)
            await execution_engine.cancel_position_orders(db, position)
            await execution_engine.close_position(db, bot, position, price, reason)
            closed += 1
        return closed

    async def emergency_stop(self, db: AsyncSession, bot: BotInstance, flatten: bool) -> dict:
        """Stop new orders, cancel resting orders, optionally flatten, and record it."""
        settings = get_settings()
        bot.running = False
        bot.paused = True
        bot.emergency_stopped = True
        bot.status_message = "EMERGENCY STOP engaged"

        cancelled = 0
        if settings.places_real_orders:
            for symbol in settings.symbol_list:
                cancelled += len(await exchange.cancel_all(symbol))
        open_orders = (
            await db.execute(
                select(Order).where(
                    Order.status.in_([OrderStatus.OPEN.value, OrderStatus.PENDING.value])
                )
            )
        ).scalars().all()
        for order in open_orders:
            order.status = OrderStatus.CANCELLED.value

        closed = await self.close_all(db, bot, "emergency_stop") if flatten else 0

        db.add(
            RiskEvent(
                event_type="emergency_stop",
                severity="critical",
                detail=f"Emergency stop: cancelled {cancelled} exchange orders, closed {closed} positions",
                context={"flatten": flatten},
            )
        )
        await notify(
            db,
            "emergency_stop",
            "EMERGENCY STOP",
            f"Cancelled {cancelled} exchange orders; closed {closed} positions.",
            "critical",
        )
        return {"cancelled_orders": cancelled, "closed_positions": closed}

    async def reconcile(self) -> None:
        """Periodic account/position reconciliation against Binance."""
        settings = get_settings()
        if not settings.places_real_orders:
            return
        async with SessionLocal() as db:
            bot = await portfolio.get_bot(db)
            ok, detail = await self._check_position_drift(db)
            if not ok:
                bot.paused = True
                bot.status_message = f"Paused: position mismatch — {detail}"
                db.add(
                    RiskEvent(
                        event_type="position_mismatch",
                        severity="critical",
                        detail=detail,
                    )
                )
                await notify(db, "position_mismatch", "Position mismatch detected", detail, "critical")
            await db.commit()


trading_engine = TradingEngine()
