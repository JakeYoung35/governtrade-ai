-- Governor records (governor/records.py). Batches and decisions are append-only; forecasts
-- gain an outcome when their market settles. Exact decimals and timestamps round-trip through
-- jsonb payloads and unconstrained numeric columns (loaded as text), never through floats.

-- Every input of one allocation batch, so its decisions can be replayed exactly.
CREATE TABLE IF NOT EXISTS governor_batches (
  id             text NOT NULL,
  started_at     timestamptz NOT NULL,
  account_id     uuid NOT NULL REFERENCES accounts(id),
  policy_version text NOT NULL,
  inputs         jsonb NOT NULL   -- policy, portfolio, reliability, proposals, raw order books
);
SELECT create_hypertable('governor_batches', 'started_at', if_not_exists => TRUE);
-- Unique indexes on hypertables must include the time column.
CREATE UNIQUE INDEX IF NOT EXISTS governor_batches_id_idx ON governor_batches (id, started_at);
CREATE INDEX IF NOT EXISTS governor_batches_account_idx ON governor_batches (account_id, started_at DESC);

-- One decision per proposal. Queryable columns plus the full decision (with trace) as payload.
CREATE TABLE IF NOT EXISTS governor_decisions (
  proposal_id     text PRIMARY KEY,
  batch_id        text NOT NULL,
  account_id      uuid NOT NULL REFERENCES accounts(id),
  agent_id        uuid NOT NULL REFERENCES agents(id),
  market_id       text NOT NULL,
  side            text NOT NULL CHECK (side IN ('yes','no')),
  decision        text NOT NULL CHECK (decision IN ('approved','resized','rejected')),
  approved_budget numeric NOT NULL CHECK (approved_budget >= 0),
  contracts       numeric NOT NULL CHECK (contracts >= 0),
  reason_codes    text[] NOT NULL,
  policy_version  text NOT NULL,
  decided_at      timestamptz NOT NULL,
  payload         jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS governor_decisions_account_idx ON governor_decisions (account_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS governor_decisions_agent_idx   ON governor_decisions (agent_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS governor_decisions_batch_idx   ON governor_decisions (batch_id);

-- The first forecast per agent and market, scored for reliability once the market settles.
CREATE TABLE IF NOT EXISTS governor_forecasts (
  proposal_id               text PRIMARY KEY,
  agent_id                  uuid NOT NULL REFERENCES agents(id),
  market_id                 text NOT NULL,
  submitted_at              timestamptz NOT NULL,
  estimated_yes_probability numeric NOT NULL CHECK (estimated_yes_probability > 0 AND estimated_yes_probability < 1),
  outcome                   numeric CHECK (outcome BETWEEN 0 AND 1),
  resolved_at               timestamptz,
  CHECK ((outcome IS NULL) = (resolved_at IS NULL)),
  UNIQUE (agent_id, market_id)
);
CREATE INDEX IF NOT EXISTS governor_forecasts_market_idx ON governor_forecasts (market_id) WHERE outcome IS NULL;

-- Permanent, append-only audit of loss-breaker latches and operator resets. State keeps only
-- the most recent events; every event stays here.
CREATE TABLE IF NOT EXISTS governor_breaker_events (
  id         text PRIMARY KEY,
  at         timestamptz NOT NULL,
  account_id uuid NOT NULL REFERENCES accounts(id),
  action     text NOT NULL,
  payload    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS governor_breaker_events_account_idx ON governor_breaker_events (account_id, at DESC);

INSERT INTO schema_migrations (version) VALUES (3) ON CONFLICT DO NOTHING;
