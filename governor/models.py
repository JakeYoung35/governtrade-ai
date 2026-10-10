"""Immutable inputs and outputs of a governor decision.

Prices are dollars per contract for the named side, with a $1 winning payout.
`estimated_yes_probability` always describes YES, whatever side is proposed.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .money import ONE, ZERO, to_decimal

SIDES = ("yes", "no")
OUTCOMES = ("approved", "resized", "rejected")


def _set(obj, name, value):
    object.__setattr__(obj, name, value)


def _require_id(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{name} must be a non-empty string of at most 200 characters.")


def _require_utc(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime.")


def _require_price(value, name):
    if not ZERO < value < ONE:
        raise ValueError(f"{name} must be strictly between 0 and 1.")


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    agent_id: str
    market_id: str
    side: str
    estimated_yes_probability: Decimal
    requested_budget: Decimal  # maximum all-in USD the agent asks for, not an entitlement
    received_at: datetime
    expires_at: datetime
    max_entry_price: Decimal | None = None

    def __post_init__(self):
        for name in ("proposal_id", "agent_id", "market_id"):
            _require_id(getattr(self, name), name)
        if self.side not in SIDES:
            raise ValueError("side must be 'yes' or 'no'.")
        p = to_decimal(self.estimated_yes_probability, "estimated_yes_probability")
        _require_price(p, "estimated_yes_probability")
        _set(self, "estimated_yes_probability", p)
        budget = to_decimal(self.requested_budget, "requested_budget")
        if budget <= ZERO:
            raise ValueError("requested_budget must be positive.")
        _set(self, "requested_budget", budget)
        if self.max_entry_price is not None:
            ceiling = to_decimal(self.max_entry_price, "max_entry_price")
            _require_price(ceiling, "max_entry_price")
            _set(self, "max_entry_price", ceiling)
        _require_utc(self.received_at, "received_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.received_at:
            raise ValueError("expires_at must be after received_at.")

    @property
    def win_probability(self):
        """Probability that the proposed side pays $1."""
        p = self.estimated_yes_probability
        return p if self.side == "yes" else ONE - p


@dataclass(frozen=True)
class PriceLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self):
        price = to_decimal(self.price, "price")
        _require_price(price, "price")
        quantity = to_decimal(self.quantity, "quantity")
        if quantity <= ZERO:
            raise ValueError("quantity must be positive.")
        _set(self, "price", price)
        _set(self, "quantity", quantity)


@dataclass(frozen=True)
class OrderBook:
    """Bids for both outcomes, as Kalshi reports them. Asks are derived, never invented."""
    market_id: str
    event_group: str
    taken_at: datetime
    yes_bids: tuple = ()
    no_bids: tuple = ()
    is_open: bool = True
    fee_multiplier: Decimal = ONE
    contract_step: Decimal = ONE

    def __post_init__(self):
        _require_id(self.market_id, "market_id")
        _require_id(self.event_group, "event_group")
        _require_utc(self.taken_at, "taken_at")
        _set(self, "yes_bids", tuple(self.yes_bids))
        _set(self, "no_bids", tuple(self.no_bids))
        if not all(isinstance(level, PriceLevel) for level in self.yes_bids + self.no_bids):
            raise ValueError("Book levels must be PriceLevel values.")
        if self.is_open is not True and self.is_open is not False:
            raise ValueError("is_open must be a boolean.")
        multiplier = to_decimal(self.fee_multiplier, "fee_multiplier")
        step = to_decimal(self.contract_step, "contract_step")
        if multiplier < ZERO or step <= ZERO:
            raise ValueError("fee_multiplier must be >= 0 and contract_step > 0.")
        _set(self, "fee_multiplier", multiplier)
        _set(self, "contract_step", step)

    def asks(self, side):
        """Executable buy ladder for `side`, cheapest first.

        Buying YES lifts NO bids at the complementary price (YES ask = 1 - NO bid),
        and vice versa, keeping the opposing bid's depth.
        """
        if side not in SIDES:
            raise ValueError("side must be 'yes' or 'no'.")
        opposing = self.no_bids if side == "yes" else self.yes_bids
        return tuple(sorted((PriceLevel(ONE - level.price, level.quantity) for level in opposing),
                            key=lambda level: level.price))

    def best_ask(self, side):
        ladder = self.asks(side)
        return ladder[0] if ladder else None


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    taken_at: datetime
    books: dict = field(default_factory=dict)  # market_id -> OrderBook

    def __post_init__(self):
        _require_id(self.snapshot_id, "snapshot_id")
        _require_utc(self.taken_at, "taken_at")
        for market_id, book in self.books.items():
            if not isinstance(book, OrderBook) or book.market_id != market_id:
                raise ValueError("Snapshot books must be keyed by their own market_id.")


@dataclass(frozen=True)
class Exposure:
    """Remaining loss-at-risk (cost including fees) of an open position or pending order."""
    market_id: str
    agent_id: str
    event_group: str
    side: str
    amount: Decimal

    def __post_init__(self):
        for name in ("market_id", "agent_id", "event_group"):
            _require_id(getattr(self, name), name)
        if self.side not in SIDES:
            raise ValueError("side must be 'yes' or 'no'.")
        amount = to_decimal(self.amount, "amount")
        if amount < ZERO:
            raise ValueError("Exposure amount cannot be negative.")
        _set(self, "amount", amount)


@dataclass(frozen=True)
class Portfolio:
    equity: Decimal  # cash + reserved + marked position value, fixed for the whole batch
    available_cash: Decimal
    exposures: tuple = ()
    paused: bool = False
    breaker_latched: bool = False
    valuation_stale: bool = False

    def __post_init__(self):
        _set(self, "equity", to_decimal(self.equity, "equity"))
        _set(self, "available_cash", to_decimal(self.available_cash, "available_cash"))
        if self.available_cash < ZERO:
            raise ValueError("available_cash cannot be negative.")
        _set(self, "exposures", tuple(self.exposures))
        if not all(isinstance(item, Exposure) for item in self.exposures):
            raise ValueError("exposures must be Exposure values.")
        for name in ("paused", "breaker_latched", "valuation_stale"):
            if getattr(self, name) is not True and getattr(self, name) is not False:
                raise ValueError(f"{name} must be a boolean.")


@dataclass(frozen=True)
class Decision:
    proposal_id: str
    agent_id: str
    market_id: str
    side: str
    outcome: str
    approved_budget: Decimal  # exact all-in cost of the approved contracts
    contracts: Decimal
    limit_price: Decimal | None
    reason_codes: tuple
    reliability: Decimal
    policy_version: str
    snapshot_id: str
    trace: dict

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError("Unknown decision outcome.")
        if (self.outcome == "rejected") != (self.contracts == ZERO):
            raise ValueError("Rejected decisions have zero contracts; approved ones have more.")

    def to_dict(self):
        """JSON-safe form with exact decimal strings, for persistence and the API."""
        def text(value):
            return None if value is None else str(value)
        return {
            "proposalId": self.proposal_id, "agentId": self.agent_id, "marketId": self.market_id,
            "side": self.side, "decision": self.outcome, "approvedBudgetUsd": text(self.approved_budget),
            "contracts": text(self.contracts), "limitPrice": text(self.limit_price),
            "reasonCodes": list(self.reason_codes), "reliabilityScore": text(self.reliability),
            "policyVersion": self.policy_version, "snapshotId": self.snapshot_id, "trace": self.trace,
        }
