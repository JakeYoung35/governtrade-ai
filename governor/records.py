"""Governor records kept in the workspace state dict, plus exact replay of stored batches.

State keys (JSON-safe: decimals as strings, times as ISO-8601 UTC):
  governorBatches    newest first: {id, accountId, startedAt, policyVersion, inputs}
  governorDecisions  newest first: Decision.to_dict() plus {batchId, accountId, decidedAt}
  governorForecasts  submission order; only the first forecast per (agent, market)
  governor           {"breakers": {accountId: BreakerState.to_dict()}, "breakerEvents": [...]}

JSON mode saves these with the rest of the state. Postgres mode writes the three lists to the
governor_* tables (migrations/003_governor.sql) and the small `governor` object with the
workspace settings. State keeps a recent window of batches and decisions; Postgres keeps all.

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


def _iso(value, name):
    return to_utc(value, name).isoformat()


def _time(text):
    return datetime.fromisoformat(text)


def _optional(value):
    return None if value is None else str(value)


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
                 portfolio, reliability, policy=DEFAULT_POLICY):
    """Self-contained record of one batch's inputs. `books` are raw get_orderbook payloads."""
    return {"id": batch_id, "accountId": account_id, "startedAt": _iso(started_at, "started_at"),
            "policyVersion": policy.version,
            "inputs": {"snapshotId": snapshot_id, "evaluatedAt": _iso(evaluated_at, "evaluated_at"),
                       "policy": policy.to_dict(), "portfolio": portfolio_to_dict(portfolio),
                       "reliability": reliability_to_dict(reliability),
                       "proposals": [proposal_to_dict(p) for p in sorted(proposals, key=lambda p: p.proposal_id)],
                       "books": copy.deepcopy(list(books))}}


def replay(batch):
    """Recompute a stored batch's decisions from its recorded inputs."""
    inputs = batch["inputs"]
    snapshot = build_snapshot(inputs["books"], snapshot_id=inputs["snapshotId"], taken_at=_time(batch["startedAt"]))
    return decide([proposal_from_dict(p) for p in inputs["proposals"]], snapshot,
                  portfolio_from_dict(inputs["portfolio"]), reliability_from_dict(inputs["reliability"]),
                  _time(inputs["evaluatedAt"]), Policy.from_dict(inputs["policy"]))


def record_batch(s, batch, decisions, decided_at):
    """Store a batch and its decisions. Ids are write-once: repeats are refused, never overwritten."""
    batches = s.setdefault("governorBatches", [])
    stored = s.setdefault("governorDecisions", [])
    if any(b["id"] == batch["id"] for b in batches):
        raise ValueError(f"Batch {batch['id']} is already recorded.")
    known = {d["proposalId"] for d in stored}
    new = [d.to_dict() for d in decisions]
    if any(d["proposalId"] in known for d in new):
        raise ValueError("A proposal already has a recorded decision.")
    decided = _iso(decided_at, "decided_at")
    batches.insert(0, copy.deepcopy(batch))
    # Newest first; within one batch, descending proposal id (the order Postgres loads them in).
    for d in new:
        stored.insert(0, {**d, "batchId": batch["id"], "accountId": batch["accountId"], "decidedAt": decided})
    del batches[BATCH_WINDOW:]
    del stored[DECISION_WINDOW:]


# Forecasts and reliability.

def record_forecast(s, proposal):
    """Keep the agent's first forecast for this market; later ones are not independent evidence.

    Returns True when this proposal's forecast was stored.
    """
    rows = s.setdefault("governorForecasts", [])
    if any(f["agentId"] == proposal.agent_id and f["marketId"] == proposal.market_id for f in rows):
        return False
    rows.append({"proposalId": proposal.proposal_id, "agentId": proposal.agent_id, "marketId": proposal.market_id,
                 "submittedAt": proposal.received_at.isoformat(),
                 "estimatedYesProbability": str(proposal.estimated_yes_probability),
                 "outcome": None, "resolvedAt": None})
    return True


def resolve_forecasts(s, outcomes, now):
    """Set outcomes (market_id -> 1 YES, 0 NO, 0.5 tie) on unresolved forecasts. Returns the count."""
    resolved_at = _iso(now, "now")
    parsed = {}
    for market_id, value in outcomes.items():
        outcome = to_decimal(value, "outcome")
        if not ZERO <= outcome <= ONE:
            raise ValueError("Outcomes must be between 0 and 1.")
        parsed[market_id] = outcome
    count = 0
    for f in s.get("governorForecasts", []):
        if f["outcome"] is None and f["marketId"] in parsed:
            f["outcome"], f["resolvedAt"] = str(parsed[f["marketId"]]), resolved_at
            count += 1
    return count


def forecasts(s):
    return [Forecast(f["agentId"], f["marketId"], f["proposalId"], _time(f["submittedAt"]),
                     f["estimatedYesProbability"], f["outcome"]) for f in s.get("governorForecasts", [])]


def reliability(s, policy=DEFAULT_POLICY):
    """agent_id -> ReliabilityScore from every stored forecast."""
    return score_all(forecasts(s), policy)


# Loss breaker state.

def _governor(s):
    return s.setdefault("governor", {"breakers": {}, "breakerEvents": []})


def breaker_state(s, account_id):
    data = _governor(s)["breakers"].get(account_id)
    return BreakerState.from_dict(data) if data else BreakerState()


def save_breaker(s, account_id, state):
    _governor(s)["breakers"][account_id] = state.to_dict()


def record_breaker_event(s, account_id, event):
    """Audit a latch or reset. Newest first; a bounded window lives in state."""
    events = _governor(s)["breakerEvents"]
    events.insert(0, {**copy.deepcopy(event), "accountId": account_id})
    del events[BREAKER_EVENT_WINDOW:]
