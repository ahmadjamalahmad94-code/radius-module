-- L2: setup-wizard support for nas_devices.
--
-- Adds two columns the L3 wizard backend writes:
--   ros_version       — '6' / '7' / '' (unknown). Drives which
--                       RouterOS script template the wizard renders
--                       on the result page.
--   provisioned_at    — UTC ISO timestamp the wizard wrote this row;
--                       absent for rows added through the legacy
--                       /devices/new form. Used by the Operations
--                       Center to flag rows that were auto-provisioned
--                       versus manually configured.
--
-- See docs/MIKROTIK_CONTROL_PLAN.md Phase L for the full flow.

ALTER TABLE nas_devices ADD COLUMN ros_version TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN provisioned_at TEXT NOT NULL DEFAULT '';
