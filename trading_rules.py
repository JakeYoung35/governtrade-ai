"""The user's trading rules: one catalog that defines, explains and validates every rule the user sets.

Every money rule is a percentage of the account's current value (cash, reserved cash and marked positions), and
every rule has a starting position. A workspace still cannot trade until its owner has reviewed and saved the rules
on the setup screen. The catalog is served to the browser, so the sliders, their live previews and the server-side
validation use the same names, ranges and explanations.

Agent mandates are percentages of the agent's account value too (MANDATE_FIELDS), with their own starting positions.
Each save increments the rules version; every governor decision records the version it used.

Schema 2 (this one) replaced the dollar-based schema 1. Workspaces saved under schema 1 have their rules converted
to percentages once, from the account values they had, and must review and save them again (see migrate()).
"""
from decimal import Decimal
import math
import numbers

from governor.policy import Policy

SCHEMA = 2

GROUPS = [
    {"id": "sizing", "title": "Bet sizing",
     "intro": "How big each trade is before any cap applies."},
    {"id": "caps", "title": "Exposure caps",
     "intro": "How much of your account can be at risk. Each cap is a share of your account's current value; 0% "
              "stops new trades of that kind."},
    {"id": "daily", "title": "Daily limits",
     "intro": "How much can be spent, and how much can be lost, in one day (UTC)."},
    {"id": "timing", "title": "Timing",
     "intro": "How fresh prices must be, and how long proposals and approvals stay valid."},
    {"id": "trust", "title": "How agents earn trust",
     "intro": "Each agent's forecasts are scored once markets settle. Trust scales its bet sizes: a well-calibrated "
              "agent bets more, a poor one less."},
]

# start: the slider's starting position. scale: "log" for ranges spanning orders of magnitude, so small values are
# easy to reach. step: the slider's step; the number box accepts any value in range (whole numbers if integer).
RULES = [
    {"key": "kellyMultiplier", "group": "sizing", "label": "Kelly multiplier", "unit": "percent", "min": 1, "max": 100,
     "start": 25, "step": 1,
     "explanation": "The Kelly criterion is the bet size that grows an account fastest when the agent's probability "
                    "is right. Betting the full amount is very volatile, so most traders bet a fraction of it. This is "
                    "that fraction. Lower is safer and slower."},
    {"key": "marketCapPercent", "group": "caps", "label": "Per-market cap", "unit": "percent", "min": 0, "max": 100,
     "start": 5, "step": 0.5,
     "explanation": "The most of your account that can be at risk in any single market."},
    {"key": "agentCapPercent", "group": "caps", "label": "Per-agent cap", "unit": "percent", "min": 0, "max": 100,
     "start": 15, "step": 0.5,
     "explanation": "The most of your account any one agent can have at risk, across all its markets."},
    {"key": "eventCapPercent", "group": "caps", "label": "Per-event cap", "unit": "percent", "min": 0, "max": 100,
     "start": 10, "step": 0.5,
     "explanation": "The most at risk on one event. An event can have several markets that depend on each other, "
                    "such as both teams in one game, so this stops correlated bets from piling up."},
    {"key": "totalCapPercent", "group": "caps", "label": "Total exposure cap", "unit": "percent", "min": 0, "max": 100,
     "start": 30, "step": 0.5,
     "explanation": "The most of your account that can be at risk at once, across every agent and market. The rest "
                    "stays in cash."},
    {"key": "dailySpendingPercent", "group": "daily", "label": "Daily spending ceiling", "unit": "percent", "min": 0,
     "max": 100, "start": 20, "step": 0.5,
     "explanation": "The most all agents together can spend in a day, as a share of your account. Each agent's own "
                    "daily cap must fit under it. A connected account can have a lower ceiling of its own."},
    {"key": "dailyLossPercent", "group": "daily", "label": "Daily loss limit", "unit": "percent", "min": 0.1,
     "max": 100, "start": 3, "step": 0.1,
     "explanation": "If your account falls this far below its value at the start of the day, new trades stop, and "
                    "the governor stays stopped until you reset it."},
    {"key": "quoteMaxAgeSeconds", "group": "timing", "label": "Price freshness", "unit": "seconds", "min": 1,
     "max": 600, "start": 30, "step": 1, "integer": True,
     "explanation": "The oldest a market price or order book can be when a trade is decided or placed. Older prices "
                    "are refused, so trades never use stale data."},
    {"key": "proposalLifetimeSeconds", "group": "timing", "label": "Proposal lifetime", "unit": "seconds", "min": 1,
     "max": 3600, "start": 60, "step": 1, "integer": True, "scale": "log",
     "explanation": "How long an agent's proposal stays valid when the agent does not set its own expiry. Proposals "
                    "not decided in time are rejected."},
    {"key": "approvalWindowSeconds", "group": "timing", "label": "Approval window", "unit": "seconds", "min": 60,
     "max": 86400, "start": 900, "step": 60, "integer": True, "scale": "log",
     "explanation": "How long a trade sized by the governor waits for your Approve. After that its sizing is out of "
                    "date, so a late approval cancels it instead of trading."},
    {"key": "startingTrustPercent", "group": "trust", "label": "Starting trust", "unit": "percent", "min": 1,
     "max": 99, "start": 50, "step": 1,
     "explanation": "How much a new agent's forecasts are trusted before any have been scored. 50% is neutral; lower "
                    "makes new agents bet less until they prove themselves."},
    {"key": "trustWeight", "group": "trust", "label": "How fast trust changes", "unit": "count", "min": 1,
     "max": 1000, "start": 20, "step": 1, "integer": True, "scale": "log",
     "explanation": "How many scored forecasts it takes before an agent's own record counts as much as the starting "
                    "trust. Lower reacts faster to results; higher needs more evidence."},
]
KEYS = [r["key"] for r in RULES]
BY_KEY = {r["key"]: r for r in RULES}
DEFAULTS = {r["key"]: r["start"] for r in RULES}

