-- ╔════════════════════════════════════════════════════════════════════╗
-- ║  Unified access-schedule column — connection_schedule (JSON TEXT)    ║
-- ║                                                                      ║
-- ║  A reusable day/time window schedule that replaces the simpler       ║
-- ║  working_days CSV. See app/radius/core/access_schedule.py for the    ║
-- ║  data model and SERVICES_COOKBOOK §17 for the design.                ║
-- ║                                                                      ║
-- ║  - Empty / NULL  → no restriction (access always allowed).           ║
-- ║  - Otherwise stores the JSON dict {"windows":[...]} as produced by   ║
-- ║    access_schedule.serialize(...).                                   ║
-- ║                                                                      ║
-- ║  The legacy working_days column stays as a denormalized cache of     ║
-- ║  the days mentioned in any window — updated by code on every save.  ║
-- ║  Existing rows continue to use working_days; the new picker writes   ║
-- ║  to BOTH for back-compat.                                            ║
-- ╚════════════════════════════════════════════════════════════════════╝

ALTER TABLE subscribers
ADD COLUMN connection_schedule TEXT NOT NULL DEFAULT '';

ALTER TABLE subscriber_groups
ADD COLUMN connection_schedule TEXT NOT NULL DEFAULT '';

ALTER TABLE access_plans
ADD COLUMN connection_schedule TEXT NOT NULL DEFAULT '';
