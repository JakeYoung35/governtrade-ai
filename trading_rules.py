"""The user's trading rules: one catalog that defines, explains and validates every rule the user sets.

There are no preset values. A workspace cannot trade until its owner has set every rule (the first-login setup),
and every trade check and governor batch reads the values the user chose. The catalog is served to the browser,
so the setup screen, the Trading Rules page and the server-side validation use the same names, ranges and
explanations.

Values are stored in the units the user types (percent, dollars, seconds, a count) and converted for the governor
by to_policy(). Each save increments the rules version; every governor decision records the version it used.
"""
from decimal import Decimal
import math

from governor.policy import Policy

GROUPS = [
    {"id": "sizing", "title": "Bet sizing",
     "intro": "How big each trade is before any cap applies."},
    {"id": "caps", "title": "Exposure caps (share of your account)",
     "intro": "Limits on how much of your account can be at risk, as a percentage of your account value (cash plus "
              "open positions) when each batch of proposals is decided. 0% stops new trades of that kind."},
    {"id": "dollars", "title": "Dollar limits",
     "intro": "Fixed dollar limits checked on every order, on top of the percentage caps. The lower limit wins."},
    {"id": "loss", "title": "Loss protection",
     "intro": "When losses stop new trading."},
    {"id": "timing", "title": "Timing",
     "intro": "How fresh prices must be, and how long proposals and approvals stay valid."},
    {"id": "trust", "title": "How agents earn trust",
     "intro": "Each agent's forecasts are scored once markets settle. Trust scales its bet sizes: a well-calibrated "
              "agent bets more, a poor one less."},
]

