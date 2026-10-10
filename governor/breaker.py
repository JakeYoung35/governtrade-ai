"""Daily loss circuit breaker: latches when equity falls daily_loss_limit below the day's base.

The base is the UTC day-opening equity (the account's first balance sync of the day),
including unrealized losses. Once latched it stays latched: a new day does not resume
trading, only an operator reset with a stated reason does. For the rest of that UTC day a
reset measures from the lower of the day open and the equity at reset, so trading can resume
after an accepted loss while a further decline of the same size latches again.

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

    The base is the lowest positive value among today's day-open equity and a same-day reset
    value, so a reset taken at high equity never makes the limit stricter than the day open.
    day_open_equity may be None (no sync yet today); with no base at all nothing is measured.
    A base that exists but is not positive latches (fail closed): the limit cannot be measured,
    and an operator reset sets a usable base from current equity.
    """
    now = to_utc(now, "now")
    if state.latched or equity is None:
        return BreakerCheck(state, False, None, None)
    candidates = [to_decimal(day_open_equity, "day_open_equity")] if day_open_equity is not None else []
    if state.rebase_day == now.date().isoformat():
        candidates.append(state.rebase_equity)
    if not candidates:
        return BreakerCheck(state, False, None, None)
    equity = to_decimal(equity, "equity")
    positive = [value for value in candidates if value > ZERO]
    if not positive:
        reason = (f"Today's base equity is ${min(candidates):,.2f}, so the daily loss limit cannot be measured. "
                  "Reset the breaker to measure from current equity.")
        return BreakerCheck(_latch(state, now, reason), True, None, None)
    base = min(positive)
    threshold = base * (ONE - policy.daily_loss_limit)
    if equity > threshold:
        return BreakerCheck(state, False, base, threshold)
    decline = (base - equity) / base
    reason = (f"Equity ${equity:,.2f} is {decline * 100:.2f}% below today's base of ${base:,.2f}; "
              f"the daily loss limit is {policy.daily_loss_limit * 100:.2f}%.")
    return BreakerCheck(_latch(state, now, reason), True, base, threshold)


def _latch(state, now, reason):
    return BreakerState(latched=True, latched_at=now, reason=reason,
                        rebase_day=state.rebase_day, rebase_equity=state.rebase_equity)


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
