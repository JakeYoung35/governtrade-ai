-- GovernTrade AI: initial Postgres/TimescaleDB schema for the main (connected-account) workspace.
-- The isolated presenter demo stays in local JSON. Credentials stay in the local encrypted vault;
-- only opaque vault references are stored here.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version    int PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

-- Workspace ceiling, all-agent kill switch and hard safety limits (single row).
CREATE TABLE IF NOT EXISTS workspace_settings (
  id             boolean PRIMARY KEY DEFAULT true CHECK (id),
  daily_cap      numeric(12,2) NOT NULL DEFAULT 100 CHECK (daily_cap >= 0),
  automation     boolean NOT NULL DEFAULT true,
  daily_loss_cap numeric(12,2) NOT NULL DEFAULT 50,
  market_cap     numeric(12,2) NOT NULL DEFAULT 50,
  exposure_cap   numeric(12,2) NOT NULL DEFAULT 500,
  slippage_max   numeric(6,3)  NOT NULL DEFAULT 1,
  schema_version int NOT NULL DEFAULT 3
);
INSERT INTO workspace_settings (id) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS accounts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name              text NOT NULL CHECK (length(name) <= 100),
  venue             text NOT NULL DEFAULT 'kalshi',
  environment       text NOT NULL CHECK (environment IN ('sandbox','live')),
  subaccount        int  NOT NULL DEFAULT 0 CHECK (subaccount BETWEEN 0 AND 63),
  live_enabled      boolean NOT NULL DEFAULT false,
  credential_ref    text,
  credential_masks  jsonb,
  status            text NOT NULL DEFAULT 'disconnected',
  stale             boolean NOT NULL DEFAULT true,
  error             text,
  ceiling_daily_cap numeric(12,2) CHECK (ceiling_daily_cap >= 0),
  baseline_day      date,
  baseline_equity   numeric(14,2),
  balance           jsonb,          -- last-known balance, shown as stale when a sync fails
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agents (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id           uuid NOT NULL REFERENCES accounts(id),
  name                 text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  provider             text NOT NULL,
  model                text NOT NULL,
  model_validated      boolean NOT NULL DEFAULT false,
  model_credential_ref text,
  model_key_mask       text,
  status      text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','paused','deleted')),
  personality text CHECK (personality IN ('Conservative','Moderate','Aggressive')),
  -- Mandate: all NULL until the user saves one.
  auto_limit          numeric(12,2) CHECK (auto_limit >= 0),
  daily_cap           numeric(12,2) CHECK (daily_cap >= 0),
  minimum_reserve     numeric(12,2) CHECK (minimum_reserve >= 0),
  new_market_approval boolean,
  CHECK (auto_limit <= daily_cap),
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agents_account_idx ON agents (account_id);

CREATE TABLE IF NOT EXISTS orders (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id       uuid NOT NULL REFERENCES agents(id),
  account_id     uuid NOT NULL REFERENCES accounts(id),
  market_id      text NOT NULL,
  market         jsonb NOT NULL,    -- quote snapshot used by the latest evaluation
  direction      text NOT NULL CHECK (direction IN ('Yes','No')),
  amount         numeric(12,2) NOT NULL CHECK (amount >= 1),
  reasoning      text CHECK (length(reasoning) <= 8000),
  status         text NOT NULL,
  decision       text CHECK (decision IN ('ALLOW','REQUIRE_APPROVAL','BLOCK')),
  committed      boolean NOT NULL DEFAULT false,
  filled         boolean NOT NULL DEFAULT false,
  automatic      boolean,
  venue_order_id text,
  quantity       numeric(14,4),
  actual_cost    numeric(12,2),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_account_time_idx ON orders (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_agent_time_idx   ON orders (agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_market_idx       ON orders (market_id);

CREATE TABLE IF NOT EXISTS order_evaluations (
  id           bigserial PRIMARY KEY,
  order_id     uuid NOT NULL REFERENCES orders(id),
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  outcome      text NOT NULL CHECK (outcome IN ('ALLOW','REQUIRE_APPROVAL','BLOCK')),
  layers       jsonb NOT NULL,
  checks       jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS order_evaluations_order_idx ON order_evaluations (order_id, evaluated_at);

-- Replaces state['keys']; the primary key makes worker retries idempotent.
CREATE TABLE IF NOT EXISTS idempotency_keys (
  agent_id uuid NOT NULL REFERENCES agents(id),
  key      text NOT NULL CHECK (length(key) BETWEEN 1 AND 200),
  order_id uuid NOT NULL REFERENCES orders(id),
  PRIMARY KEY (agent_id, key)
);

-- id doubles as the worker's read cursor.
CREATE TABLE IF NOT EXISTS chat_messages (
  id         bigserial PRIMARY KEY,
  agent_id   uuid NOT NULL REFERENCES agents(id),
  role       text NOT NULL CHECK (role IN ('customer','agent')),
  text       text NOT NULL CHECK (length(text) <= 8000),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_agent_idx ON chat_messages (agent_id, id);

-- Time-series: activity feed and balance history.
CREATE TABLE IF NOT EXISTS events (
  time     timestamptz NOT NULL DEFAULT now(),
  kind     text NOT NULL,
  text     text NOT NULL,
  order_id uuid
);
SELECT create_hypertable('events', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS balance_snapshots (
  time           timestamptz NOT NULL DEFAULT now(),
  account_id     uuid NOT NULL REFERENCES accounts(id),
  cash           numeric(14,2),
  reserved       numeric(14,2),
  position_value numeric(14,2),
  exposure       numeric(14,2),
  daily_loss     numeric(14,2),
  stale          boolean NOT NULL DEFAULT false
);
SELECT create_hypertable('balance_snapshots', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS balance_snapshots_account_idx ON balance_snapshots (account_id, time DESC);

INSERT INTO schema_migrations (version) VALUES (1) ON CONFLICT DO NOTHING;
