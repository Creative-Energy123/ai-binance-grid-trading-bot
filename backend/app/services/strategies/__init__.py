from app.services.strategies.base import Strategy
from app.services.strategies.breakout import BreakoutStrategy
from app.services.strategies.range_scalping import RangeScalpingStrategy
from app.services.strategies.trend_pullback import TrendPullbackStrategy

ALL_STRATEGIES: list[Strategy] = [
    TrendPullbackStrategy(),
    BreakoutStrategy(),
    RangeScalpingStrategy(),
]

__all__ = [
    "ALL_STRATEGIES",
    "BreakoutStrategy",
    "RangeScalpingStrategy",
    "Strategy",
    "TrendPullbackStrategy",
]
