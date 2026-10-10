"""The governed proposal contract: a published JSON Schema plus strict parsing with field-level errors.

An AI worker can fetch describe() (served at GET /api/governor/schema), use the schema as a tool
or structured-output definition, and correct a rejected request from the returned field errors.
Pure: no I/O and no clock reads.
"""
from datetime import datetime, timedelta
import hashlib
import json
import re

from .models import to_utc
from .money import to_decimal
from .policy import DEFAULT_POLICY

SCHEMA_VERSION = 1
MAX_TTL_SECONDS = 3600
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "GovernedProposal",
    "description": "One trade proposal for the portfolio governor. The governor decides the stake; "
                   "maxBudgetUsd is a ceiling, not an entitlement.",
    "type": "object",
    "additionalProperties": False,
    "required": ["schemaVersion", "proposalId", "agentId", "marketId", "side", "estimatedYesProbability",
                 "maxBudgetUsd", "reasoning"],
    "properties": {
        "schemaVersion": {"const": SCHEMA_VERSION},
        "proposalId": {"type": "string", "pattern": _ID.pattern,
                       "description": "Your idempotency key, unique per agent. Retrying with the same id and payload "
                                      "returns the original result. A UUID is recommended. Ids starting with \"chat-\" "
                                      "are reserved for the in-app agent chat."},
        "agentId": {"type": "string", "description": "The configured agent id from GET /api/worker."},
        "marketId": {"type": "string", "minLength": 1, "maxLength": 200,
                     "description": "Kalshi market ticker, e.g. KXNFLGAME-26OCT18BALCLE-CLE."},
        "side": {"enum": ["yes", "no"], "description": "Contract side to buy."},
        "estimatedYesProbability": {"type": "number", "minimum": 0.01, "maximum": 0.99,
                                    "description": "Your probability that the market resolves YES, even when side is "
                                                   "\"no\". Sizing and reliability scoring use this number."},
        "maxBudgetUsd": {"type": "number", "minimum": 1, "maximum": 1000000,
                         "description": "Most you want committed, all-in, in US dollars."},
        "maxEntryPrice": {"type": "number", "minimum": 0.01, "maximum": 0.99,
                          "description": "Optional. Highest price per contract (dollars) for the chosen side."},
        "expiresAt": {"type": "string", "format": "date-time",
                      "description": "Optional ISO-8601 time with timezone, at most one hour ahead. Defaults to the "
                                     "policy's proposal lifetime."},
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 8000,
                      "description": "Why you propose this trade; shown to the user and kept for audit."},
    },
}

EXAMPLE = {
    "schemaVersion": SCHEMA_VERSION,
    "proposalId": "8f14e45f-ceea-467f-a8d5-3f3a0e0b5c2d",
    "agentId": "<agent id from GET /api/worker>",
    "marketId": "KXNFLGAME-26OCT18BALCLE-CLE",
    "side": "yes",
    "estimatedYesProbability": 0.45,
    "maxBudgetUsd": 25,
    "maxEntryPrice": 0.40,
    "reasoning": "Cleveland's injury news is not priced in; I estimate 45% against a 37 cent ask.",
}