# Each agent's mandate: shares of the agent's account value, plus new-market approval.
MANDATE_FIELDS = [
    {"key": "autoLimitPercent", "label": "Automatic trade limit", "unit": "percent", "min": 0, "max": 100, "start": 2,
     "step": 0.5, "explanation": "Trades above this share of the account wait for your approval."},
    {"key": "dailyCapPercent", "label": "Agent daily cap", "unit": "percent", "min": 0, "max": 100, "start": 10,
     "step": 0.5, "explanation": "The most this agent can spend in a day. It must fit your daily spending ceiling."},
    {"key": "minimumPercent", "label": "Minimum cash reserve", "unit": "percent", "min": 0, "max": 100, "start": 20,
     "step": 0.5, "explanation": "Cash that must stay in the account after any trade by this agent."},
    {"key": "newMarketApproval", "label": "New markets require approval", "unit": "boolean", "start": False,
     "explanation": "The first trade in a market this agent has not traded before waits for your approval."},
]
MANDATE_KEYS = [f["key"] for f in MANDATE_FIELDS]
MANDATE_DEFAULTS = {f["key"]: f["start"] for f in MANDATE_FIELDS}

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


def catalog():
    """Everything the browser needs to show, explain and preview the rules and mandates."""
    return {"groups": GROUPS, "rules": RULES, "defaults": DEFAULTS, "mandateFields": MANDATE_FIELDS,
            "alwaysOn": ALWAYS_ON}


def unset(previous=None, converted=False, version=0):
    """The rules record of a workspace whose owner has not saved rules yet (or must review converted ones)."""
    return {"schema": SCHEMA, "version": version, "setAt": None, "values": None, "previous": previous,
            "converted": converted}


def _number(value):
    """A stored number as int or float (Postgres numeric columns load as Decimal), or None."""
    if isinstance(value, bool) or not isinstance(value, numbers.Number):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() else value


def account_value(record):
    """An account's current value in dollars (cash + reserved cash + marked positions). 0 for a synced account that
    is empty; None only when no usable balance is known. record: an account (its last synced balance) or a balance
    read. Either way every percentage of it is $0, so an empty or unknown account cannot trade."""
    if not isinstance(record, dict):
        return None
    balance = record if "cash" in record else record.get("balance")
    if not isinstance(balance, dict):
        return None
    parts = [_number(balance.get("cash")), _number(balance.get("reserved", 0)), _number(balance.get("positionValue", 0))]
    if any(p is None for p in parts):
        return None
    return sum(parts)


