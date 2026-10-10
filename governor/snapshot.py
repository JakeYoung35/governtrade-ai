"""Convert venue order-book payloads into a frozen governor Snapshot.

Pure conversion. The caller fetches each market with VenueAdapter.get_orderbook and
passes the resulting dicts in, so the governor package itself still does no I/O and a
stored set of payloads rebuilds exactly the same snapshot.
"""
from datetime import datetime

from .models import OrderBook, PriceLevel, Snapshot, to_utc
from .money import ZERO, to_decimal

_REQUIRED = ("id", "eventTicker", "open", "executionReady", "feeMultiplier", "yesBids", "noBids", "asOf")


def _levels(rows, name):
    if not isinstance(rows, list):
        raise ValueError(f"{name} must be a list of [price, quantity] pairs.")
    levels = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(f"{name} must be a list of [price, quantity] pairs.")
        quantity = to_decimal(row[1], f"{name} quantity")
        if quantity == ZERO:
            continue  # an emptied level carries no liquidity
        levels.append(PriceLevel(to_decimal(row[0], f"{name} price"), quantity))
    return levels


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("asOf must be an ISO-8601 timestamp string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("asOf must be an ISO-8601 timestamp string.") from None
    return to_utc(parsed, "asOf")


def order_book(raw, event_group=None):
    """OrderBook from one get_orderbook payload.

    event_group defaults to the venue event, so mutually related markets of one game
    share exposure; pass a manual correlation group to override it.
    """
    if not isinstance(raw, dict):
        raise ValueError("Order book payload must be an object.")
    missing = [key for key in _REQUIRED if key not in raw]
    if missing:
        raise ValueError(f"Order book payload is missing {', '.join(missing)}.")
    return OrderBook(
        market_id=raw["id"],
        event_group=event_group or raw["eventTicker"],
        taken_at=_timestamp(raw["asOf"]),
        yes_bids=_levels(raw["yesBids"], "yesBids"),
        no_bids=_levels(raw["noBids"], "noBids"),
        is_open=raw["open"],
        is_tradable=raw["executionReady"],
        fee_multiplier=to_decimal(raw["feeMultiplier"], "feeMultiplier"),
    )


def build_snapshot(raw_books, *, snapshot_id, taken_at, event_groups=None):
    """Snapshot of every payload. event_groups: optional market_id -> correlation group."""
    event_groups = dict(event_groups or {})
    books = {}
    for raw in raw_books:
        market_id = raw.get("id") if isinstance(raw, dict) else None
        book = order_book(raw, event_groups.get(market_id))
        if book.market_id in books:
            raise ValueError(f"Market {book.market_id} appears twice in one snapshot.")
        books[book.market_id] = book
    return Snapshot(snapshot_id, taken_at, books)
