"""Fractional Kelly target stake for a long binary contract with a $1 payout.

With costs paid up front, full Kelly commits (p - c) / (1 - c) of bankroll, where p is
the win probability and c the all-in cost per contract. This is a constrained sizing
heuristic per proposal, not a joint portfolio optimizer.
"""
from .money import ONE, ZERO


def full_kelly(win_probability, effective_cost):
    """Full-Kelly bankroll fraction; zero for invalid costs or nonpositive net edge."""
    if not ZERO < effective_cost < ONE:
        return ZERO
    return max(ZERO, (win_probability - effective_cost) / (ONE - effective_cost))


def target_stake(*, equity, win_probability, effective_cost, reliability, requested_budget, kelly_fraction):
    """Dollars to commit before portfolio caps: equity * fraction * reliability * full Kelly,
    never more than the agent requested."""
    if equity <= ZERO:
        return ZERO
    kelly = full_kelly(win_probability, effective_cost)
    return min(requested_budget, equity * kelly_fraction * reliability * kelly)
