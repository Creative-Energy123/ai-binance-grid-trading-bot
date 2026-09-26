"""PositionSizer — size from risk, never from a fixed notional.

quantity = (equity * risk_per_trade) / stop_distance

The result is then clamped by leverage, exposure caps and exchange minimums, and
for futures it is rejected when the liquidation price would sit too close.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.services.contracts import SizedOrder, Side, TradeSignal


class SizingError(RuntimeError):
    pass


def liquidation_price(entry: float, side: Side, leverage: int, maintenance_margin: float = 0.005) -> float:
    """Approximate isolated-margin liquidation price.

    Exchange formulas differ by tier; this is deliberately conservative and is
    only used as a safety gate, never as a substitute for the exchange's value.
    """
    if leverage <= 0:
        return 0.0
    move = entry * (1 / leverage - maintenance_margin)
    return max(entry - move, 0.0) if side is Side.LONG else entry + move


def size_position(
    signal: TradeSignal,
    equity: float,
    settings: Settings | None = None,
    min_notional: float = 5.0,
    quantity_step: float | None = None,
) -> SizedOrder:
    settings = settings or get_settings()
    notes: list[str] = []

    if equity <= 0:
        raise SizingError("Account equity is zero; cannot size a position")

    stop_distance = signal.stop_distance
    if stop_distance <= 0:
        raise SizingError("Stop distance is zero; cannot size a position")

    risk_amount = equity * settings.risk_per_trade_pct / 100
    quantity = risk_amount / stop_distance
    notional = quantity * signal.entry_price

    leverage = settings.futures_leverage if settings.futures_enabled else 1
    if settings.futures_enabled:
        leverage = max(1, min(leverage, settings.futures_max_leverage))
        max_notional = equity * leverage
    else:
        max_notional = equity  # spot cannot spend more than the balance

    if notional > max_notional:
        notes.append(
            f"Notional capped by available buying power ({notional:.2f} -> {max_notional:.2f}); "
            "realised risk will be below the configured target"
        )
        notional = max_notional
        quantity = notional / signal.entry_price
        risk_amount = quantity * stop_distance

    symbol_cap = equity * settings.max_symbol_exposure_pct / 100
    if notional > symbol_cap:
        notes.append(f"Notional capped by the per-symbol exposure limit ({symbol_cap:.2f})")
        notional = symbol_cap
        quantity = notional / signal.entry_price
        risk_amount = quantity * stop_distance

    if quantity_step and quantity_step > 0:
        quantity = (quantity // quantity_step) * quantity_step
        notional = quantity * signal.entry_price
        risk_amount = quantity * stop_distance

    if quantity <= 0 or notional < min_notional:
        raise SizingError(
            f"Position of {notional:.4f} USDT is below the exchange minimum ({min_notional}); "
            "increase equity or risk per trade, or widen the stop"
        )

    liq: float | None = None
    if settings.futures_enabled:
        liq = liquidation_price(signal.entry_price, signal.side, leverage)
        distance_pct = 100 * abs(signal.entry_price - liq) / signal.entry_price
        if distance_pct < settings.min_liquidation_distance_pct:
            raise SizingError(
                f"Liquidation would sit {distance_pct:.2f}% away, inside the "
                f"{settings.min_liquidation_distance_pct}% safety margin — lower the leverage"
            )
        stop_beyond_liq = (
            signal.stop_price <= liq if signal.side is Side.LONG else signal.stop_price >= liq
        )
        if stop_beyond_liq:
            raise SizingError("Stop sits beyond the liquidation price; the position is unsafe")

    return SizedOrder(
        symbol=signal.symbol,
        side=signal.side,
        quantity=quantity,
        notional=notional,
        entry_price=signal.entry_price,
        stop_price=signal.stop_price,
        tp1_price=signal.tp1_price,
        tp2_price=signal.tp2_price,
        risk_amount=risk_amount,
        leverage=leverage,
        liquidation_price=liq,
        notes=notes,
    )