def dollars(percent, value):
    """percent of value, in dollars (0 if the value is unknown). Not rounded to cents: a limit is compared exactly,
    so 0.1% of a $4 account is $0.004, not a $0.00 limit that blocks everything. Displays round."""
    return round((percent or 0) / 100 * (value or 0), 6)


def _percent(amount, value, low=0.0, high=100.0):
    """amount as a percentage of value, clamped to [low, high], or None if it cannot be computed."""
    amount, value = _number(amount), _number(value)
    if amount is None or not value:
        return None
    return _number(round(min(high, max(low, amount / value * 100)), 2))  # 20, not 20.0


def migrate(s):
    """Bring a workspace to schema 2.

    - No rules at all (before user-set rules): no rules, so the owner reviews the starting positions on setup.
    - Schema 1 (dollar) rules: converted once to percentages from the account values, then shown for review
      ("converted"); nothing trades until the owner saves them.
    - Dollar mandates and per-account dollar ceilings become percentages of their account's value; a mandate whose
      account has no known value is cleared, so the owner sets it again.
    """
    rules = s.get("rules")
    total = sum(v for v in (account_value(a) for a in s.get("accounts", [])) if v) or None
    old_cap = _number((s.get("permissions") or {}).get("dailyCap"))
    if rules is None:
        s["rules"] = unset()
    elif rules.get("schema") != SCHEMA:
        old = rules.get("values")
        s["rules"] = unset(_convert_v1(old, s, total), converted=True, version=rules.get("version", 0)) if old else unset()
        if old:
            old_cap = _number(old.get("dailySpendingCeiling"))
    for account in s.get("accounts", []):
        ceiling = account.get("ceiling") or {}
        if "dailyCap" in ceiling:
            # Before schema 2 a new account copied the workspace cap as its own ceiling; such a copy was never a
            # lower ceiling the user chose. A lower one becomes the same share of the account's value.
            cap = _number(ceiling.pop("dailyCap"))
            if cap is not None and (old_cap is None or cap < old_cap):
                percent = _percent(cap, account_value(account))
                if percent is not None:
                    ceiling["dailyPercent"] = percent
    for agent in s.get("agentConfigs", []):
        mandate = agent.get("mandate")
        if mandate and "autoLimit" in mandate:
            account = next((a for a in s.get("accounts", []) if a["id"] == agent.get("accountId")), None)
            value = account_value(account)  # unknown or $0: there is nothing to convert against
            if not value and agent.get("status") == "active":
                agent["status"] = "paused"  # it cannot trade without a mandate; the owner sets one and resumes it
            agent["mandate"] = None if not value else {
                "autoLimitPercent": _percent(mandate.get("autoLimit"), value) or 0,
                "dailyCapPercent": _percent(mandate.get("dailyCap"), value) or 0,
                "minimumPercent": _percent(mandate.get("minimum"), value) or 0,
                "newMarketApproval": bool(mandate.get("newMarketApproval"))}
    s.pop("safety", None)
    (s.get("permissions") or {}).pop("dailyCap", None)
    return s


def _convert_v1(old, s, total):
    """Schema 1 dollar rules as schema 2 percentages, for review. A dollar limit and the percentage cap it merged
    into become the stricter of the two; without a known account value, dollar rules fall back to their start."""
    def value(key, fallback):
        v = _number(old.get(key))
        return fallback if v is None else v

    def stricter(percent, amount, base, low=0.0):
        candidates = [p for p in (percent, _percent(amount, base, low)) if p is not None]
        return min(candidates)

    day_open = sum(_number((a.get("baseline") or {}).get("equity")) or 0 for a in s.get("accounts", [])) or total
    converted = {k: value(k, DEFAULTS[k]) for k in ("kellyMultiplier", "agentCapPercent", "eventCapPercent",
                                                     "quoteMaxAgeSeconds", "proposalLifetimeSeconds",
                                                     "approvalWindowSeconds", "startingTrustPercent", "trustWeight")}
    converted["marketCapPercent"] = stricter(value("marketCapPercent", DEFAULTS["marketCapPercent"]),
                                             old.get("marketDollarLimit"), total)
    converted["totalCapPercent"] = stricter(value("totalCapPercent", DEFAULTS["totalCapPercent"]),
                                            old.get("exposureDollarLimit"), total)
    converted["dailyLossPercent"] = stricter(value("lossBreakerPercent", DEFAULTS["dailyLossPercent"]),
                                             old.get("dailyLossDollarLimit"), day_open, low=0.1)
    spending = _percent(old.get("dailySpendingCeiling"), total)
    converted["dailySpendingPercent"] = DEFAULTS["dailySpendingPercent"] if spending is None else spending
    try:
        return validate(converted)
    except ValueError:
        return dict(DEFAULTS)


