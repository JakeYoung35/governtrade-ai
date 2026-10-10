#!/usr/bin/env python3
"""Standalone Kalshi demo execution client. No production endpoint or simulator state."""

import argparse
import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid

BASE_URL = "https://external-api.demo.kalshi.co"
API_ROOT = "/trade-api/v2"
ORDER_PATH = "/portfolio/events/orders"
JOURNAL_PATH = Path(__file__).resolve().parent / ".data/kalshi-demo/orders.sqlite3"
ENV_PATH = Path(__file__).resolve().parent / ".env"
# Order reads can briefly 404 right after a successful create or cancel. Bounded wait, about 3.75 s total.
STATUS_LAG_DELAYS = (0.25, 0.5, 1.0, 2.0)


class DemoError(Exception):
    """An actionable CLI error, without credentials or request headers."""


class APIError(DemoError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(f"Kalshi demo HTTP {status}: {detail}")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_demo_env(path=None):
    """Read only demo credentials; do not execute or expand file contents."""
    path = Path(path) if path is not None else ENV_PATH
    if not path.exists():
        return {}
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if name not in ("KALSHI_DEMO_API_KEY_ID", "KALSHI_DEMO_PRIVATE_KEY_PATH"):
            continue
        value = value.strip()
        if not separator:
            raise DemoError(f"Invalid demo .env entry on line {number}; expected NAME=value.")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise DemoError(f"Unmatched quote in demo .env on line {number}.")
            value = value[1:-1]
        values[name] = value
    return values


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise DemoError("Identifiers must contain only letters, digits, _, ., :, or -.")
    if value in (".", ".."):
        raise DemoError("Invalid identifier.")
    return value


def order_payload(ticker, side, quantity, limit_price, client_id, tif="good_till_canceled"):
    identifier(ticker)
    identifier(client_id)
    if side not in ("yes", "no"):
        raise DemoError("Side must be yes or no.")
    if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
        raise DemoError("Quantity must be a whole number from 1 to 1,000,000.")
    try:
        price = Decimal(str(limit_price))
        valid = price.is_finite() and 0 < price < 1 and price == price.quantize(Decimal("0.0001"))
    except InvalidOperation:
        valid = False
    if not valid:
        raise DemoError("Limit price must be between 0 and 1 dollar, with at most four decimal places.")
    if tif not in ("good_till_canceled", "immediate_or_cancel", "fill_or_kill"):
        raise DemoError("Unsupported time in force.")
    # V2 orders use a single YES-price book: buying NO at .40 is an ask at .60.
    return {
        "ticker": ticker, "side": "bid" if side == "yes" else "ask",
        "price": format(price if side == "yes" else 1 - price, ".4f"),
        "count": f"{quantity}.00", "client_order_id": client_id,
        "time_in_force": tif, "self_trade_prevention_type": "taker_at_cross",
        "cancel_order_on_pause": True,
    }


class DemoClient:
    def __init__(self, key_id=None, key_path=None):
        config = read_demo_env()
        self.key_id = key_id if key_id is not None else os.environ.get("KALSHI_DEMO_API_KEY_ID", config.get("KALSHI_DEMO_API_KEY_ID", ""))
        self.key_path = key_path if key_path is not None else os.environ.get("KALSHI_DEMO_PRIVATE_KEY_PATH", config.get("KALSHI_DEMO_PRIVATE_KEY_PATH", ""))
        self._key = None
        self.opener = build_opener(NoRedirects())

    def headers(self, method, path):
        if not self.key_id or not self.key_path:
            raise DemoError("Set KALSHI_DEMO_API_KEY_ID and KALSHI_DEMO_PRIVATE_KEY_PATH. See KALSHI_DEMO.md.")
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
        except ImportError as exc:
            raise DemoError("Install dependencies: python -m pip install -r requirements-demo.txt") from exc
        if self._key is None:
            try:
                self._key = serialization.load_pem_private_key(Path(self.key_path).expanduser().read_bytes(), password=None)
            except (OSError, ValueError, TypeError) as exc:
                raise DemoError("Cannot load the demo private key. Use an unencrypted PEM private-key file.") from exc
        timestamp = str(time.time_ns() // 1_000_000)
        message = (timestamp + method.upper() + path.split("?")[0]).encode()
        if isinstance(self._key, ed25519.Ed25519PrivateKey):
            signature = self._key.sign(message)
        elif isinstance(self._key, rsa.RSAPrivateKey):
            signature = self._key.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        else:
            raise DemoError("The demo key must be RSA or Ed25519.")
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": timestamp,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode()}

    def request(self, method, path, *, params=None, payload=None, auth=True):
        if not path.startswith("/") or path.startswith("//") or "?" in path or ".." in path:
            raise DemoError("Invalid API path.")
        full_path = API_ROOT + path
        headers = {"Accept": "application/json", "User-Agent": "GovernTrade-demo/1.0"}
        if auth:
            headers.update(self.headers(method, full_path))
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, allow_nan=False).encode()
        url = BASE_URL + full_path + ("?" + urlencode(params) if params else "")
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=15) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            # Do not echo arbitrary response text, which may contain request information.
            hints = {400: "Invalid order or market parameters.", 401: "Check demo credentials and system clock.",
                     403: "API key lacks permission.", 404: "Market/order not found.",
                     409: "Client order ID already exists; reconcile before placing another order.",
                     429: "Rate limited; wait before retrying."}
            raise APIError(exc.code, hints.get(exc.code, "Request failed; reconcile mutations before retrying.")) from exc
        except (URLError, TimeoutError, OSError) as exc:
            detail = " Check your connection and retry."
            if method != "GET":
                detail = " The mutation may have succeeded; reconcile its saved order ID before retrying."
            raise DemoError("Network request failed or timed out." + detail) from exc
        except (ValueError, UnicodeError) as exc:
            raise DemoError("Kalshi returned invalid JSON; a submitted order may still exist.") from exc
        if not isinstance(result, dict):
            raise DemoError("Unexpected API response; reconcile any submitted order.")
        return result

    def get_order(self, order_id):
        result = self.request("GET", "/portfolio/orders/" + identifier(order_id))
        if not isinstance(result.get("order"), dict):
            raise DemoError("Order response is missing its order object.")
        return result["order"]

    def find_order(self, client_id):
        cursor = None
        seen = set()
        while True:
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            page = self.request("GET", "/portfolio/orders", params=params)
            if not isinstance(page.get("orders"), list):
                raise DemoError("Order list response is missing orders.")
            for order in page["orders"]:
                if order.get("client_order_id") == client_id:
                    return order
            cursor = page.get("cursor")
            if not cursor:
                return None
            if cursor in seen:
                raise DemoError("Repeated order cursor; reconciliation incomplete.")
            seen.add(cursor)


