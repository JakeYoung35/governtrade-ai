"""Governor records kept in the workspace state dict, plus exact replay of stored batches.

State keys (JSON-safe: decimals as strings, times as ISO-8601 UTC):
  governorBatches    newest first: {id, accountId, startedAt, policyVersion, inputs}
  governorDecisions  newest first: Decision.to_dict() plus {batchId, accountId, decidedAt}
  governorForecasts  submission order; only the first forecast per (agent, market)
  governorBreakerEvents  newest first: loss-breaker latches and resets, {id, accountId, action, at, ...}
  governor           {"breakers": {accountId: BreakerState.to_dict()}}
  governorReplacedForecasts  proposal ids of forecasts replaced since the last save (store.save deletes them)

Recording never drops anything. Postgres mode writes the four lists to the governor_* tables
(migrations/003_governor.sql), keeps every row, and loads back only recent windows. JSON mode
has no database, so compact_for_json() bounds what the local file keeps.

Lists stay sorted by (time, id) with Python's code-point string order, which is the order
Postgres returns under COLLATE "C", so a reloaded state equals the saved one.

A batch record stores every decision input (policy, portfolio, reliability, proposals and the
raw venue order books), so replay(batch) recomputes its decisions exactly.
"""
import copy
from datetime import datetime

from .allocator import decide
from .breaker import BreakerState
from .models import Exposure, Portfolio, Proposal, to_utc
from .money import ONE, ZERO, to_decimal
from .policy import DEFAULT_POLICY, Policy
from .reliability import Forecast, ReliabilityScore, score_all
from .snapshot import build_snapshot

BATCH_WINDOW = 50  # batches carry full order books, so state keeps only the recent ones
DECISION_WINDOW = 500
BREAKER_EVENT_WINDOW = 200
REQUEST_WINDOW = 500  # governed proposal requests kept in full (Postgres load and the JSON file)
_SLIM_REQUEST_FIELDS = ("governorProposalId", "proposalId", "agentId", "accountId", "payloadHash", "receivedAt",
                        "status", "decision", "batchId", "orderId", "note")


def _iso(value, name):
    return to_utc(value, name).isoformat()


def _time(text):
    return datetime.fromisoformat(text)


def _optional(value):
    return None if value is None else str(value)


def _plain(value):
    # Fixed-point text, never exponent form, so it matches what Postgres returns for numeric::text.
    return format(to_decimal(value, "value"), "f")


def _require_known(s, *, agents=(), accounts=()):
    """Refuse ids the workspace does not know; the governor tables reference agents and accounts,
    so recording one would make every later save of the request fail."""
    known_agents = {a["id"] for a in s.get("agentConfigs", [])}
    known_accounts = {a["id"] for a in s.get("accounts", [])}
    unknown = sorted(set(agents) - known_agents) + sorted(set(accounts) - known_accounts)
    if unknown:
        raise ValueError(f"Unknown agent or account: {', '.join(unknown)}.")


def _sort(rows, time_key, id_key, newest_first):
    # Compare parsed times: ISO strings with and without microseconds do not sort as text.
    rows.sort(key=lambda row: (_time(row[time_key]), row[id_key]), reverse=newest_first)


# Serialization of decision inputs.

def proposal_to_dict(p):
    return {"proposalId": p.proposal_id, "agentId": p.agent_id, "marketId": p.market_id, "side": p.side,
            "estimatedYesProbability": str(p.estimated_yes_probability), "requestedBudget": str(p.requested_budget),
            "receivedAt": p.received_at.isoformat(), "expiresAt": p.expires_at.isoformat(),
            "maxEntryPrice": _optional(p.max_entry_price)}


def proposal_from_dict(d):
    return Proposal(d["proposalId"], d["agentId"], d["marketId"], d["side"], d["estimatedYesProbability"],
                    d["requestedBudget"], _time(d["receivedAt"]), _time(d["expiresAt"]), d.get("maxEntryPrice"))


def portfolio_to_dict(p):
    return {"equity": str(p.equity), "availableCash": str(p.available_cash),
            "exposures": [{"marketId": e.market_id, "agentId": e.agent_id, "eventGroup": e.event_group,
                           "side": e.side, "amount": str(e.amount)} for e in p.exposures],
            "paused": p.paused, "breakerLatched": p.breaker_latched, "valuationStale": p.valuation_stale}