def ready(s):
    rules = s.get("rules") or {}
    return rules.get("schema") == SCHEMA and bool(rules.get("values"))


def values(s):
    """The user's rule values. Raises ValueError if they have not been set."""
    if not ready(s):
        raise ValueError("Set your trading rules first.")
    return s["rules"]["values"]


def _check(field, v, problems):
    """v if it is valid for field (as int for whole-number fields), else None with the problem appended."""
    if v is None or v == "":
        problems.append(f"{field['label']} is required.")
    elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        problems.append(f"{field['label']} must be a number.")
    elif field.get("integer") and v != int(v):
        problems.append(f"{field['label']} must be a whole number.")
    elif not field["min"] <= v <= field["max"]:
        problems.append(f"{field['label']} must be between {_show(field, field['min'])} and {_show(field, field['max'])}.")
    else:
        return int(v) if field.get("integer") else v
    return None


def validate(raw):
    """Every rule, within its range. Returns clean values; raises ValueError naming each problem."""
    if not isinstance(raw, dict):
        raise ValueError("Send every trading rule.")
    problems = [f"Unknown rule {k}." for k in sorted(set(raw) - set(KEYS))]
    clean = {rule["key"]: _check(rule, raw.get(rule["key"]), problems) for rule in RULES}
    if problems:
        raise ValueError(" ".join(problems))
    return clean


def validate_mandate(raw, daily_spending_percent):
    """An agent mandate in percentages. The agent's daily cap must fit the daily spending ceiling."""
    if not isinstance(raw, dict) or set(raw) != set(MANDATE_KEYS):
        raise ValueError("Complete the four mandate restrictions.")
    problems = []
    clean = {f["key"]: _check(f, raw[f["key"]], problems) for f in MANDATE_FIELDS if f["unit"] != "boolean"}
    if type(raw["newMarketApproval"]) is not bool:
        problems.append("Choose whether new markets require approval.")
    if problems:
        raise ValueError(" ".join(problems))
    if clean["dailyCapPercent"] > daily_spending_percent:
        raise ValueError(f"The agent daily cap cannot exceed your daily spending ceiling of {daily_spending_percent}%.")
    if clean["autoLimitPercent"] > clean["dailyCapPercent"]:
        raise ValueError("The automatic trade limit cannot exceed the agent daily cap.")
    return {**clean, "newMarketApproval": raw["newMarketApproval"]}


def save(s, raw, now):
    """Validate and store a new rules version. Returns the labels of the rules that changed; saving the same values
    again changes nothing, so versions only mark real changes."""
    clean = validate(raw)
    rules = s.get("rules") or {}
    old = rules.get("values") or {}
    changed = [BY_KEY[k]["label"] for k in KEYS if old.get(k) != clean[k]]
    if old and not changed:
        return []
    s["rules"] = {"schema": SCHEMA, "version": rules.get("version", 0) + 1, "setAt": now, "values": clean,
                  "previous": None, "converted": False}
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
                  daily_loss_limit=pct("dailyLossPercent"), max_quote_age_seconds=v["quoteMaxAgeSeconds"],
                  default_proposal_ttl_seconds=v["proposalLifetimeSeconds"],
                  approval_ttl_seconds=v["approvalWindowSeconds"], reliability_prior=pct("startingTrustPercent"),
                  reliability_prior_weight=v["trustWeight"])


def fixture(s, overrides=None):
    """Save the starting positions (plus overrides) as this workspace's rules. For tests and scripted demos."""
    save(s, {**DEFAULTS, **(overrides or {})}, "2026-01-01T00:00:00+00:00")
    return s


def _show(field, v):
    return {"percent": f"{v}%", "seconds": f"{v} seconds"}.get(field["unit"], str(v))
