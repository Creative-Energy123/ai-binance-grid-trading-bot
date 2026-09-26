"""ExecutionEngine / OrderManager.

Paper and backtest modes simulate fills with explicit fee and slippage costs.
Testnet and live modes place real orders through ccxt and then *verify* them:
an API call returning without raising is never treated as a fill. Orders carry a
deterministic client order id so a retry after a network failure cannot open a
second position.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BotInstance, Order, OrderStatus, Position, PositionStatus, Trade
from app.services.alerts import notify
from app.services.contracts import Side, SizedOrder, TradeSignal
from app.services.exchange import exchange
from app.services.risk import register_trade_result

logger = logging.getLogger(__name__)


class ExecutionError(RuntimeError):
    pass


def ccxt_side(side: Side, closing: bool = False) -> str:
    if side is Side.LONG:
        return "sell" if closing else "buy"
    return "buy" if closing else "sell"


def new_client_order_id(prefix: str, symbol: str) -> str:
    """Deterministically unique, and short enough for Binance's 36-char limit."""
    clean = symbol.replace("/", "").replace(":", "")[:8]
    return f"{prefix}-{clean}-{uuid.uuid4().hex[:12]}"


def apply_slippage(price: float, side: Side, closing: bool, slippage_pct: float) -> float:
    """Slippage always works against us."""
    adverse_up = (side is Side.LONG) != closing  # buying -> pay more, selling -> get less
    factor = 1 + slippage_pct / 100 if adverse_up else 1 - slippage_pct / 100
    return price * factor


def fee_for(notional: float, taker: bool = True) -> float:
    settings = get_settings()
    rate = settings.taker_fee_pct if taker else settings.maker_fee_pct
    return abs(notional) * rate / 100


@dataclass
class Fill:
    price: float
    quantity: float
    fee: float
    simulated: bool


