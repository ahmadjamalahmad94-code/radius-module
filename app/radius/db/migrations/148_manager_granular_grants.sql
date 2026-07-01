-- Granular per-manager grants (owner-configured, server-enforced).
-- Three levels, all additive columns on the existing per-entity policy row
-- (manager_distributor_policies) — we BUILD ON the existing policy store
-- instead of forking a parallel table:
--
--   • section_access_json : {"<section>": "open"|"locked"|"hidden", ...}
--       per-section 3-state access. Absent key => "open" (non-regressive:
--       role RBAC keeps governing until the owner locks/hides a section).
--   • action_grants_json  : {"<entity>": {"create":bool,"edit":bool,"delete":bool}}
--       per-action gating inside an open section (level 2).
--   • field_grants_json   : {"<entity>": ["field", ...]}
--       per-field edit control (level 3). A key PRESENT (even empty list)
--       flips that entity into restrictive mode: only listed fields are
--       editable; every other field is dropped/ignored server-side. A key
--       ABSENT => field control OFF for that entity (all fields editable,
--       today's behavior — non-regressive).
--
-- Owner/super (primary owner) always bypasses every level.
-- Defaults are the empty object => no restriction until the owner configures.

ALTER TABLE manager_distributor_policies
  ADD COLUMN section_access_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE manager_distributor_policies
  ADD COLUMN action_grants_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE manager_distributor_policies
  ADD COLUMN field_grants_json TEXT NOT NULL DEFAULT '{}';
