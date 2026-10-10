-- Governed proposal submissions (governor_service.py): one row per accepted request, updated as it moves
-- from queued to decided, submitted, skipped or failed. The full request record is the payload.
CREATE TABLE IF NOT EXISTS governor_requests (
  governor_proposal_id text PRIMARY KEY,
  agent_id             uuid NOT NULL REFERENCES agents(id),
  account_id           uuid NOT NULL REFERENCES accounts(id),
  proposal_id          text NOT NULL,   -- the worker's idempotency key, unique per agent
  status               text NOT NULL,
  received_at          timestamptz NOT NULL,
  payload              jsonb NOT NULL,
  UNIQUE (agent_id, proposal_id)
);
CREATE INDEX IF NOT EXISTS governor_requests_queued_idx ON governor_requests (received_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS governor_requests_recent_idx ON governor_requests (received_at DESC);

INSERT INTO schema_migrations (version) VALUES (4) ON CONFLICT DO NOTHING;
