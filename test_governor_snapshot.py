"""Order-book snapshots: KalshiAdapter.get_orderbook and governor.snapshot conversion."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import copy
import unittest

from adapters import KalshiAdapter
from governor import rules
from governor.allocator import decide
from governor.models import Portfolio, PriceLevel, Proposal
from governor.snapshot import build_snapshot, order_book

NOW = datetime(2026, 10, 10, 14, 30, tzinfo=timezone.utc)
TICKER = "KXNFLGAME-26OCT18BALCLE-CLE"

# Recorded from the Kalshi demo exchange on 2026-10-10 (trimmed to the fields used).
MARKET = {"ticker": TICKER, "event_ticker": "KXNFLGAME-26OCT18BALCLE", "title": "Cleveland wins",
          "market_type": "binary", "notional_value_dollars": "1.0000", "status": "active",
          "close_time": "2026-10-20T17:00:00Z", "yes_ask_dollars": "0.3700", "no_ask_dollars": "0.6400"}
EVENT = {"category": "Sports", "series_ticker": "KXNFLGAME"}
SERIES = {"fee_type": "quadratic", "fee_multiplier": 1}
ORDERBOOK = {"orderbook_fp": {
    "yes_dollars": [["0.0100", "137.00"], ["0.3300", "6060.60"], ["0.3400", "7843.04"], ["0.3500", "8095.23"], ["0.3600", "1851.84"]],
    "no_dollars": [["0.5900", "20.00"], ["0.6000", "5019.92"], ["0.6100", "5982.90"], ["0.6200", "6140.26"], ["0.6300", "2702.70"]]}}


def kalshi(market=MARKET, event=EVENT, series=SERIES, orderbook=ORDERBOOK):
    adapter, calls = KalshiAdapter({}), []

    def request(method, path, body=None, public=False):
        calls.append((method, path, public))
        if path.endswith("/orderbook"):
            return copy.deepcopy(orderbook)
        if path.startswith("/markets/"):
            return {"market": dict(market)}
        if path.startswith("/events/"):
            return {"event": dict(event)}
        return {"series": dict(series)}
    adapter.request = request
    return adapter, calls


def payload(**overrides):
    adapter, _ = kalshi()
    raw = adapter.get_orderbook(TICKER)
    raw.update({"asOf": NOW.isoformat(), **overrides})
    return raw


class GetOrderbookTests(unittest.TestCase):
    def test_returns_full_depth_and_market_terms_from_public_endpoints(self):
        adapter, calls = kalshi()
        raw = adapter.get_orderbook(TICKER)
        self.assertEqual(raw["yesBids"][-1], ["0.3600", "1851.84"])
        self.assertEqual(raw["noBids"][-1], ["0.6300", "2702.70"])
        self.assertEqual(len(raw["yesBids"]), 5)
        self.assertEqual((raw["eventTicker"], raw["agent"], raw["open"], raw["executionReady"], raw["feeMultiplier"]),
                         ("KXNFLGAME-26OCT18BALCLE", "sports", True, True, 1.0))
        self.assertEqual(datetime.fromisoformat(raw["asOf"]).tzinfo, timezone.utc)
        self.assertTrue(all(method == "GET" and public for method, _, public in calls))
        self.assertIn(("GET", f"/markets/{TICKER}/orderbook", True), calls)

    def test_empty_or_null_sides_become_empty_lists(self):
        adapter, _ = kalshi(orderbook={"orderbook_fp": {"yes_dollars": None}})
        raw = adapter.get_orderbook(TICKER)
        self.assertEqual((raw["yesBids"], raw["noBids"]), ([], []))
        adapter, _ = kalshi(orderbook={})
        self.assertEqual(adapter.get_orderbook(TICKER)["yesBids"], [])

    def test_unsupported_fee_type_or_closed_market_is_flagged(self):
        adapter, _ = kalshi(series={"fee_type": "flat", "fee_multiplier": 1})
        self.assertFalse(adapter.get_orderbook(TICKER)["executionReady"])
        adapter, _ = kalshi(market={**MARKET, "status": "closed"})
        self.assertFalse(adapter.get_orderbook(TICKER)["open"])

    def test_quote_is_unchanged_by_the_shared_market_lookup(self):
        # get_quote now shares _market with get_orderbook; its output must not change.
        adapter, _ = kalshi()
        quote = adapter.get_quote(TICKER, "No")
        quote.pop("quoteAt")
        self.assertEqual(quote, {
            "id": TICKER, "name": "Cleveland wins", "agent": "sports", "instrument": "prediction", "venue": "kalshi",
            "price": 0.64, "closeAt": "2026-10-20T17:00:00Z", "slippage": 0, "open": True, "direction": "No",
            "executionReady": True, "feeMultiplier": 1.0,
            "executionNote": "Only binary quadratic-fee markets with a valid ask are supported."})


class SnapshotConversionTests(unittest.TestCase):
    def test_book_matches_the_live_quote(self):
        book = order_book(payload())
        self.assertEqual(book.best_ask("yes"), PriceLevel(D("0.37"), D("2702.70")))
        self.assertEqual(book.best_ask("no"), PriceLevel(D("0.64"), D("1851.84")))
        self.assertEqual((book.event_group, book.fee_multiplier, book.is_open, book.is_tradable),
                         ("KXNFLGAME-26OCT18BALCLE", D(1), True, True))
        self.assertEqual(book.taken_at, NOW)

    def test_timestamps_with_z_or_offsets_normalize_to_utc(self):
        self.assertEqual(order_book(payload(asOf="2026-10-10T14:30:00Z")).taken_at, NOW)
        self.assertEqual(order_book(payload(asOf="2026-10-10T10:30:00-04:00")).taken_at, NOW)

    def test_zero_quantity_levels_are_dropped(self):
        book = order_book(payload(noBids=[["0.6300", "0.00"], ["0.6200", "10"]]))
        self.assertEqual(book.best_ask("yes"), PriceLevel(D("0.38"), D("10")))

    def test_malformed_payloads_are_refused(self):
        bad = [payload(asOf="yesterday"), payload(asOf="2026-10-10T14:30:00"), payload(yesBids="0.36"),
               payload(yesBids=[["0.36"]]), payload(yesBids=[["1.2", "5"]]), payload(yesBids=[["0.36", "NaN"]]),
               payload(open="true"), payload(feeMultiplier=None)]
        for raw in bad:
            with self.subTest(raw={k: raw[k] for k in ("asOf", "yesBids", "open", "feeMultiplier")}), self.assertRaises(ValueError):
                order_book(raw)
        incomplete = payload()
        del incomplete["eventTicker"]
        with self.assertRaisesRegex(ValueError, "eventTicker"):
            order_book(incomplete)

    def test_snapshot_groups_and_duplicates(self):
        bal = payload(id="KXNFLGAME-26OCT18BALCLE-BAL")
        snap = build_snapshot([payload(), bal], snapshot_id="s1", taken_at=NOW,
                              event_groups={"KXNFLGAME-26OCT18BALCLE-BAL": "ravens-exposure"})
        self.assertEqual(snap.books[TICKER].event_group, "KXNFLGAME-26OCT18BALCLE")
        self.assertEqual(snap.books["KXNFLGAME-26OCT18BALCLE-BAL"].event_group, "ravens-exposure")
        with self.assertRaisesRegex(ValueError, "twice"):
            build_snapshot([payload(), payload()], snapshot_id="s1", taken_at=NOW)


class SnapshotToDecisionTests(unittest.TestCase):
    def proposal(self):
        return Proposal("p1", "a1", TICKER, "yes", D("0.45"), D(25), NOW - timedelta(seconds=1), NOW + timedelta(seconds=60))

    def test_live_shaped_book_allocates_like_the_live_check(self):
        snap = build_snapshot([payload()], snapshot_id="s1", taken_at=NOW)
        d = decide([self.proposal()], snap, Portfolio(D(1000), D(1000)), {}, NOW)[0]
        # Same result the allocator gave against the live demo book on 2026-10-10.
        self.assertEqual((d.outcome, d.contracts, d.limit_price), ("approved", D(33), D("0.37")))

    def test_untradable_market_is_rejected_with_its_own_reason(self):
        snap = build_snapshot([payload(executionReady=False)], snapshot_id="s1", taken_at=NOW)
        d = decide([self.proposal()], snap, Portfolio(D(1000), D(1000)), {}, NOW)[0]
        self.assertEqual(d.reason_codes, (rules.MARKET_NOT_TRADABLE,))

    def test_closed_market_still_reports_closed_first(self):
        snap = build_snapshot([payload(open=False, executionReady=False)], snapshot_id="s1", taken_at=NOW)
        d = decide([self.proposal()], snap, Portfolio(D(1000), D(1000)), {}, NOW)[0]
        self.assertEqual(d.reason_codes, (rules.MARKET_CLOSED,))


if __name__ == "__main__":
    unittest.main()