def describe(policy=DEFAULT_POLICY):
    """Everything a worker or model needs to submit well-formed proposals."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "submit": "POST /api/governor/proposals with Authorization: Bearer <worker token> and one JSON object.",
        "lookup": "GET /api/governor/proposals/{governorProposalId} for the decision and order status.",
        "jsonSchema": SCHEMA,
        "example": EXAMPLE,
        "responses": {
            "202": {"governorProposalId": "...", "proposalId": "...", "status": "queued"},
            "200": "An identical retry: the original record, unchanged.",
            "400": {"error": "Invalid proposal.", "errors": [{"field": "side", "error": "must be \"yes\" or \"no\""}],
                    "schema": "/api/governor/schema"},
            "409": "The proposalId was already used by this agent with a different payload.",
        },
        "notes": [
            "Send numbers as JSON numbers, not strings or booleans.",
            "Proposals are decided in batches every few seconds, together with other agents' proposals.",
            "The governor may approve, resize or reject. Approved stakes still pass your mandate and account "
            "ceiling, which can require the user's approval.",
            f"Proposals expire after {policy.default_proposal_ttl_seconds} seconds unless expiresAt says otherwise.",
        ],
    }


def _number(payload, field, low, high, errors, *, required=True):
    if field not in payload:
        if required:
            errors.append({"field": field, "error": "is required"})
        return None
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append({"field": field, "error": "must be a JSON number"})
        return None
    try:
        number = to_decimal(value, field)
    except ValueError:
        errors.append({"field": field, "error": "must be a finite number"})
        return None
    if not low <= number <= high:
        errors.append({"field": field, "error": f"must be between {low} and {high}"})
        return None
    return number


def _string(payload, field, errors, *, pattern=None, max_length=200):
    if field not in payload:
        errors.append({"field": field, "error": "is required"})
        return None
    value = payload[field]
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        errors.append({"field": field, "error": f"must be a non-empty string of at most {max_length} characters"})
        return None
    if pattern and not pattern.match(value):
        errors.append({"field": field, "error": "may contain only letters, digits and . _ : -"})
        return None
    return value


def parse_submission(payload, *, received_at, policy=DEFAULT_POLICY):
    """Validate one proposal. Returns (normalized, errors); normalized is None when errors is non-empty.

    normalized uses exact decimal strings and ISO UTC times, ready for storage and Proposal().
    """
    received_at = to_utc(received_at, "received_at")
    if not isinstance(payload, dict):
        return None, [{"field": "", "error": "must be a JSON object"}]
    errors = [{"field": key, "error": "is not a known field"} for key in sorted(set(payload) - set(SCHEMA["properties"]))]
    version = payload.get("schemaVersion")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        errors.append({"field": "schemaVersion", "error": f"must be {SCHEMA_VERSION}"})
    proposal_id = _string(payload, "proposalId", errors, pattern=_ID)
    agent_id = _string(payload, "agentId", errors)
    market_id = _string(payload, "marketId", errors)
    side = payload.get("side")
    if side not in ("yes", "no"):
        errors.append({"field": "side", "error": "must be \"yes\" or \"no\""})
    probability = _number(payload, "estimatedYesProbability", to_decimal("0.01"), to_decimal("0.99"), errors)
    budget = _number(payload, "maxBudgetUsd", to_decimal(1), to_decimal(1000000), errors)
    ceiling = _number(payload, "maxEntryPrice", to_decimal("0.01"), to_decimal("0.99"), errors, required=False)
    reasoning = _string(payload, "reasoning", errors, max_length=8000)
    expires_at = received_at + timedelta(seconds=policy.default_proposal_ttl_seconds)
    if "expiresAt" in payload:
        try:
            expires_at = to_utc(datetime.fromisoformat(payload["expiresAt"]), "expiresAt")
        except (TypeError, ValueError):
            errors.append({"field": "expiresAt", "error": "must be an ISO-8601 time with a timezone"})
        else:
            if not received_at < expires_at <= received_at + timedelta(seconds=MAX_TTL_SECONDS):
                errors.append({"field": "expiresAt", "error": "must be in the future and at most one hour ahead"})
    if errors:
        return None, errors
    return {"proposalId": proposal_id, "agentId": agent_id, "marketId": market_id, "side": side,
            "estimatedYesProbability": format(probability, "f"), "maxBudgetUsd": format(budget, "f"),
            "maxEntryPrice": None if ceiling is None else format(ceiling, "f"),
            "expiresAt": expires_at.isoformat(), "reasoning": reasoning}, []


def payload_hash(payload):
    """Fingerprint of the client's JSON object, for idempotent retries: an identical retry matches,
    any change to any field (including an explicit expiresAt) does not."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
