"""Agent reliability from resolved forecasts (Brier score shrunk toward a prior).

historyWeight = n / (n + priorWeight)
observed      = clamp(1 - brier / baseline, 0, 1)
reliability   = (1 - historyWeight) * prior + historyWeight * observed

A bounded risk multiplier for sizing, not a calibrated probability that an agent is right.
Only the first forecast per (agent, market) counts, whether or not it was approved, so
repeated submissions are not independent evidence and governor choices do not bias scores.
"""
from dataclasses import dataclass
from decimal import Decimal

from .money import ONE, ZERO, clamp, to_decimal
from .policy import DEFAULT_POLICY


@dataclass(frozen=True)
class Forecast:
    agent_id: str
    market_id: str
    estimated_yes_probability: Decimal
    outcome: Decimal | None = None  # 1 YES, 0 NO, 0.5 tie; None while unresolved

    def __post_init__(self):
        p = to_decimal(self.estimated_yes_probability, "estimated_yes_probability")
        if not ZERO < p < ONE:
            raise ValueError("estimated_yes_probability must be strictly between 0 and 1.")
        object.__setattr__(self, "estimated_yes_probability", p)
        if self.outcome is not None:
            outcome = to_decimal(self.outcome, "outcome")
            if not ZERO <= outcome <= ONE:
                raise ValueError("outcome must be between 0 and 1.")
            object.__setattr__(self, "outcome", outcome)


@dataclass(frozen=True)
class ReliabilityScore:
    agent_id: str
    score: Decimal
    resolved_count: int
    brier: Decimal | None  # None until at least one forecast resolves


def score_agent(agent_id, forecasts, policy=DEFAULT_POLICY):
    """Reliability for one agent. `forecasts` must be in submission order."""
    first_by_market = {}
    for forecast in forecasts:
        if forecast.agent_id == agent_id:
            first_by_market.setdefault(forecast.market_id, forecast)
    resolved = [f for f in first_by_market.values() if f.outcome is not None]
    if not resolved:
        return ReliabilityScore(agent_id, policy.reliability_prior, 0, None)
    n = len(resolved)
    brier = sum((f.estimated_yes_probability - f.outcome) ** 2 for f in resolved) / n
    observed = clamp(ONE - brier / policy.brier_baseline, ZERO, ONE)
    weight = Decimal(n) / Decimal(n + policy.reliability_prior_weight)
    score = (ONE - weight) * policy.reliability_prior + weight * observed
    return ReliabilityScore(agent_id, score, n, brier)


def score_all(forecasts, policy=DEFAULT_POLICY):
    """agent_id -> ReliabilityScore for every agent that has forecasts."""
    forecasts = list(forecasts)
    agents = dict.fromkeys(f.agent_id for f in forecasts)
    return {agent_id: score_agent(agent_id, forecasts, policy) for agent_id in agents}
