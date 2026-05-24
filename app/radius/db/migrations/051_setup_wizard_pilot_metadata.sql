-- Wave H: pilot-readiness metadata for versioned script generation.
-- Additive metadata columns only.

ALTER TABLE setup_wizard_steps ADD COLUMN planner_version TEXT NOT NULL DEFAULT '';
ALTER TABLE setup_wizard_steps ADD COLUMN script_version TEXT NOT NULL DEFAULT '';
ALTER TABLE setup_wizard_steps ADD COLUMN target_routeros_version TEXT NOT NULL DEFAULT '';
ALTER TABLE setup_wizard_steps ADD COLUMN compatibility_warnings_json TEXT NOT NULL DEFAULT '[]';
