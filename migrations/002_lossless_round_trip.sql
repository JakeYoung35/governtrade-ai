-- Columns that let store.py round-trip the in-memory state without losing fields
-- and write append-only rows (events, evaluations, chats) exactly once.

-- Fields without a dedicated column (e.g. connectionType, future additions).
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS extra jsonb NOT NULL DEFAULT '{}';
ALTER TABLE accounts           ADD COLUMN IF NOT EXISTS extra jsonb NOT NULL DEFAULT '{}';
ALTER TABLE agents             ADD COLUMN IF NOT EXISTS extra jsonb NOT NULL DEFAULT '{}';
ALTER TABLE orders             ADD COLUMN IF NOT EXISTS extra jsonb NOT NULL DEFAULT '{}';

-- NULL means the order never reached a budget reservation decision (key absent in state).
ALTER TABLE orders ALTER COLUMN committed DROP NOT NULL, ALTER COLUMN committed DROP DEFAULT;

-- Application event id; unique indexes on hypertables must include the time column.
-- Several events can share one timestamp, so seq preserves their insertion order.
ALTER TABLE events ADD COLUMN IF NOT EXISTS id text NOT NULL;
ALTER TABLE events ADD COLUMN IF NOT EXISTS seq bigserial;
CREATE UNIQUE INDEX IF NOT EXISTS events_id_time_idx ON events (id, time);

-- Position within the order's evaluation history / the agent's chat.
ALTER TABLE order_evaluations ADD COLUMN IF NOT EXISTS seq int NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS order_evaluations_seq_idx ON order_evaluations (order_id, seq);
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS seq int NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_seq_idx ON chat_messages (agent_id, seq);

INSERT INTO schema_migrations (version) VALUES (2) ON CONFLICT DO NOTHING;