def portfolio_from_dict(d):
    exposures = [Exposure(e["marketId"], e["agentId"], e["eventGroup"], e["side"], e["amount"]) for e in d["exposures"]]
    return Portfolio(d["equity"], d["availableCash"], exposures, paused=d["paused"],
                     breaker_latched=d["breakerLatched"], valuation_stale=d["valuationStale"])


def reliability_to_dict(scores):
    return {agent: {"score": str(r.score), "resolvedCount": r.resolved_count, "brier": _optional(r.brier)}
            for agent, r in sorted(scores.items())}


def reliability_from_dict(d):
    return {agent: ReliabilityScore(agent, to_decimal(r["score"], "score"), r["resolvedCount"],
                                    None if r["brier"] is None else to_decimal(r["brier"], "brier"))
            for agent, r in d.items()}


# Batches and decisions.

def batch_record(*, batch_id, account_id, started_at, evaluated_at, snapshot_id, books, proposals,
                 portfolio, reliability, policy=DEFAULT_POLICY, event_groups=None):
    """Self-contained record of one batch's inputs. `books` are raw get_orderbook payloads;
    event_groups is the manual correlation override passed to build_snapshot, if any."""
    return {"id": batch_id, "accountId": account_id, "startedAt": _iso(started_at, "started_at"),
            "policyVersion": policy.version,
            "inputs": {"snapshotId": snapshot_id, "evaluatedAt": _iso(evaluated_at, "evaluated_at"),
                       "policy": policy.to_dict(), "portfolio": portfolio_to_dict(portfolio),
                       "reliability": reliability_to_dict(reliability),
                       "proposals": [proposal_to_dict(p) for p in sorted(proposals, key=lambda p: p.proposal_id)],
                       "books": copy.deepcopy(list(books)), "eventGroups": dict(sorted((event_groups or {}).items()))}}


def replay(batch):
    """Recompute a stored batch's decisions from its recorded inputs."""
    inputs = batch["inputs"]
    snapshot = build_snapshot(inputs["books"], snapshot_id=inputs["snapshotId"], taken_at=_time(batch["startedAt"]),
                              event_groups=inputs["eventGroups"])
    return decide([proposal_from_dict(p) for p in inputs["proposals"]], snapshot,
                  portfolio_from_dict(inputs["portfolio"]), reliability_from_dict(inputs["reliability"]),
                  _time(inputs["evaluatedAt"]), Policy.from_dict(inputs["policy"]))


def record_batch(s, batch, decisions, decided_at):
    """Store a batch and its decisions. Ids are write-once: repeats are refused, never overwritten.

    State only sees recent records, so a repeat of an older id is refused by Postgres when saved
    (the governor inserts have no ON CONFLICT clause) rather than here.
    """
    _require_known(s, agents=[d.agent_id for d in decisions], accounts=[batch["accountId"]])
    batches = s.setdefault("governorBatches", [])
    stored = s.setdefault("governorDecisions", [])
    if any(b["id"] == batch["id"] for b in batches):
        raise ValueError(f"Batch {batch['id']} is already recorded.")
    known = {d["proposalId"] for d in stored}
    new = [d.to_dict() for d in decisions]
    if any(d["proposalId"] in known for d in new):
        raise ValueError("A proposal already has a recorded decision.")
    decided = _iso(decided_at, "decided_at")
    batches.append(copy.deepcopy(batch))
    stored.extend({**d, "batchId": batch["id"], "accountId": batch["accountId"], "decidedAt": decided} for d in new)
    _sort(batches, "startedAt", "id", newest_first=True)
    _sort(stored, "decidedAt", "proposalId", newest_first=True)


# Forecasts and reliability.

def record_forecast(s, proposal):
    """Keep the agent's first forecast for this market; later ones are not independent evidence.

    "First" is the earliest (submittedAt, proposalId), the same rule score_agent uses, whatever
    order proposals are processed in. An earlier forecast replaces a stored later one until the
    market resolves; a resolved forecast is never replaced. Returns True when this proposal's
    forecast is the one kept.
    """
    _require_known(s, agents=[proposal.agent_id])
    rows = s.setdefault("governorForecasts", [])
    key = (proposal.received_at, proposal.proposal_id)
    existing = next((f for f in rows if f["agentId"] == proposal.agent_id and f["marketId"] == proposal.market_id), None)
    if existing:
        if existing["outcome"] is not None or (_time(existing["submittedAt"]), existing["proposalId"]) <= key:
            return False
        rows.remove(existing)
        s.setdefault("governorReplacedForecasts", []).append(existing["proposalId"])
    rows.append({"proposalId": proposal.proposal_id, "agentId": proposal.agent_id, "marketId": proposal.market_id,
                 "submittedAt": proposal.received_at.isoformat(),
                 "estimatedYesProbability": _plain(proposal.estimated_yes_probability),
                 "outcome": None, "resolvedAt": None})
    _sort(rows, "submittedAt", "proposalId", newest_first=False)
    return True