RULES = [
    {"key": "kellyMultiplier", "group": "sizing", "label": "Kelly multiplier", "unit": "percent", "min": 1, "max": 100,
     "explanation": "The Kelly criterion is the bet size that grows an account fastest when the agent's probability "
                    "is right. Betting the full amount is very volatile, so most traders bet a fraction of it. This is "
                    "that fraction. Lower is safer and slower.",
     "example": "At 25%, if full Kelly says to bet $40, the governor bets $10 (before caps and the agent's trust)."},
    {"key": "marketCapPercent", "group": "caps", "label": "Per-market cap", "unit": "percent", "min": 0, "max": 100,
     "explanation": "The most of your account that can be at risk in any single market.",
     "example": "At 5% with a $1,000 account, no more than $50 can be at risk in one market."},
    {"key": "agentCapPercent", "group": "caps", "label": "Per-agent cap", "unit": "percent", "min": 0, "max": 100,
     "explanation": "The most of your account any one agent can have at risk, across all its markets.",
     "example": "At 15% with a $1,000 account, each agent can have at most $150 at risk."},
    {"key": "eventCapPercent", "group": "caps", "label": "Per-event cap", "unit": "percent", "min": 0, "max": 100,
     "explanation": "The most at risk on one event. An event can have several markets that depend on each other, "
                    "such as both teams in one game, so this stops correlated bets from piling up.",
     "example": "At 10% with a $1,000 account, all markets on one game together are capped at $100."},
    {"key": "totalCapPercent", "group": "caps", "label": "Total exposure cap", "unit": "percent", "min": 0, "max": 100,
     "explanation": "The most of your account that can be at risk at once, across every agent and market.",
     "example": "At 30% with a $1,000 account, total open risk stays at or below $300; the rest stays in cash."},
    {"key": "dailySpendingCeiling", "group": "dollars", "label": "Daily spending ceiling", "unit": "dollars", "min": 0,
     "max": 1000000000,
     "explanation": "The most all agents together can spend in a day (UTC). Each agent's own daily cap must fit "
                    "under it. A connected account can have a lower ceiling of its own.",
     "example": "At $100, once agents have spent $100 today, every further trade is blocked until tomorrow."},
    {"key": "marketDollarLimit", "group": "dollars", "label": "Per-market dollar limit", "unit": "dollars", "min": 0,
     "max": 1000000000,
     "explanation": "The most that can be committed to one market in total, in dollars, including earlier trades.",
     "example": "At $50, after $40 in one market only $10 more can go into it."},
    {"key": "exposureDollarLimit", "group": "dollars", "label": "Open exposure dollar limit", "unit": "dollars",
     "min": 0, "max": 1000000000,
     "explanation": "The most that can be at risk across all open positions and unconfirmed orders, in dollars.",
     "example": "At $500 with $480 at risk, a $30 trade is blocked."},
    {"key": "dailyLossDollarLimit", "group": "loss", "label": "Daily loss dollar limit", "unit": "dollars", "min": 0,
     "max": 1000000000,
     "explanation": "New trades are blocked once today's loss in an account reaches this many dollars.",
     "example": "At $50, if the account is down $50 since the start of the day, no new trades until tomorrow."},
    {"key": "lossBreakerPercent", "group": "loss", "label": "Loss breaker", "unit": "percent", "min": 0.1, "max": 100,
     "explanation": "If your account value falls this far below its value at the start of the day, the governor "
                    "stops all new trades and stays stopped until you reset it.",
     "example": "At 3% with a $1,000 start, trading stops when the account drops to $970."},
    {"key": "quoteMaxAgeSeconds", "group": "timing", "label": "Price freshness", "unit": "seconds", "min": 1,
     "max": 600, "integer": True,
     "explanation": "The oldest a market price or order book can be when a trade is decided or placed. Older prices "
                    "are refused, so trades never use stale data.",
     "example": "At 30 seconds, a price read 45 seconds ago is refused and the proposal is rejected."},
    {"key": "proposalLifetimeSeconds", "group": "timing", "label": "Proposal lifetime", "unit": "seconds", "min": 1,
     "max": 3600, "integer": True,
     "explanation": "How long an agent's proposal stays valid when the agent does not set its own expiry. Proposals "
                    "not decided in time are rejected.",
     "example": "At 60 seconds, a proposal waiting longer than a minute for the governor expires."},
    {"key": "approvalWindowSeconds", "group": "timing", "label": "Approval window", "unit": "seconds", "min": 60,
     "max": 86400, "integer": True,
     "explanation": "How long a trade sized by the governor waits for your Approve. After that its sizing is out of "
                    "date, so a late approval cancels it instead of trading.",
     "example": "At 900 seconds (15 minutes), approving after 20 minutes cancels the trade."},
    {"key": "startingTrustPercent", "group": "trust", "label": "Starting trust", "unit": "percent", "min": 1,
     "max": 99,
     "explanation": "How much a new agent's forecasts are trusted before any have been scored. 50% is neutral; lower "
                    "makes new agents bet less until they prove themselves.",
     "example": "At 50%, a new agent's bets are neither scaled up nor down for its (empty) track record."},
    {"key": "trustWeight", "group": "trust", "label": "How fast trust changes", "unit": "count", "min": 1,
     "max": 1000, "integer": True,
     "explanation": "How many scored forecasts it takes before an agent's own record counts as much as the starting "
                    "trust. Lower reacts faster to results; higher needs more evidence.",
     "example": "At 20, after 20 scored forecasts the agent's record and the starting trust count equally."},
]
KEYS = [r["key"] for r in RULES]
BY_KEY = {r["key"]: r for r in RULES}

# Checks that are always on. They protect data and order integrity, so they are explained but not configurable.
ALWAYS_ON = [
    {"label": "Market must be open", "explanation": "Closed or settled markets cannot be traded."},
    {"label": "Binary markets only", "explanation": "Only yes/no markets with a valid price can be traded by the app."},
    {"label": "$1 minimum order", "explanation": "Kalshi orders below $1 are not placed."},
    {"label": "Fresh account balance", "explanation": "Every order needs a balance read from Kalshi just before it, "
                                                      "so cash and positions are never guessed."},
    {"label": "Enough cash", "explanation": "An order must fit the available cash, after unconfirmed orders are held back."},
    {"label": "Live-money confirmation",
     "explanation": "Kalshi Live accounts must be confirmed as real money before any order."},
]

# The values the app used before rules were user-set. They are never applied automatically: existing users see them
# as "your previous value" during setup, and tests use them as a fixture.
LEGACY_VALUES = {
    "kellyMultiplier": 25, "marketCapPercent": 5, "agentCapPercent": 15, "eventCapPercent": 10, "totalCapPercent": 30,
    "dailySpendingCeiling": 100, "marketDollarLimit": 50, "exposureDollarLimit": 500, "dailyLossDollarLimit": 50,
    "lossBreakerPercent": 3, "quoteMaxAgeSeconds": 30, "proposalLifetimeSeconds": 60, "approvalWindowSeconds": 900,
    "startingTrustPercent": 50, "trustWeight": 20,
}
# Where a workspace from before user-set rules kept the values it now shows as hints.
_LEGACY_STATE = {"dailySpendingCeiling": ("permissions", "dailyCap"), "marketDollarLimit": ("safety", "marketCap"),
                 "exposureDollarLimit": ("safety", "exposureCap"), "dailyLossDollarLimit": ("safety", "dailyLossCap")}


