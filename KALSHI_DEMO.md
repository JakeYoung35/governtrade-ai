# Kalshi demo execution script

`kalshi_demo.py` runs locally, once per command. It uses Kalshi's mock-funds
exchange at `https://external-api.demo.kalshi.co/trade-api/v2`. There is no
production switch. The existing browser simulator and its balances are separate.

## Setup

From the repository directory (the inner `governtrade-ai` folder in this workspace):

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-demo.txt
```

Create a [Kalshi demo account](https://demo.kalshi.co/), then create a demo API
key in Account & security → API Keys with portfolio read and order permissions.
Save the private-key file locally, outside the repository. Fill in `.env` beside
`kalshi_demo.py`:

```dotenv
KALSHI_DEMO_API_KEY_ID=YOUR_DEMO_KEY_ID
KALSHI_DEMO_PRIVATE_KEY_PATH="/absolute/path/to/demo-private-key.pem"
```

Then check the connection:

```sh
python kalshi_demo.py balance
```

The script supports RSA and Ed25519 PEM private keys. It automatically reads
the `.env` beside the script, regardless of the terminal's working directory.
Exported environment variables take precedence. Use one `NAME=value` per line,
optional matching quotes, and separate comment lines; shell expansion and
multiline values are not supported. Never paste the private
key into source or chat. `.pem`, `.key`, `.env*`, and `.venv/` are ignored by Git.

## First order

List open demo markets, then copy an actual ticker from the result:

```sh
python kalshi_demo.py markets --limit 10
python kalshi_demo.py quote MARKET_TICKER
```

Market results include a cursor for another page (`markets --cursor CURSOR`).
You can filter with `--series SERIES_TICKER`. Quotes report YES and NO bid, ask,
and ask depth; an empty side is `null`. Demo liquidity and prices differ from
production. A quote is a snapshot, not a fill guarantee.

Preview a payload without credentials, network, or a saved order:

```sh
python kalshi_demo.py buy MARKET_TICKER --side yes --quantity 1 \
  --limit-price 0.40 --client-order-id demo-first-001 --dry-run
```

Submit that intention to the demo exchange by removing `--dry-run`:

```sh
python kalshi_demo.py buy MARKET_TICKER --side yes --quantity 1 \
  --limit-price 0.40 --client-order-id demo-first-001
```

This sends an order immediately. Price is dollars per contract for the selected
side: `--side no --limit-price 0.40` means NO at up to 40 cents, not YES at 40
cents. Quantity is whole contracts in this first version. The exchange validates
the market's tick size and funds. Fees are additional; the dry-run amount is
contract cost before fees, not an all-in spending cap.

The default is `good_till_canceled`: an unfilled order can remain on Kalshi after
the script exits. Optional `--time-in-force immediate_or_cancel` fills available
quantity immediately and cancels the rest; `fill_or_kill` requests an immediate
complete fill or cancellation. Nothing runs on a schedule.

The result includes Kalshi's submission acknowledgment followed by a status
lookup. Read `status`, `fill_count_fp`, and `remaining_count_fp` from the order:
an acknowledgment alone is not a fill. Use the returned venue order ID:

```sh
python kalshi_demo.py status ORDER_ID
python kalshi_demo.py cancel ORDER_ID
```

Cancel affects only the unfilled remainder, never completed fills. The script
obtains the market ticker for cancellation routing. If a follow-up status read
fails, it preserves the successful submission/cancellation response and explains
how to check status later. JSON output is labeled `kalshi-demo`; errors go to
stderr with exit code 1. Argument errors return 2.

## Repeated commands and network failures

Each new trading intention needs a unique `--client-order-id`. If omitted, the
script generates one and prints it to stderr before submission. Prefer an
explicit ID when invoking from another program. Repeating a buy with the same ID
and payload returns its saved journal entry, without sending another order.
Changing the payload under that ID is rejected.

The SQLite journal is `.data/kalshi-demo/orders.sqlite3`, scoped by a hash of the
API key ID. It stores requests and responses, never private keys or signatures.
Keep this file between runs and use the same API key when reconciling.

```sh
python kalshi_demo.py local demo-first-001
python kalshi_demo.py reconcile demo-first-001
python kalshi_demo.py retry demo-first-001
```

`reconcile` searches current venue orders, following pagination when necessary.
`retry` first reconciles and only resubmits an `unknown` request if it was not
found, using the identical payload and client ID. Kalshi deduplicates this ID;
a duplicate response remains unknown locally until reconciliation finds the
order. HTTP 429 and ambiguous server/network errors are not retried automatically.
Wait before an explicit retry. Do not create a new ID to recover a timeout.

An interrupted process can leave `submitting` in the journal. Reconciliation
can recover it if the order exists; retries do not take over an in-flight claim.
If it cannot be found, inspect the demo account before any manual recovery. The
script does not search archived historical orders. A missing current order is
not proof that it never executed. Orders placed elsewhere and API key rotations
do not share the local journal automatically.

## Scope and verification

This is a manual demo execution adapter, not the governor. It does not enforce
the web simulator's policies, choose trades, allocate a portfolio, or reconcile
a separate cash ledger. Opposite-direction orders may reduce existing venue
positions. Production execution, fractional quantities, background polling,
and historical-order recovery are outside this first script.

Run offline tests after installing the demo dependency:

```sh
python -m unittest discover -s . -p 'test_*.py' -v
```

Tests cover actual RSA/Ed25519 signature verification, side/price conversion,
invalid inputs, empty books, persistence before submission, concurrent duplicate
claims, timeouts, reconciliation/retry, and cancellation. They do not contact
Kalshi. A real demo lifecycle still requires your credentials and API access.

## Official API references

- [Demo environment](https://docs.kalshi.com/getting_started/demo_env)
- [Request authentication](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests)
- [V2 order creation](https://docs.kalshi.com/api-reference/orders/create-order-v2)
- [Order status](https://docs.kalshi.com/api-reference/orders/get-order)
- [V2 cancellation](https://docs.kalshi.com/api-reference/orders/cancel-order-v2)
- [Order-book representation](https://docs.kalshi.com/getting_started/orderbook_responses)

Checked 2026-10-09. V2 creation uses the single YES-price book (`bid` for YES;
`ask` at the complementary YES price for NO). Status reads use
`/portfolio/orders/{order_id}`; creation and cancellation use
`/portfolio/events/orders`. No legacy order-creation fallback is attempted.
