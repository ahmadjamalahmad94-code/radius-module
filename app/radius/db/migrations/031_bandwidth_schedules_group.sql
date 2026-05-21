-- ╔════════════════════════════════════════════════════════════════════╗
-- ║  bandwidth_schedules — subscriber-group target                       ║
-- ║                                                                      ║
-- ║  Lets a speed rule target a whole subscriber_group, just like the    ║
-- ║  existing plan / subscriber / card_batch targets. The schedule       ║
-- ║  applies to every active member of the group.                        ║
-- ║                                                                      ║
-- ║  target_type values supported now:                                   ║
-- ║    "plan" | "subscriber" | "card_batch" | "subscriber_group"         ║
-- ║                                                                      ║
-- ║  When target_type = "subscriber_group", the rule binds via the new   ║
-- ║  subscriber_group_id FK (NULL otherwise).                            ║
-- ╚════════════════════════════════════════════════════════════════════╝

ALTER TABLE bandwidth_schedules
ADD COLUMN subscriber_group_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_bandwidth_schedules_target_group
ON bandwidth_schedules(tenant_id, target_type, subscriber_group_id, enabled);