class Journal:
    """Durable claims prevent concurrent local commands from submitting the same ID."""
    def __init__(self, key_id, path=JOURNAL_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.scope = hashlib.sha256(key_id.encode()).hexdigest()
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS orders (
            scope TEXT NOT NULL, client_id TEXT NOT NULL, payload TEXT NOT NULL,
            state TEXT NOT NULL, response TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY(scope, client_id))""")
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, client_id):
        row = self.db.execute("SELECT * FROM orders WHERE scope=? AND client_id=?", (self.scope, client_id)).fetchone()
        if row is None:
            raise DemoError("Client order ID not in this key's local journal.")
        return {**dict(row), "payload": json.loads(row["payload"]),
                "response": json.loads(row["response"]) if row["response"] else None}

    def claim(self, payload, retry=False):
        client_id = payload["client_order_id"]
        encoded = json.dumps(payload, sort_keys=True)
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM orders WHERE scope=? AND client_id=?", (self.scope, client_id)).fetchone()
            if row:
                if row["payload"] != encoded:
                    raise DemoError("Client order ID conflicts with a different order payload.")
                if not retry or row["state"] != "unknown":
                    return False
                self.db.execute("UPDATE orders SET state='submitting', updated_at=? WHERE scope=? AND client_id=?", (now, self.scope, client_id))
            else:
                self.db.execute("INSERT INTO orders VALUES (?, ?, ?, 'submitting', NULL, ?)", (self.scope, client_id, encoded, now))
        return True

    def record(self, client_id, state, response):
        with self.db:
            self.db.execute("UPDATE orders SET state=?, response=?, updated_at=? WHERE scope=? AND client_id=?",
                            (state, json.dumps(response), datetime.now(timezone.utc).isoformat(), self.scope, client_id))


def get_order_after_write(client, order_id):
    """Read an order we just created or changed, waiting out the venue's brief read lag."""
    for delay in STATUS_LAG_DELAYS + (None,):
        try:
            return client.get_order(order_id)
        except APIError as exc:
            if exc.status != 404:
                raise
            if delay is None:
                raise DemoError(f"Order {order_id} is not visible yet (Kalshi read lag). "
                                f"Check later with: status {order_id}") from exc
            time.sleep(delay)


def submit(client, journal, payload, retry=False):
    # Load credentials before claiming so setup failures never look like submitted orders.
    client.headers("POST", API_ROOT + ORDER_PATH)
    if not journal.claim(payload, retry=retry):
        return {"submitted_now": False, "journal": journal.get(payload["client_order_id"])}
    client_id = payload["client_order_id"]
    print(f"Demo client order ID: {client_id} (saved before submission)", file=sys.stderr)
    try:
        response = client.request("POST", ORDER_PATH, payload=payload)
        if not response.get("order_id"):
            raise DemoError("Submission response has no order ID; reconcile using the client order ID.")
    except DemoError as exc:
        rejected = isinstance(exc, APIError) and exc.status in (400, 401, 403, 404, 422)
        journal.record(client_id, "rejected" if rejected else "unknown", {"error": str(exc)})
        raise
    journal.record(client_id, "acknowledged", response)
    result = {"submitted_now": True, "client_order_id": client_id, "submission": response}
    try:
        result["order"] = get_order_after_write(client, response["order_id"])
    except DemoError as exc:
        result["status_notice"] = f"Submission acknowledged; status lookup failed: {exc}"
    return result


def reconcile(client, journal, client_id):
    row = journal.get(client_id)
    response = row["response"] or {}
    order = client.get_order(response["order_id"]) if response.get("order_id") else client.find_order(client_id)
    if order:
        journal.record(client_id, "acknowledged", order)
        return {"found": True, "order": order}
    return {"found": False, "journal_state": row["state"],
            "notice": "Not found in current orders. This is not proof of rejection; the API can lag and older orders may be historical. Do not generate a new ID."}


def quote_summary(result):
    book = result.get("orderbook_fp")
    if not isinstance(book, dict):
        raise DemoError("Expected the fixed-point orderbook_fp response.")
    best = {}
    for side in ("yes", "no"):
        levels = book.get(side + "_dollars", [])
        parsed = []
        for price, count in levels:
            p, q = Decimal(price), Decimal(count)
            if not p.is_finite() or not q.is_finite() or not 0 <= p <= 1 or q < 0:
                raise DemoError("Invalid order-book price or depth.")
            if q:
                parsed.append((p, q))
        best[side] = max(parsed, default=None)
    result = {}
    for side, opposite in (("yes", "no"), ("no", "yes")):
        bid, ask = best[side], best[opposite]
        result[side] = {"bid": str(bid[0]) if bid else None,
                        "ask": str(1 - ask[0]) if ask else None,
                        "ask_quantity": str(ask[1]) if ask else None}
    return result


def parser():
    p = argparse.ArgumentParser(description="Kalshi DEMO only — mock-funds execution. See KALSHI_DEMO.md.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("balance", help="Read demo cash balance and portfolio value")
    markets = sub.add_parser("markets", help="List one page of open demo markets")
    markets.add_argument("--limit", type=int, default=20)
    markets.add_argument("--cursor")
    markets.add_argument("--series")
    sub.add_parser("quote", help="Read a demo market and executable quotes").add_argument("ticker")
    buy = sub.add_parser("buy", help="Submit a demo limit order")
    buy.add_argument("ticker")
    buy.add_argument("--side", required=True, choices=("yes", "no"))
    buy.add_argument("--quantity", required=True, type=int)
    buy.add_argument("--limit-price", required=True, help="Dollars per contract for the selected YES/NO side")
    buy.add_argument("--client-order-id", help="Stable ID for this intention; reuse on repeated commands")
    buy.add_argument("--time-in-force", default="good_till_canceled",
                     choices=("good_till_canceled", "immediate_or_cancel", "fill_or_kill"))
    buy.add_argument("--dry-run", action="store_true", help="Validate and print payload without credentials or network")
    sub.add_parser("status", help="Read actual venue order status").add_argument("order_id")
    sub.add_parser("cancel", help="Cancel only an order's unfilled remainder").add_argument("order_id")
    sub.add_parser("reconcile", help="Look up a saved submission by client ID").add_argument("client_order_id")
    sub.add_parser("retry", help="Reconcile, then retry an unknown submission with its original ID").add_argument("client_order_id")
    sub.add_parser("local", help="Inspect a saved submission without contacting Kalshi").add_argument("client_order_id")
    return p


def run(args, client):
    cmd = args.command
    if cmd == "balance":
        data = client.request("GET", "/portfolio/balance")
        cash = data.get("balance_dollars")
        return {"balance_dollars": str(Decimal(str(cash)) if cash is not None else Decimal(str(data["balance"])) / 100),
                "portfolio_value_dollars": str(Decimal(str(data["portfolio_value"])) / 100), "raw": data}
    if cmd == "markets":
        if not 1 <= args.limit <= 1000:
            raise DemoError("Market limit must be between 1 and 1000.")
        params = {"status": "open", "limit": args.limit}
        if args.cursor:
            params["cursor"] = args.cursor
        if args.series:
            params["series_ticker"] = identifier(args.series)
        return client.request("GET", "/markets", params=params, auth=False)
    if cmd == "quote":
        path = "/markets/" + identifier(args.ticker)
        market = client.request("GET", path, auth=False)
        book = client.request("GET", path + "/orderbook", auth=False)
        return {"market": market["market"], "quotes": quote_summary(book), "raw_orderbook": book}
    if cmd == "status":
        return {"order": client.get_order(args.order_id)}
    if cmd == "cancel":
        order = client.get_order(args.order_id)
        if order.get("status") in ("executed", "canceled"):
            return {"cancelled_now": False, "order": order}
        ticker = identifier(order.get("ticker"))
        response = client.request("DELETE", ORDER_PATH + "/" + identifier(args.order_id), params={"market_ticker": ticker})
        result = {"cancellation": response, "notice": "Only unfilled contracts were cancelled; existing fills remain."}
        try:
            result["order"] = get_order_after_write(client, args.order_id)
        except DemoError as exc:
            result["status_notice"] = str(exc)
        return result
    if cmd == "buy":
        payload = order_payload(args.ticker, args.side, args.quantity, args.limit_price,
                                args.client_order_id or str(uuid.uuid4()), args.time_in_force)
        if args.dry_run:
            return {"dry_run": True, "payload": payload,
                    "limit_cost_before_fees": str(Decimal(args.limit_price) * args.quantity)}
    if not client.key_id:
        raise DemoError("Set KALSHI_DEMO_API_KEY_ID to select the local journal account.")
    journal = Journal(client.key_id)
    try:
        if cmd == "buy":
            # Repeated IDs are handled before fetching market state: never change prior results.
            try:
                prior = journal.get(payload["client_order_id"])
            except DemoError:
                prior = None
            if prior:
                if prior["payload"] != payload:
                    raise DemoError("Client order ID conflicts with a different order payload.")
                return {"submitted_now": False, "journal": prior}
            market = client.request("GET", "/markets/" + payload["ticker"], auth=False)["market"]
            if market.get("status") not in ("active", "open"):
                raise DemoError("The selected demo market is not open.")
            return submit(client, journal, payload)
        client_id = identifier(args.client_order_id)
        if cmd == "local":
            return {"journal": journal.get(client_id)}
        result = reconcile(client, journal, client_id)
        if cmd == "retry" and not result["found"]:
            row = journal.get(client_id)
            if row["state"] == "submitting":
                raise DemoError("Submission is in progress or the process stopped during submission. Reconcile later; no automatic takeover is allowed.")
            if row["state"] != "unknown":
                raise DemoError("Only an unknown submission can be retried. Inspect the saved result.")
            return submit(client, journal, row["payload"], retry=True)
        return result
    finally:
        journal.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args, DemoClient())
        print(json.dumps({"environment": "kalshi-demo", "result": result}, indent=2, allow_nan=False))
        return 0
    except (DemoError, OSError, sqlite3.Error, InvalidOperation, KeyError, TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