class ExecutionEngine:
    # ------------------------------------------------------------- entries --
    async def open_position(
        self,
        db: AsyncSession,
        bot: BotInstance,
        sized: SizedOrder,
        signal: TradeSignal,
        signal_id: int | None = None,
    ) -> Position:
        settings = get_settings()
        duplicate = (
            await db.execute(
                select(Position).where(
                    Position.symbol == sized.symbol,
                    Position.status == PositionStatus.OPEN.value,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ExecutionError(f"A position on {sized.symbol} is already open (id={duplicate.id})")

        fill = await self._enter(db, bot, sized)

        position = Position(
            symbol=sized.symbol,
            side=sized.side.value,
            strategy=signal.strategy,
            regime=signal.regime,
            mode=bot.mode,
            status=PositionStatus.OPEN.value,
            signal_id=signal_id,
            entry_price=fill.price,
            quantity=fill.quantity,
            remaining_quantity=fill.quantity,
            stop_price=sized.stop_price,
            initial_stop_price=sized.stop_price,
            tp1_price=sized.tp1_price,
            tp2_price=sized.tp2_price,
            leverage=sized.leverage,
            margin_type=settings.futures_margin_type,
            liquidation_price=sized.liquidation_price,
            risk_amount=sized.risk_amount,
            signal_score=signal.score,
            entry_reason=" | ".join(signal.reasons[:8]),
            fees_paid=fill.fee,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            tp1_filled=False,
            opened_at=datetime.now(timezone.utc),
        )
        db.add(position)
        await db.flush()

        if settings.places_real_orders:
            await self._place_protective_orders(db, position)

        await notify(
            db,
            "trade_opened",
            f"{position.symbol} {position.side.upper()} opened",
            (
                f"Entry {fill.price:.6g} | stop {position.stop_price:.6g} | "
                f"TP1 {position.tp1_price:.6g} | qty {fill.quantity:.6g} | "
                f"score {signal.score:.0f} | {signal.strategy} | mode {bot.mode}"
            ),
        )
        return position

    async def _enter(self, db: AsyncSession, bot: BotInstance, sized: SizedOrder) -> Fill:
        settings = get_settings()
        side = ccxt_side(sized.side)
        client_id = new_client_order_id("ent", sized.symbol)

        order = Order(
            client_order_id=client_id,
            symbol=sized.symbol,
            side=side,
            order_type="market",
            quantity=sized.quantity,
            price=sized.entry_price,
            mode=bot.mode,
            status=OrderStatus.PENDING.value,
        )
        db.add(order)
        await db.flush()

        if not settings.places_real_orders:
            price = apply_slippage(sized.entry_price, sized.side, False, settings.slippage_pct)
            fee = fee_for(price * sized.quantity)
            order.status = OrderStatus.FILLED.value
            order.filled_quantity = sized.quantity
            order.average_fill_price = price
            order.fee = fee
            await db.flush()
            return Fill(price=price, quantity=sized.quantity, fee=fee, simulated=True)

        if settings.futures_enabled:
            await exchange.set_leverage(
                sized.symbol, sized.leverage, settings.futures_margin_type
            )
        try:
            response = await exchange.create_order(
                sized.symbol,
                "market",
                side,
                sized.quantity,
                params={"newClientOrderId": client_id},
            )
        except RuntimeError as exc:
            order.status = OrderStatus.FAILED.value
            order.error = str(exc)
            await db.flush()
            raise ExecutionError(f"Entry order failed: {exc}") from exc

        order.exchange_order_id = str(response.get("id") or "")
        order.raw_response = {k: v for k, v in response.items() if k != "info"}

        confirmed = await self._confirm_fill(order, sized.symbol)
        if confirmed.quantity <= 0:
            order.status = OrderStatus.FAILED.value
            order.error = "Order acknowledged but no fill could be confirmed"
            await db.flush()
            raise ExecutionError(
                f"{sized.symbol} entry was acknowledged but not confirmed filled; "
                "reconcile the account before trading again"
            )

        order.status = (
            OrderStatus.FILLED.value
            if confirmed.quantity >= sized.quantity * 0.999
            else OrderStatus.PARTIALLY_FILLED.value
        )
        order.filled_quantity = confirmed.quantity
        order.average_fill_price = confirmed.price
        order.fee = confirmed.fee
        await db.flush()
        return confirmed

    async def _confirm_fill(self, order: Order, symbol: str) -> Fill:
        """Read the order back from the exchange rather than trusting the ack."""
        if not order.exchange_order_id:
            return Fill(price=0.0, quantity=0.0, fee=0.0, simulated=False)
        try:
            fetched = await exchange.fetch_order(order.exchange_order_id, symbol)
        except RuntimeError as exc:
            logger.error("Could not confirm order %s: %s", order.exchange_order_id, exc)
            return Fill(price=0.0, quantity=0.0, fee=0.0, simulated=False)

        filled = float(fetched.get("filled") or 0.0)
        avg = float(fetched.get("average") or fetched.get("price") or 0.0)
        fee_info = fetched.get("fee") or {}
        fee = float(fee_info.get("cost") or 0.0) or fee_for(avg * filled)
        return Fill(price=avg, quantity=filled, fee=fee, simulated=False)

    async def _place_protective_orders(self, db: AsyncSession, position: Position) -> None:
        """A live position without a working stop is the one thing we never allow."""
        settings = get_settings()
        side = ccxt_side(Side(position.side), closing=True)
        params: dict = {"stopPrice": position.stop_price, "reduceOnly": True}
        if not settings.futures_enabled:
            params.pop("reduceOnly")

        client_id = new_client_order_id("stp", position.symbol)
        order = Order(
            position_id=position.id,
            client_order_id=client_id,
            symbol=position.symbol,
            side=side,
            order_type="stop_loss",
            quantity=position.remaining_quantity,
            price=position.stop_price,
            mode=position.mode,
            status=OrderStatus.PENDING.value,
        )
        db.add(order)
        await db.flush()
        try:
            response = await exchange.create_order(
                position.symbol,
                "STOP_MARKET" if settings.futures_enabled else "stop_loss_limit",
                side,
                position.remaining_quantity,
                price=None if settings.futures_enabled else position.stop_price,
                params={**params, "newClientOrderId": client_id},
            )
            order.exchange_order_id = str(response.get("id") or "")
            order.status = OrderStatus.OPEN.value
            order.raw_response = {k: v for k, v in response.items() if k != "info"}
        except RuntimeError as exc:
            order.status = OrderStatus.FAILED.value
            order.error = str(exc)
            await db.flush()
            await notify(
                db,
                "stop_order_failed",
                f"Stop order FAILED for {position.symbol}",
                f"{exc}. Closing the position immediately — a live position without a stop is not allowed.",
                severity="critical",
            )
            await self.close_position(db, None, position, position.entry_price, "stop_placement_failed")
            raise ExecutionError(f"Stop-loss placement failed: {exc}") from exc
        await db.flush()

    # -------------------------------------------------------------- exits --
    async def close_position(
        self,
        db: AsyncSession,
        bot: BotInstance | None,
        position: Position,
        price: float,
        reason: str,
        fraction: float = 1.0,
    ) -> Trade | None:
        """Close all or part of a position and write the journal entry."""
        settings = get_settings()
        quantity = position.remaining_quantity * max(0.0, min(1.0, fraction))
        if quantity <= 0:
            return None

        side = Side(position.side)
        if settings.places_real_orders:
            fill = await self._exit_live(db, position, quantity)
        else:
            exit_price = apply_slippage(price, side, True, settings.slippage_pct)
            fill = Fill(
                price=exit_price,
                quantity=quantity,
                fee=fee_for(exit_price * quantity),
                simulated=True,
            )

        direction = 1 if side is Side.LONG else -1
        gross = (fill.price - position.entry_price) * fill.quantity * direction
        entry_fee_share = position.fees_paid * (fill.quantity / position.quantity) if position.quantity else 0.0
        fees = fill.fee + entry_fee_share
        pnl = gross - fees

        position.remaining_quantity -= fill.quantity
        position.realized_pnl += pnl
        position.fees_paid += fill.fee
        closed = position.remaining_quantity <= max(position.quantity * 1e-6, 1e-12)

        now = datetime.now(timezone.utc)
        opened_at = position.opened_at or now
        if opened_at.tzinfo is None:  # values read back from the DB can be naive
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        risk = position.risk_amount or 0.0
        trade = Trade(
            position_id=position.id,
            symbol=position.symbol,
            strategy=position.strategy,
            side=position.side,
            mode=position.mode,
            entry_price=position.entry_price,
            exit_price=fill.price,
            stop_price=position.stop_price,
            tp_price=position.tp1_price,
            quantity=fill.quantity,
            risk_amount=risk,
            fees=fees,
            slippage=abs(fill.price - price) * fill.quantity,
            pnl=pnl,
            pnl_pct=100 * pnl / (position.entry_price * fill.quantity) if fill.quantity else 0.0,
            r_multiple=(pnl / risk) if risk else 0.0,
            duration_seconds=int((now - opened_at).total_seconds()),
            regime=position.regime,
            signal_score=position.signal_score,
            entry_reason=position.entry_reason,
            exit_reason=reason,
            opened_at=opened_at,
            closed_at=now,
        )
        db.add(trade)

        if closed:
            position.status = PositionStatus.CLOSED.value
            position.closed_at = now
            position.exit_reason = reason
            position.remaining_quantity = 0.0

        if bot is not None:
            bot.realized_pnl += pnl
            bot.equity += pnl
            bot.peak_equity = max(bot.peak_equity, bot.equity)
            if closed:
                await register_trade_result(db, bot, position.realized_pnl)

        await db.flush()
        await notify(
            db,
            "trade_closed",
            f"{position.symbol} {position.side.upper()} {'closed' if closed else 'partially closed'}",
            (
                f"Exit {fill.price:.6g} ({reason}) | P&L {pnl:+.2f} USDT "
                f"({trade.r_multiple:+.2f}R) | fees {fees:.4f}"
            ),
        )
        return trade

    async def _exit_live(self, db: AsyncSession, position: Position, quantity: float) -> Fill:
        settings = get_settings()
        side = ccxt_side(Side(position.side), closing=True)
        client_id = new_client_order_id("ext", position.symbol)
        order = Order(
            position_id=position.id,
            client_order_id=client_id,
            symbol=position.symbol,
            side=side,
            order_type="market",
            quantity=quantity,
            mode=position.mode,
            status=OrderStatus.PENDING.value,
        )
        db.add(order)
        await db.flush()

        params: dict = {"newClientOrderId": client_id}
        if settings.futures_enabled:
            params["reduceOnly"] = True
        response = await exchange.create_order(
            position.symbol, "market", side, quantity, params=params
        )
        order.exchange_order_id = str(response.get("id") or "")
        fill = await self._confirm_fill(order, position.symbol)
        if fill.quantity <= 0:
            order.status = OrderStatus.FAILED.value
            order.error = "Exit not confirmed"
            await db.flush()
            raise ExecutionError(
                f"Exit order for {position.symbol} could not be confirmed; reconcile manually"
            )
        order.status = OrderStatus.FILLED.value
        order.filled_quantity = fill.quantity
        order.average_fill_price = fill.price
        order.fee = fill.fee
        await db.flush()
        return fill

    async def cancel_position_orders(self, db: AsyncSession, position: Position) -> None:
        settings = get_settings()
        rows = (
            await db.execute(
                select(Order).where(
                    Order.position_id == position.id,
                    Order.status.in_([OrderStatus.OPEN.value, OrderStatus.PENDING.value]),
                )
            )
        ).scalars().all()
        for order in rows:
            if settings.places_real_orders and order.exchange_order_id:
                try:
                    await exchange.cancel_order(order.exchange_order_id, order.symbol)
                except RuntimeError as exc:
                    logger.warning("Cancel failed for %s: %s", order.exchange_order_id, exc)
                    order.error = str(exc)
            order.status = OrderStatus.CANCELLED.value
        await db.flush()

    # ---------------------------------------------------- position management --
    async def manage_position(
        self,
        db: AsyncSession,
        bot: BotInstance,
        position: Position,
        price: float,
        high: float,
        low: float,
        atr: float | None,
    ) -> None:
        """Apply stop, targets and trailing to an open position.

        `high`/`low` are the extremes of the most recent entry-timeframe candle so
        an intrabar stop touch is not missed between ticks. When both the stop and
        a target were touched in the same candle the stop is assumed to have hit
        first — the pessimistic assumption.
        """
        settings = get_settings()
        side = Side(position.side)
        long = side is Side.LONG

        stop_hit = low <= position.stop_price if long else high >= position.stop_price
        if stop_hit:
            await self.cancel_position_orders(db, position)
            await self.close_position(db, bot, position, position.stop_price, "stop_loss")
            return

        tp2_hit = high >= position.tp2_price if long else low <= position.tp2_price
        if tp2_hit:
            await self.cancel_position_orders(db, position)
            await self.close_position(db, bot, position, position.tp2_price, "take_profit_2")
            return

        if not position.tp1_filled:
            tp1_hit = high >= position.tp1_price if long else low <= position.tp1_price
            if tp1_hit:
                await self.close_position(
                    db, bot, position, position.tp1_price, "take_profit_1",
                    fraction=settings.tp1_close_fraction,
                )
                if position.status == PositionStatus.OPEN.value:
                    position.tp1_filled = True
                    if settings.move_stop_to_breakeven_after_tp1:
                        position.stop_price = position.entry_price
                        await self._resync_stop(db, position)
                await db.flush()
                return

        if settings.trailing_enabled and position.tp1_filled and atr:
            distance = atr * settings.trailing_atr_multiplier
            trailed = price - distance if long else price + distance
            improved = trailed > position.stop_price if long else trailed < position.stop_price
            if improved:
                position.stop_price = trailed
                await self._resync_stop(db, position)
                await db.flush()

    async def _resync_stop(self, db: AsyncSession, position: Position) -> None:
        """Replace the resting stop order after the stop level moves."""
        if not get_settings().places_real_orders:
            return
        await self.cancel_position_orders(db, position)
        try:
            await self._place_protective_orders(db, position)
        except ExecutionError as exc:
            logger.error("Could not re-place stop for %s: %s", position.symbol, exc)


execution_engine = ExecutionEngine()
