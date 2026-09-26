"""Shared dataclasses passed between engine stages.

MarketDataService -> IndicatorEngine -> MarketRegimeEngine -> SignalEngine ->
RiskEngine -> PositionSizer -> ExecutionEngine -> OrderManager -> PortfolioManager
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.services.indicators import IndicatorSnapshot
from app.services.regime import RegimeAssessment


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class MarketContext:
    """Everything one symbol's decision is made from, at one point in time."""

    symbol: str
    price: float
    primary: IndicatorSnapshot
    setup: IndicatorSnapshot
    entry: IndicatorSnapshot
    regime: RegimeAssessment
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    spread_pct: float | None = None
    quote_volume_24h: float | None = None


@dataclass
class ScoreBreakdown:
    """Component scores are 0..1; weighted into a 0..100 total by SignalEngine."""

    trend: float = 0.0
    momentum: float = 0.0
    volume: float = 0.0
    volatility: float = 0.0
    structure: float = 0.0
    entry: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return dict(self.__dict__)


@dataclass
class StrategyCandidate:
    strategy: str
    side: Side
    scores: ScoreBreakdown
    reasons: list[str] = field(default_factory=list)
    stop_reference: float | None = None  # structural level this setup invalidates at


@dataclass
class TradeSignal:
    symbol: str
    side: Side
    strategy: str
    score: float
    max_score: float
    scores: ScoreBreakdown
    weighted: dict[str, float]
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    risk_pct: float
    reward_risk: float
    regime: str
    reasons: list[str] = field(default_factory=list)
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def stop_distance(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def stop_distance_pct(self) -> float:
        if not self.entry_price:
            return 0.0
        return 100 * self.stop_distance / self.entry_price

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "strategy": self.strategy,
            "score": round(self.score, 2),
            "max_score": self.max_score,
            "scores": self.scores.as_dict(),
            "weighted": {k: round(v, 2) for k, v in self.weighted.items()},
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "risk_pct": self.risk_pct,
            "reward_risk": round(self.reward_risk, 2),
            "stop_distance_pct": round(self.stop_distance_pct, 4),
            "regime": self.regime,
            "reasons": self.reasons,
            "as_of": self.as_of.isoformat(),
        }


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, float] = field(default_factory=dict)


@dataclass
class SizedOrder:
    symbol: str
    side: Side
    quantity: float
    notional: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    risk_amount: float
    leverage: int = 1
    liquidation_price: float | None = None
    notes: list[str] = field(default_factory=list)
