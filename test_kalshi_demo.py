"""Offline contract tests. No credentials, network, or venue orders are used."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

import kalshi_demo as demo


def payload(**overrides):
    values = dict(ticker="TEST-MARKET", side="yes", quantity=2, limit_price="0.40", client_id="test-order")
    values.update(overrides)
    return demo.order_payload(**values)


class PayloadTests(unittest.TestCase):
    def test_yes_and_no_prices(self):
        yes, no = payload(), payload(side="no")
        self.assertEqual((yes["side"], yes["price"]), ("bid", "0.4000"))
        self.assertEqual((no["side"], no["price"]), ("ask", "0.6000"))
        self.assertEqual(no["count"], "2.00")

    def test_reject_invalid_inputs(self):
        for value in ("NaN", "Infinity", "-0.1", "0", "1", "0.00001", "bad"):
            with self.subTest(price=value), self.assertRaises(demo.DemoError):
                payload(limit_price=value)
        for value in (0, -1, True, 1.5, 1000001):
            with self.subTest(quantity=value), self.assertRaises(demo.DemoError):
                payload(quantity=value)
        for value in ("../balance", "X?foo=1", "https://example.com", ""):
            with self.subTest(ticker=value), self.assertRaises(demo.DemoError):
                payload(ticker=value)

    def test_empty_book_does_not_invent_prices(self):
        result = demo.quote_summary({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
        self.assertIsNone(result["yes"]["ask"])
        self.assertIsNone(result["no"]["bid"])

    def test_quotes_keep_complementary_depth(self):
        result = demo.quote_summary({"orderbook_fp": {
            "yes_dollars": [["0.42", "13.00"], ["0.41", "9.00"]],
            "no_dollars": [["0.56", "17.00"]]}})
        self.assertEqual(result["yes"], {"bid": "0.42", "ask": "0.44", "ask_quantity": "17.00"})
        self.assertEqual(result["no"]["ask"], "0.58")

    def test_dry_run_never_requests_network(self):
        with patch.object(demo.DemoClient, "request") as request, redirect_stdout(io.StringIO()) as out:
            code = demo.main(["buy", "TEST", "--side", "no", "--quantity", "1", "--limit-price", "0.40", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["result"]["dry_run"])
        request.assert_not_called()


class AuthenticationTests(unittest.TestCase):
    def test_env_file_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text('# comment\nKALSHI_DEMO_API_KEY_ID=file-id\nKALSHI_DEMO_PRIVATE_KEY_PATH="/tmp/key with spaces.pem"\nUNRELATED=ignored\n')
            with patch.object(demo, "ENV_PATH", env), patch.dict("os.environ", {}, clear=True):
                client = demo.DemoClient()
                self.assertEqual(client.key_id, "file-id")
                self.assertEqual(client.key_path, "/tmp/key with spaces.pem")
                self.assertNotIn("UNRELATED", demo.read_demo_env())
                with patch.dict("os.environ", {"KALSHI_DEMO_API_KEY_ID": "shell-id"}):
                    self.assertEqual(demo.DemoClient().key_id, "shell-id")
                    self.assertEqual(demo.DemoClient(key_id="explicit-id").key_id, "explicit-id")

    def test_env_parse_error_does_not_expose_value(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text('KALSHI_DEMO_API_KEY_ID="secret-value\n')
            with self.assertRaises(demo.DemoError) as error:
                demo.read_demo_env(env)
            self.assertNotIn("secret-value", str(error.exception))

    def test_signatures_verify_full_path_without_query(self):
        for key in (ed25519.Ed25519PrivateKey.generate(), rsa.generate_private_key(65537, 2048)):
            with self.subTest(key=type(key).__name__), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "key.pem"
                path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                client = demo.DemoClient("test-key", str(path))
                headers = client.headers("GET", "/trade-api/v2/portfolio/orders?limit=10")
                message = (headers["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/v2/portfolio/orders").encode()
                signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
                if isinstance(key, rsa.RSAPrivateKey):
                    key.public_key().verify(signature, message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
                else:
                    key.public_key().verify(signature, message)

    def test_missing_credentials_actionable(self):
        with patch.object(demo, "read_demo_env", return_value={}), patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(demo.DemoError, "KALSHI_DEMO_API_KEY_ID"):
            demo.DemoClient().headers("GET", "/trade-api/v2/portfolio/balance")

    def test_public_request_demo_url_and_timeout(self):
        client = demo.DemoClient()
        client.opener = Mock()
        client.opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b'{"markets": []}'))
        client.opener.open.return_value.__exit__ = Mock(return_value=False)
        client.request("GET", "/markets", params={"status": "open"}, auth=False)
        args, kwargs = client.opener.open.call_args
        self.assertEqual(args[0].full_url, demo.BASE_URL + "/trade-api/v2/markets?status=open")
        self.assertFalse(any("kalshi-access" in key.lower() for key in args[0].headers))
        self.assertEqual(kwargs["timeout"], 15)

    def test_no_redirects_or_implicit_post_retry(self):
        self.assertIsNone(demo.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.example"))
        client = demo.DemoClient()
        client.headers = Mock(return_value={})
        client.opener = Mock()
        client.opener.open.side_effect = URLError("offline")
        with self.assertRaises(demo.DemoError):
            client.request("POST", demo.ORDER_PATH, payload=payload())
        self.assertEqual(client.opener.open.call_count, 1)

    def test_http_errors_do_not_echo_sensitive_body(self):
        client = demo.DemoClient()
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError("demo", 401, "secret", {}, io.BytesIO(b"secret"))
        with self.assertRaises(demo.APIError) as error:
            client.request("GET", "/markets", auth=False)
        self.assertNotIn("secret", str(error.exception))


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "orders.sqlite3"
        self.journal = demo.Journal("test-key", self.path)
        self.client = Mock(spec=demo.DemoClient)
        self.client.key_id = "test-key"
        self.client.request.return_value = {"order_id": "venue-1", "fill_count": "0.00", "remaining_count": "2.00"}
        self.client.get_order.return_value = {"order_id": "venue-1", "ticker": "TEST-MARKET", "status": "resting"}
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.journal.close)

    def test_submission_acknowledgement_is_not_a_fill(self):
        with redirect_stderr(io.StringIO()):
            result = demo.submit(self.client, self.journal, payload())
        self.assertEqual(result["order"]["status"], "resting")
        self.assertEqual(self.journal.get("test-order")["state"], "acknowledged")
        self.assertEqual(self.journal.get("test-order")["response"]["order_id"], "venue-1")

    def test_duplicate_and_changed_payload(self):
        with redirect_stderr(io.StringIO()):
            demo.submit(self.client, self.journal, payload())
            duplicate = demo.submit(self.client, self.journal, payload())
        self.assertFalse(duplicate["submitted_now"])
        self.assertEqual(self.client.request.call_count, 1)
        with self.assertRaisesRegex(demo.DemoError, "conflicts"):
            demo.submit(self.client, self.journal, payload(side="no"))

    def test_claim_is_saved_before_network_and_survives_restart(self):
        def request(*args, **kwargs):
            other = demo.Journal("test-key", self.path)
            try:
                self.assertEqual(other.get("test-order")["state"], "submitting")
            finally:
                other.close()
            raise demo.DemoError("timeout")
        self.client.request.side_effect = request
        with redirect_stderr(io.StringIO()), self.assertRaises(demo.DemoError):
            demo.submit(self.client, self.journal, payload())
        self.assertEqual(self.journal.get("test-order")["state"], "unknown")

    def test_failed_journal_write_never_submits(self):
        with patch.object(self.journal, "claim", side_effect=demo.sqlite3.OperationalError("disk")), self.assertRaises(demo.sqlite3.Error):
            demo.submit(self.client, self.journal, payload())
        self.client.request.assert_not_called()

    def test_unknown_retry_uses_identical_id_and_payload(self):
        self.journal.claim(payload())
        self.journal.record("test-order", "unknown", {"error": "timeout"})
        self.client.find_order.return_value = None
        with patch.object(demo, "Journal", return_value=self.journal), patch.object(self.journal, "close"), redirect_stderr(io.StringIO()):
            result = demo.run(demo.parser().parse_args(["retry", "test-order"]), self.client)
        self.assertTrue(result["submitted_now"])
        self.client.request.assert_called_once_with("POST", demo.ORDER_PATH, payload=payload())

    def test_reconcile_finds_unknown_without_resubmitting(self):
        self.journal.claim(payload())
        self.journal.record("test-order", "unknown", {})
        self.client.find_order.return_value = {"order_id": "found-1", "client_order_id": "test-order"}
        with patch.object(demo, "Journal", return_value=self.journal), patch.object(self.journal, "close"):
            result = demo.run(demo.parser().parse_args(["retry", "test-order"]), self.client)
        self.assertTrue(result["found"])
        self.client.request.assert_not_called()

    def test_inflight_retry_does_not_take_over(self):
        self.journal.claim(payload())
        self.client.find_order.return_value = None
        with patch.object(demo, "Journal", return_value=self.journal), patch.object(self.journal, "close"), self.assertRaisesRegex(demo.DemoError, "in progress"):
            demo.run(demo.parser().parse_args(["retry", "test-order"]), self.client)
        self.client.request.assert_not_called()

    def test_status_failure_preserves_acknowledged_order(self):
        self.client.get_order.side_effect = demo.DemoError("status lag")
        with redirect_stderr(io.StringIO()):
            result = demo.submit(self.client, self.journal, payload())
        self.assertIn("status_notice", result)
        self.assertEqual(self.journal.get("test-order")["state"], "acknowledged")

    def test_status_lag_after_fill_is_retried(self):
        # Seen live on 2026-10-10: the fill was acknowledged, then the first status read returned 404.
        filled = {"order_id": "venue-1", "ticker": "TEST-MARKET", "status": "executed"}
        self.client.get_order.side_effect = [demo.APIError(404, "Market/order not found."), filled]
        with redirect_stderr(io.StringIO()), patch.object(demo.time, "sleep") as sleep:
            result = demo.submit(self.client, self.journal, payload())
        self.assertEqual(result["order"]["status"], "executed")
        self.assertNotIn("status_notice", result)
        sleep.assert_called_once_with(demo.STATUS_LAG_DELAYS[0])

    def test_persistent_status_lag_is_bounded_and_explained(self):
        self.client.get_order.side_effect = demo.APIError(404, "Market/order not found.")
        with redirect_stderr(io.StringIO()), patch.object(demo.time, "sleep") as sleep:
            result = demo.submit(self.client, self.journal, payload())
        self.assertEqual(self.client.get_order.call_count, len(demo.STATUS_LAG_DELAYS) + 1)
        self.assertEqual(sleep.call_count, len(demo.STATUS_LAG_DELAYS))
        self.assertIn("not visible yet", result["status_notice"])
        self.assertIn("venue-1", result["status_notice"])
        self.assertEqual(self.journal.get("test-order")["state"], "acknowledged")

    def test_other_status_errors_are_not_retried(self):
        self.client.get_order.side_effect = demo.APIError(401, "Check demo credentials and system clock.")
        with redirect_stderr(io.StringIO()), patch.object(demo.time, "sleep") as sleep:
            result = demo.submit(self.client, self.journal, payload())
        self.client.get_order.assert_called_once()
        sleep.assert_not_called()
        self.assertIn("401", result["status_notice"])

    def test_cancel_follow_up_retries_status_lag(self):
        resting = {"order_id": "venue-1", "ticker": "TEST-MARKET", "status": "resting"}
        canceled = dict(resting, status="canceled")
        self.client.get_order.side_effect = [resting, demo.APIError(404, "Market/order not found."), canceled]
        self.client.request.return_value = {"order_id": "venue-1", "reduced_by": "2.00"}
        with patch.object(demo.time, "sleep"):
            result = demo.run(demo.parser().parse_args(["cancel", "venue-1"]), self.client)
        self.assertEqual(result["order"]["status"], "canceled")
        self.assertNotIn("status_notice", result)

    def test_status_command_does_not_mask_unknown_orders(self):
        # A user-typed order ID may simply be wrong; only reads after our own write wait out lag.
        self.client.get_order.side_effect = demo.APIError(404, "Market/order not found.")
        with patch.object(demo.time, "sleep") as sleep, self.assertRaises(demo.APIError):
            demo.run(demo.parser().parse_args(["status", "venue-1"]), self.client)
        sleep.assert_not_called()

    def test_rejection_and_duplicate_conflict_are_distinct(self):
        for status, expected in ((400, "rejected"), (409, "unknown"), (429, "unknown"), (503, "unknown")):
            p = payload(client_id=f"order-{status}")
            self.client.request.side_effect = demo.APIError(status, "test")
            with redirect_stderr(io.StringIO()), self.assertRaises(demo.APIError):
                demo.submit(self.client, self.journal, p)
            self.assertEqual(self.journal.get(p["client_order_id"])["state"], expected)

    def test_concurrent_claim_has_one_winner(self):
        def claim(_):
            journal = demo.Journal("test-key", self.path)
            try:
                return journal.claim(payload())
            finally:
                journal.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(results), 1)

    def test_cancel_routes_by_ticker_and_preserves_partial_fill(self):
        self.client.get_order.return_value.update(fill_count_fp="1.00", remaining_count_fp="1.00")
        self.client.request.return_value = {"order_id": "venue-1", "reduced_by": "1.00"}
        result = demo.run(demo.parser().parse_args(["cancel", "venue-1"]), self.client)
        self.client.request.assert_called_once_with("DELETE", demo.ORDER_PATH + "/venue-1", params={"market_ticker": "TEST-MARKET"})
        self.assertEqual(result["order"]["fill_count_fp"], "1.00")

    def test_terminal_order_is_not_cancelled_again(self):
        self.client.get_order.return_value["status"] = "executed"
        result = demo.run(demo.parser().parse_args(["cancel", "venue-1"]), self.client)
        self.assertFalse(result["cancelled_now"])
        self.client.request.assert_not_called()

    def test_reconciliation_paginates(self):
        client = demo.DemoClient()
        client.request = Mock(side_effect=[{"orders": [], "cursor": "next"},
                                          {"orders": [{"client_order_id": "test-order", "order_id": "found"}]}])
        self.assertEqual(client.find_order("test-order")["order_id"], "found")
        self.assertEqual(client.request.call_args.kwargs["params"]["cursor"], "next")


if __name__ == "__main__":
    unittest.main()
