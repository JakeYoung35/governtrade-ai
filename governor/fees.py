"""Kalshi taker fee model.

fee = round_up(0.07 * multiplier * contracts * price * (1 - price), $0.0001)

Verified against live demo fills on 2026-10-10: 1 contract at $0.37 paid $0.0164 and
1 at $0.36 paid $0.0162. Series can override the multiplier; KalshiAdapter.get_quote
already reports it as feeMultiplier.
"""
from decimal import Decimal

from .money import FEE_TICK, ONE, ZERO, round_up

TAKER_RATE = Decimal("0.07")


def taker_fee(contracts, price, multiplier=ONE):
    """All-in taker fee in dollars for one fill of `contracts` at `price`."""
    if contracts < ZERO:
        raise ValueError("Contract count cannot be negative.")
    if not ZERO < price < ONE:
        raise ValueError("Price must be strictly between 0 and 1.")
    if multiplier < ZERO:
        raise ValueError("Fee multiplier cannot be negative.")
    if contracts == ZERO:
        return ZERO
    return round_up(TAKER_RATE * multiplier * contracts * price * (ONE - price), FEE_TICK)


def fee_per_contract(price, multiplier=ONE):
    """Conservative per-contract fee for sizing: the rounded fee of a single contract.

    Rounding up per contract is never cheaper than the rounded fee of a larger fill,
    so sizing with this value cannot understate cost.
    """
    return taker_fee(ONE, price, multiplier)
