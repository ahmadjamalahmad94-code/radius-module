-- Scoped bandwidth schedules.
-- Additive only: keeps existing plan-level rows compatible.

ALTER TABLE bandwidth_schedules
ADD COLUMN target_type TEXT NOT NULL DEFAULT 'plan';

ALTER TABLE bandwidth_schedules
ADD COLUMN subscriber_username TEXT NOT NULL DEFAULT '';

ALTER TABLE bandwidth_schedules
ADD COLUMN card_batch_id INTEGER;

ALTER TABLE bandwidth_schedules
ADD COLUMN priority INTEGER NOT NULL DEFAULT 100;

CREATE INDEX idx_bandwidth_schedules_target_plan
ON bandwidth_schedules(tenant_id, target_type, plan_id, enabled);

CREATE INDEX idx_bandwidth_schedules_target_subscriber
ON bandwidth_schedules(tenant_id, target_type, subscriber_username, enabled);

CREATE INDEX idx_bandwidth_schedules_target_batch
ON bandwidth_schedules(tenant_id, target_type, card_batch_id, enabled);
