# Project instructions

## Layout and scope

This directory is the Git repository. Its parent workspace also contains
`governtrade-mvp 1.md`, a proposed governor MVP brief, not implemented behavior.
Read `README.md`, `COLLABORATION.md`, and `GOVERNOR_REVIEW.md` for context.

- `engine.py`: simulation state, policy checks, proposals, approvals, mandates, settlement.
- `adapters.py`: deterministic quote, fill, and settlement adapters.
- `server.py`: localhost HTTP API and single-process JSON persistence.
- `static/`: existing dashboard and guided demo.
- `test_engine.py`: backend regression suite.

The current product is a local paper simulator with five market-category agents.
The brief proposes three strategy agents and mathematical capital allocation.
Keep these distinct; do not present planned features as implemented.

## Working conventions

- Inspect Git status before edits and preserve collaborators' work.
- Preserve the existing interface and isolated main/demo state unless the task
  explicitly changes them. Keep `.data/`, credentials, and caches untracked.
- Agents propose; deterministic server-side governance authorizes commitments.
  User approval must never bypass hard policy restrictions.
- Keep paper/replay execution clearly labeled. Do not wire production trading
  into the simulated broker as a routine adapter replacement.
- Validate identity, finite numeric inputs, exact booleans, market references,
  and timestamps at the API boundary and enforce invariants in the governor.
- Use decimal arithmetic for new financial calculations. Persist policy and
  snapshot versions with decisions; injected clocks and recorded inputs should
  make governor tests reproducible.
- A matching idempotency key must return the prior result; a changed payload
  must conflict. Reserve capital and record execution atomically.
- The JSON lock only serializes one server process. Do not assume it protects
  a deployment with multiple workers.

## Run and verify

From this repository directory:

```sh
python3 server.py
python3 -m unittest discover -s . -p test_engine.py -v
node --check static/app.js
```

The preview runs at `http://localhost:3110`. Python has no third-party runtime
dependencies in the existing simulator. Node is only needed for the JavaScript
syntax check. Add focused regression tests when changing governance or ledger
behavior. The existing persistence test writes ignored `.data/test.json`.
