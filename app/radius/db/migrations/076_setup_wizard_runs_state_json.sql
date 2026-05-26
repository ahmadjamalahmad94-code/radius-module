-- Setup Wizard v3 — backfill the missing state_json column.
--
-- Migration 075 added 10 v3 columns to setup_wizard_runs but forgot
-- `state_json` — the JSON blob the v3 state machine uses to persist
-- per-run inputs (router name, type, VPN IP, paste-back outputs, etc.)
-- across state transitions.
--
-- Without this column the v3 wizard returns 500 on POST /runs because
-- V3Repo.create_run() writes to state_json on every new run.
--
-- Additive only. Existing v2 rows get '{}' as default and remain valid.

ALTER TABLE setup_wizard_runs
  ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}';
