-- ╔════════════════════════════════════════════════════════════════════╗
-- ║  bandwidth_schedules — per-day support                              ║
-- ║                                                                      ║
-- ║  Each speed rule already has a time window (starts_at_time /         ║
-- ║  ends_at_time). This adds a CSV list of day codes (sat,sun,...)      ║
-- ║  so the operator can say "only on weekends" or "only on Sat+Mon+Thu".║
-- ║                                                                      ║
-- ║  Empty value = applies every day (current behaviour, unchanged for   ║
-- ║  pre-migration rows).                                                ║
-- ║                                                                      ║
-- ║  Day codes are the same canonical set used by access_schedule:       ║
-- ║    sat,sun,mon,tue,wed,thu,fri                                       ║
-- ╚════════════════════════════════════════════════════════════════════╝

ALTER TABLE bandwidth_schedules
ADD COLUMN days_csv TEXT NOT NULL DEFAULT '';
