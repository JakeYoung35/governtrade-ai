"""Decimal conversion and rounding for every governor amount, price and probability."""
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
FEE_TICK = Decimal("0.0001")  # Kalshi demo charged $0.0164 for 1 @ $0.37 (raw 0.016317).


def to_decimal(value, name="value"):
    """Convert an external number to a finite Decimal.

    Floats go through repr so 0.1 becomes Decimal("0.1"), not its binary expansion.
    Booleans are rejected because bool is an int subclass and True would read as 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{name} must be a number.")
    try:
        result = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{name} must be a number.") from None
    if not result.is_finite():
        raise ValueError(f"{name} must be finite.")
    return result


def round_down(value, step):
    """Largest multiple of step that is <= value."""
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def round_up(value, step):
    """Smallest multiple of step that is >= value."""
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def clamp(value, low, high):
    return max(low, min(high, value))
