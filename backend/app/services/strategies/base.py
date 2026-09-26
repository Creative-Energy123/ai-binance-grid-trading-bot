from __future__ import annotations

from abc import ABC, abstractmethod

from app.config import Settings
from app.services.contracts import MarketContext, StrategyCandidate


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def scaled(value: float, worst: float, best: float) -> float:
    """Map `value` onto 0..1 between `worst` and `best` (either direction)."""
    if worst == best:
        return 0.0
    return clamp((value - worst) / (best - worst))


class Strategy(ABC):
    name: str = "base"
    enabled_setting: str = ""

    def is_enabled(self, settings: Settings) -> bool:
        return bool(getattr(settings, self.enabled_setting, False))

    @abstractmethod
    def evaluate(self, ctx: MarketContext, settings: Settings) -> StrategyCandidate | None:
        """Return a candidate when this strategy's preconditions hold, else None.

        Candidates carry component scores only — the SignalEngine applies the
        weights and the minimum-score gate.
        """
