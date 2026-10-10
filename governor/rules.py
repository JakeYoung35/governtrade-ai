"""Deterministic eligibility rules and the reason codes every decision reports."""
from datetime import timedelta

from .fees import fee_per_contract

# Portfolio-wide gates: no new risk while any applies.
PAUSED = "paused"
LOSS_BREAKER = "lossBreakerLatched"
STALE_VALUATION = "staleValuation"
NO_EQUITY = "noEquity"
# Per-proposal eligibility.
MARKET_NOT_IN_SNAPSHOT = "marketNotInSnapshot"
MARKET_CLOSED = "marketClosed"
PROPOSAL_EXPIRED = "proposalExpired"
STALE_QUOTE = "staleQuote"
POSITION_OPEN = "positionAlreadyOpen"
NO_LIQUIDITY = "insufficientLiquidity"
PRICE_CEILING = "priceAboveCeiling"
NO_EDGE = "noNetEdge"
# Batch allocation.
CONFLICT = "conflict"
REDUNDANT = "redundantSignal"
CASH_LIMIT = "cashLimit"
MARKET_CAP = "marketExposureCap"
AGENT_CAP = "agentExposureCap"
EVENT_GROUP_CAP = "eventGroupCap"
TOTAL_CAP = "totalExposureCap"
ZERO_SIZE = "zeroSize"

# A quote stamped slightly in the future is clock skew; more than this is untrustworthy.
CLOCK_SKEW = timedelta(seconds=5)


def portfolio_gate(portfolio):
    """First portfolio-wide reason to block all new risk, or None."""
    if portfolio.paused:
        return PAUSED
    if portfolio.breaker_latched:
        return LOSS_BREAKER
    if portfolio.valuation_stale:
        return STALE_VALUATION
    if portfolio.equity <= 0:
        return NO_EQUITY
    return None


def proposal_gate(proposal, book, portfolio, policy, now):
    """First reason this proposal cannot receive capital, or None.

    Checks run cheapest and most fundamental first so the reported reason is the
    one an operator should fix first.
    """
    if book is None:
        return MARKET_NOT_IN_SNAPSHOT
    if not book.is_open:
        return MARKET_CLOSED
    if now >= proposal.expires_at:
        return PROPOSAL_EXPIRED
    age = now - book.taken_at
    if age > timedelta(seconds=policy.max_quote_age_seconds) or age < -CLOCK_SKEW:
        return STALE_QUOTE
    if any(item.market_id == proposal.market_id for item in portfolio.exposures):
        return POSITION_OPEN
    best = book.best_ask(proposal.side)
    if best is None:
        return NO_LIQUIDITY
    if proposal.max_entry_price is not None and best.price > proposal.max_entry_price:
        return PRICE_CEILING
    if proposal.win_probability - (best.price + fee_per_contract(best.price, book.fee_multiplier)) <= 0:
        return NO_EDGE
    return None
