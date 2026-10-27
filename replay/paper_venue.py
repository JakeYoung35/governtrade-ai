"""Paper venue for the offline replay demo: fills against recorded Kalshi order books.

Simulated and labeled as such: balances report source "Paper replay" and mock=True. It is never
registered in adapters.ADAPTERS; only scripts/replay_demo.py and its test construct it.

Orders fill immediately-or-cancel against the recorded depth at or below their limit, with exact
Kalshi taker fees, and fills consume the book's liquidity. Positions are marked at the best bid
for their side. set_price() scripts a market move and settle() pays out a scripted result.
"""
import copy
import datetime
import itertools
import json
from pathlib import Path

from adapters import VenueError
from governor.fees import taker_fee
from governor.money import ONE, ZERO, round_down, to_decimal

FIXTURE = Path(__file__).parent / "fixtures" / "nfl_week7_books.json"
SOURCE = "Paper replay"
_LEGACY_FEE_RESERVE = to_decimal("0.03")  # KalshiAdapter's conservative per-contract reserve for ungoverned orders


class PaperVenue:
    venue = "kalshi"

    def __init__(self, clock, cash="1000", fixture=FIXTURE):
        self.clock = clock
        recorded = json.loads(Path(fixture).read_text())
        self.books = copy.deepcopy(recorded["books"])
        # Replay the books as if recorded now: the app checks close times against the real clock, so the
        # recorded ones would otherwise pass and every paper order would be blocked as a closed market.
        shift = clock() - datetime.datetime.fromisoformat(recorded["recordedAt"])
        for book in self.books.values():
            book["closeAt"] = (datetime.datetime.fromisoformat(book["closeAt"]) + shift).isoformat()
        self.cash = to_decimal(cash, "cash")
        self.positions = {}  # market_id -> {"yes": qty, "no": qty, "cost": dollars, "fees": dollars}
        self.outcomes = {}  # market_id -> YES settlement value (string) once settled
        self.realized = ZERO
        self.order_ids = itertools.count(1)

    # Book helpers. Prices and quantities stay as Decimal internally; payloads keep Kalshi's strings.

    def _book(self, market_id):
        if market_id not in self.books:
            raise VenueError("kalshi returned HTTP 404. Check credentials, environment and permissions.")
        return self.books[market_id]

    def _levels(self, market_id, key):
        return [(to_decimal(p, "price"), to_decimal(q, "quantity")) for p, q in self._book(market_id)[key]]

    def _asks(self, market_id, side):
        """Executable buy ladder for side, cheapest first (YES ask = 1 - NO bid)."""
        opposing = "noBids" if side == "yes" else "yesBids"
        return sorted(((ONE - p, q) for p, q in self._levels(market_id, opposing) if q > ZERO), key=lambda pq: pq[0])

    def _best_bid(self, market_id, side):
        bids = [p for p, q in self._levels(market_id, "yesBids" if side == "yes" else "noBids") if q > ZERO]
        return max(bids) if bids else ZERO

    def _consume(self, market_id, side, ask_price, quantity):
        opposing = "noBids" if side == "yes" else "yesBids"
        bid = ONE - ask_price
        for level in self.books[market_id][opposing]:
            if to_decimal(level[0], "price") == bid:
                level[1] = format(to_decimal(level[1], "quantity") - quantity, "f")

    # Venue interface used by Workspace and GovernorService.

    def get_orderbook(self, market_id):
        raw = copy.deepcopy(self._book(market_id))
        raw["open"] = raw["open"] and market_id not in self.outcomes
        raw["asOf"] = self.clock().isoformat()
        return raw

    def get_quote(self, market_id, direction):
        book = self._book(market_id)
        asks = self._asks(market_id, "yes" if direction == "Yes" else "no")
        price = float(asks[0][0]) if asks else 0.0
        return {"id": market_id, "name": book["name"], "agent": book["agent"], "instrument": "prediction",
                "venue": self.venue, "price": price, "quoteAt": self.clock().isoformat(), "closeAt": book["closeAt"],
                "slippage": 0, "open": book["open"] and market_id not in self.outcomes, "direction": direction,
                "executionReady": 0 < price < 1, "feeMultiplier": book["feeMultiplier"],
                "executionNote": "Paper replay: fills against recorded order-book depth."}

    def place_order(self, order):
        market = order["market"]
        side = "yes" if order["direction"] == "Yes" else "no"
        terms = order.get("governedTerms")
        if terms:
            wanted, limit = to_decimal(terms["contracts"], "contracts"), to_decimal(terms["limitPrice"], "limitPrice")
        else:  # mirror KalshiAdapter's ungoverned sizing
            limit = to_decimal(market["price"], "price")
            wanted = round_down(to_decimal(order["amount"], "amount") / (limit + _LEGACY_FEE_RESERVE), ONE)
        multiplier = to_decimal(market.get("feeMultiplier", 1), "feeMultiplier")
        filled = cost = fees = ZERO
        for price, available in self._asks(market["id"], side):
            if price > limit or filled >= wanted:
                break
            take = round_down(min(available, wanted - filled), ONE)
            fee = taker_fee(take, price, multiplier)
            if take <= ZERO or cost + price * take + fee > self.cash:
                break
            self._consume(market["id"], side, price, take)
            filled, cost, fees = filled + take, cost + price * take + fee, fees + fee
        response = {"order_id": f"paper-{next(self.order_ids)}", "fill_count": format(filled, "f")}
        if filled:
            self.cash -= cost
            position = self.positions.setdefault(market["id"], {"yes": ZERO, "no": ZERO, "cost": ZERO, "fees": ZERO})
            position[side] += filled
            position["cost"] += cost
            position["fees"] += fees
            side_price = (cost - fees) / filled
            response.update(average_fill_price=str(side_price if side == "yes" else ONE - side_price),
                            average_fee_paid=str(fees / filled))
        return response

    def cancel_orders(self, ids):
        return []

    def get_market_outcome(self, market_id):
        self._book(market_id)
        outcome = self.outcomes.get(market_id)
        return {"id": market_id, "status": "finalized" if outcome is not None else "active", "outcome": outcome}

    def get_balance_positions(self):
        value = exposure = unrealized = ZERO
        rows = []
        for market_id, p in sorted(self.positions.items()):
            marked = p["yes"] * self._best_bid(market_id, "yes") + p["no"] * self._best_bid(market_id, "no")
            value, exposure, unrealized = value + marked, exposure + p["cost"], unrealized + marked - p["cost"]
            rows.append({"ticker": market_id, "position_fp": format(p["yes"] - p["no"], "f"),
                         "market_exposure_dollars": format(p["cost"], "f"), "realized_pnl_dollars": "0",
                         "fees_paid_dollars": format(p["fees"], "f")})
        return {"externalDailySpend": 0.0, "cash": float(self.cash), "reserved": 0.0, "reservedSource": SOURCE,
                "positionValue": float(value), "exposure": float(exposure), "realizedPnl": float(self.realized),
                "unrealizedPnl": float(unrealized), "positions": rows, "asOf": self.clock().isoformat(),
                "source": SOURCE, "mock": True, "subaccount": 0, "pnlMethod": "Paper ledger marked at best bid."}

    def test_connection(self):
        return self.get_balance_positions()

    # Demo scripting.

    def set_price(self, market_id, yes_bid, no_bid, depth="5000"):
        """Script a market move: one bid level per side (YES ask = 1 - no_bid)."""
        book = self._book(market_id)
        book["yesBids"] = [[format(to_decimal(yes_bid, "yes_bid"), "f"), depth]]
        book["noBids"] = [[format(to_decimal(no_bid, "no_bid"), "f"), depth]]

    def settle(self, market_id, outcome):
        """Pay out a scripted result (YES settlement value: 1, 0, or e.g. 0.5) and close the market."""
        value = to_decimal(outcome, "outcome")
        self.outcomes[market_id] = format(value, "f")
        position = self.positions.pop(market_id, None)
        if position:
            payout = position["yes"] * value + position["no"] * (ONE - value)
            self.cash += payout
            self.realized += payout - position["cost"]
