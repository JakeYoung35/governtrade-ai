"""Versioned governor policy. Every decision records the version that produced it.

Defaults are the MVP brief's proposed values, not tuned or confirmed trading limits.
Caps are fractions of portfolio equity measured once at the start of a batch.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal

from .money import ONE, ZERO, to_decimal

_FRACTIONS = ("kelly_fraction", "market_cap", "agent_cap", "event_group_cap", "total_cap",
              "daily_loss_limit", "reliability_prior")
_POSITIVE_INTS = ("max_quote_age_seconds", "default_proposal_ttl_seconds", "reliability_prior_weight")


@dataclass(frozen=True)
class Policy:
    version: str = "governor-policy-v1"
    kelly_fraction: Decimal = Decimal("0.25")
    market_cap: Decimal = Decimal("0.05")
    agent_cap: Decimal = Decimal("0.15")
    event_group_cap: Decimal = Decimal("0.10")
    total_cap: Decimal = Decimal("0.30")
    # Not read by the pure allocator. The loss breaker (planned governor/breaker.py) latches
    # Portfolio.breaker_latched when equity falls this far below the UTC day-open value,
    # and the allocator then rejects everything with lossBreakerLatched.
    daily_loss_limit: Decimal = Decimal("0.03")
    max_quote_age_seconds: int = 30
    # Not read by the pure allocator. Worker-proposal parsing (planned governor/batch.py)
    # sets Proposal.expires_at = received_at + this when a worker omits expiresAt.
    default_proposal_ttl_seconds: int = 60
    reliability_prior: Decimal = Decimal("0.5")
    reliability_prior_weight: int = 20
    brier_baseline: Decimal = Decimal("0.25")  # Brier score of a constant 50% forecast

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("Policy version is required.")
        for name in _FRACTIONS:
            value = to_decimal(getattr(self, name), name)
            if not ZERO < value <= ONE:
                raise ValueError(f"{name} must be in (0, 1].")
            object.__setattr__(self, name, value)
        for name in _POSITIVE_INTS:
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        baseline = to_decimal(self.brier_baseline, "brier_baseline")
        if baseline <= ZERO:
            raise ValueError("brier_baseline must be positive.")
        object.__setattr__(self, "brier_baseline", baseline)

    def to_dict(self):
        def camel(name):
            head, *rest = name.split("_")
            return head + "".join(part.title() for part in rest)
        return {camel(k): (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


DEFAULT_POLICY = Policy()
