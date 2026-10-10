"""Daily loss circuit breaker: latches when equity falls daily_loss_limit below the day's base.

The base is the UTC day-opening equity (the account's first balance sync of the day),
including unrealized losses. Once latched it stays latched: a new day does not resume
trading, only an operator reset with a stated reason does. A reset re-bases the threshold
on the equity at reset time for the rest of that UTC day, so trading can resume after an
accepted loss while a further decline of the same size latches again.

Pure: callers pass equity, the day-open value and the time; nothing here reads a clock.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .models import to_utc
from .money import ONE, ZERO, to_decimal
from .policy import DEFAULT_POLICY


@dataclass(frozen=True)
class BreakerState:
    latched: bool = False
    latched_at: datetime | None = None
    reason: str | None = None
    rebase_day: str | None = None  # UTC date (YYYY-MM-DD) of the last operator reset
    rebase_equity: Decimal | None = None  # equity at that reset; the base for the rest of that day

    def __post_init__(self):
        if self.latched is not True and self.latched is not False:
            raise ValueError("latched must be a boolean.")
        if self.latched_at is not None:
            object.__setattr__(self, "latched_at", to_utc(self.latched_at, "latched_at"))
        if self.rebase_equity is not None:
            object.__setattr__(self, "rebase_equity", to_decimal(self.rebase_equity, "rebase_equity"))
        if (self.rebase_day is None) != (self.rebase_equity is None):
            raise ValueError("rebase_day and rebase_equity are set together.")

    def to_dict(self):
        return {"latched": self.latched,
                "latchedAt": self.latched_at.isoformat() if self.latched_at else None,
                "reason": self.reason, "rebaseDay": self.rebase_day,
                "rebaseEquity": None if self.rebase_equity is None else str(self.rebase_equity)}

    @classmethod
    def from_dict(cls, data):
        latched_at = data.get("latchedAt")
        return cls(latched=data["latched"],
                   latched_at=datetime.fromisoformat(latched_at) if latched_at else None,
                   reason=data.get("reason"), rebase_day=data.get("rebaseDay"),
                   rebase_equity=data.get("rebaseEquity"))


@dataclass(frozen=True)
class BreakerCheck:
    state: BreakerState
    tripped: bool  # latched by this check (it was not latched before)
    base: Decimal | None  # equity the decline is measured from; None when it cannot be measured
    threshold: Decimal | None  # equity at or below which the breaker latches


def check(state, *, equity, day_open_equity, now, policy=DEFAULT_POLICY):
    """Latch if equity has fallen to the threshold. Unmeasurable inputs never clear a latch.

    day_open_equity may be None (no sync yet today); then only a same-day reset base applies.
    """
    now = to_utc(now, "now")
    if state.latched:
        return BreakerCheck(state, False, None, None)
    day = now.date().isoformat()
    base = state.rebase_equity if state.rebase_day == day else day_open_equity
    if base is None or equity is None:
        return BreakerCheck(state, False, None, None)
    base = to_decimal(base, "day_open_equity")
    equity = to_decimal(equity, "equity")
    if base <= ZERO:
        return BreakerCheck(state, False, None, None)
    threshold = base * (ONE - policy.daily_loss_limit)
    if equity > threshold:
        return BreakerCheck(state, False, base, threshold)
    decline = (base - equity) / base
    reason = (f"Equity ${equity} is {decline * 100:.2f}% below today's base of ${base}; "
              f"the daily loss limit is {policy.daily_loss_limit * 100:.2f}%.")
    latched = BreakerState(latched=True, latched_at=now, reason=reason,
                           rebase_day=state.rebase_day, rebase_equity=state.rebase_equity)
    return BreakerCheck(latched, True, base, threshold)


def reset(state, *, operator, reason, equity, now):
    """Clear a latched breaker. Returns (new_state, audit_record) for the caller to persist."""
    if not state.latched:
        raise ValueError("The loss breaker is not latched.")
    for name, value in (("operator", operator), ("reason", reason)):
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValueError(f"A reset needs a {name} of 1 to 500 characters.")
    now = to_utc(now, "now")
    equity = to_decimal(equity, "equity")
    if equity <= ZERO:
        raise ValueError("A reset needs a current, positive equity valuation.")
    new_state = BreakerState(rebase_day=now.date().isoformat(), rebase_equity=equity)
    audit = {"action": "breakerReset", "at": now.isoformat(), "operator": operator.strip(),
             "reason": reason.strip(), "latchedAt": state.latched_at.isoformat() if state.latched_at else None,
             "latchReason": state.reason, "equityAtReset": str(equity)}
    return new_state, audit