def resolve_forecasts(s, outcomes, now, agents=None):
    """Set outcomes (market_id -> 1 YES, 0 NO, 0.5 tie) on unresolved forecasts. Returns the count.

    agents limits scoring to those agent ids, so a result read from one venue environment (demo or
    live) never scores another environment's forecasts.
    """
    resolved_at = _iso(now, "now")
    parsed = {}
    for market_id, value in outcomes.items():
        outcome = to_decimal(value, "outcome")
        if not ZERO <= outcome <= ONE:
            raise ValueError("Outcomes must be between 0 and 1.")
        parsed[market_id] = outcome
    count = 0
    for f in s.get("governorForecasts", []):
        if f["outcome"] is None and f["marketId"] in parsed and (agents is None or f["agentId"] in agents):
            f["outcome"], f["resolvedAt"] = _plain(parsed[f["marketId"]]), resolved_at
            count += 1
    return count


def forecasts(s):
    return [Forecast(f["agentId"], f["marketId"], f["proposalId"], _time(f["submittedAt"]),
                     f["estimatedYesProbability"], f["outcome"]) for f in s.get("governorForecasts", [])]


def reliability(s, policy=DEFAULT_POLICY):
    """agent_id -> ReliabilityScore from every stored forecast."""
    return score_all(forecasts(s), policy)


# Loss breaker state.

def _breakers(s):
    return s.setdefault("governor", {}).setdefault("breakers", {})


def breaker_state(s, account_id):
    data = _breakers(s).get(account_id)
    return BreakerState.from_dict(data) if data else BreakerState()


def save_breaker(s, account_id, state):
    _breakers(s)[account_id] = state.to_dict()


def record_breaker_event(s, account_id, event, event_id):
    """Audit a latch or reset (needs "action" and an ISO "at"). Write-once by event_id.

    Recording never trims, so every event reaches Postgres, which keeps it permanently. JSON mode
    has no database: its file keeps only the newest BREAKER_EVENT_WINDOW (see compact_for_json).
    """
    _require_known(s, accounts=[account_id])
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 200:
        raise ValueError("A breaker event needs an id of 1 to 200 characters.")
    if not isinstance(event.get("action"), str) or not event["action"]:
        raise ValueError("A breaker event needs an action.")
    at = _iso(_time(event["at"]), "at")
    events = s.setdefault("governorBreakerEvents", [])
    if any(e["id"] == event_id for e in events):
        raise ValueError(f"Breaker event {event_id} is already recorded.")
    events.append({**copy.deepcopy(event), "at": at, "id": event_id, "accountId": account_id})
    _sort(events, "at", "id", newest_first=True)


def compact_for_json(s):
    """Copy of state bounded for the local JSON file (JSON mode has no database to hold history).

    Keeps the newest BATCH_WINDOW batches with their decisions (so every kept decision can still
    be replayed), at most DECISION_WINDOW decisions, the newest BREAKER_EVENT_WINDOW breaker
    events, and every forecast. Postgres mode never calls this.
    """
    out = dict(s)
    batches = s.get("governorBatches", [])[:BATCH_WINDOW]
    kept = {b["id"] for b in batches}
    out["governorBatches"] = batches
    out["governorDecisions"] = [d for d in s.get("governorDecisions", []) if d["batchId"] in kept][:DECISION_WINDOW]
    out["governorBreakerEvents"] = s.get("governorBreakerEvents", [])[:BREAKER_EVENT_WINDOW]
    out["governorReplacedForecasts"] = []  # the file holds the full forecast list; nothing to delete
    # Requests beyond the window keep a slim record, so a retry of an old proposalId is still recognised
    # (and never placed twice) without keeping its full payload and reasoning on disk.
    requests = s.get("governorRequests", [])
    active = [r for r in requests if r["status"] in ("queued", "approved")]
    done = [r for r in requests if r["status"] not in ("queued", "approved")]
    full = {id(r) for r in active + done[:REQUEST_WINDOW]}
    out["governorRequests"] = [r if id(r) in full else {
        **{k: r[k] for k in _SLIM_REQUEST_FIELDS if k in r},
        "proposal": {k: r["proposal"][k] for k in ("marketId", "side", "expiresAt")}} for r in requests]
    return out