def catalog():
    """Everything the browser needs to show and explain the rules."""
    return {"groups": GROUPS, "rules": RULES, "alwaysOn": ALWAYS_ON}


def unset(previous=None):
    """The rules record of a workspace whose owner has not set rules yet."""
    return {"version": 0, "setAt": None, "values": None, "previous": previous}


def migrate(s):
    """Give a workspace its rules record. One created before user-set rules gets none, so its owner must set them
    before trading; its old values are kept only as hints. The old fixed limits are dropped."""
    if "rules" not in s:
        previous = None
        if "safety" in s:
            previous = dict(LEGACY_VALUES)
            for key, (section, name) in _LEGACY_STATE.items():
                old = (s.get(section) or {}).get(name)
                if isinstance(old, (int, float)) and not isinstance(old, bool):
                    previous[key] = old
        s["rules"] = unset(previous)
    s.pop("safety", None)
    (s.get("permissions") or {}).pop("dailyCap", None)
    return s


def ready(s):
    return bool((s.get("rules") or {}).get("values"))


def values(s):
    """The user's rule values. Raises ValueError if they have not been set."""
    if not ready(s):
        raise ValueError("Set your trading rules first.")
    return s["rules"]["values"]


def validate(raw):
    """Every rule, within its range. Returns clean values; raises ValueError naming each problem."""
    if not isinstance(raw, dict):
        raise ValueError("Send every trading rule.")
    problems = [f"Unknown rule {k}." for k in sorted(set(raw) - set(KEYS))]
    clean = {}
    for rule in RULES:
        v = raw.get(rule["key"])
        if v is None or v == "":
            problems.append(f"{rule['label']} is required.")
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            problems.append(f"{rule['label']} must be a number.")
        elif rule.get("integer") and v != int(v):
            problems.append(f"{rule['label']} must be a whole number.")
        elif not rule["min"] <= v <= rule["max"]:
            problems.append(f"{rule['label']} must be between {_show(rule, rule['min'])} and {_show(rule, rule['max'])}.")
        else:
            clean[rule["key"]] = int(v) if rule.get("integer") else (round(v, 2) if rule["unit"] == "dollars" else v)
    if problems:
        raise ValueError(" ".join(problems))
    return clean


def save(s, raw, now):
    """Validate and store a new rules version. Returns the labels of the rules that changed."""
    clean = validate(raw)
    old = (s.get("rules") or {}).get("values") or {}
    changed = [BY_KEY[k]["label"] for k in KEYS if old.get(k) != clean[k]]
    s["rules"] = {"version": (s.get("rules") or {}).get("version", 0) + 1, "setAt": now, "values": clean,
                  "previous": None}
    return changed


def to_policy(s):
    """The governor policy for this workspace's rules, or None if they have not been set."""
    if not ready(s):
        return None
    v = s["rules"]["values"]
    pct = lambda key: Decimal(str(v[key])) / 100
    return Policy(version=f"user-rules-v{s['rules']['version']}", kelly_fraction=pct("kellyMultiplier"),
                  market_cap=pct("marketCapPercent"), agent_cap=pct("agentCapPercent"),
                  event_group_cap=pct("eventCapPercent"), total_cap=pct("totalCapPercent"),
                  daily_loss_limit=pct("lossBreakerPercent"), max_quote_age_seconds=v["quoteMaxAgeSeconds"],
                  default_proposal_ttl_seconds=v["proposalLifetimeSeconds"],
                  approval_ttl_seconds=v["approvalWindowSeconds"], reliability_prior=pct("startingTrustPercent"),
                  reliability_prior_weight=v["trustWeight"])


def fixture(s, overrides=None):
    """Set LEGACY_VALUES (plus overrides) as this workspace's rules. For tests and scripted demos only."""
    save(s, {**LEGACY_VALUES, **(overrides or {})}, "2026-01-01T00:00:00+00:00")
    return s


def _show(rule, v):
    return {"percent": f"{v}%", "dollars": f"${v:,}", "seconds": f"{v} seconds"}.get(rule["unit"], str(v))
