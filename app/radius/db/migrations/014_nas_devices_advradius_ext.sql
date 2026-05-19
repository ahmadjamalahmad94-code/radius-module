-- 014_nas_devices_advradius_ext
-- RM-H5: توسعة nas_devices بحقول AdvRadius + test-connection tracking

-- ── Test connection tracking ──
ALTER TABLE nas_devices ADD COLUMN last_check_at TEXT;
ALTER TABLE nas_devices ADD COLUMN last_check_status TEXT NOT NULL DEFAULT '';

-- ── RFC 3579 — Message-Authenticator enforcement ──
ALTER TABLE nas_devices ADD COLUMN require_message_authenticator INTEGER NOT NULL DEFAULT 0;

-- ── SSH access port ──
ALTER TABLE nas_devices ADD COLUMN ssh_port INTEGER NOT NULL DEFAULT 22;

-- ── تنظيم وتصنيف ──
ALTER TABLE nas_devices ADD COLUMN tags TEXT NOT NULL DEFAULT '';   -- CSV

-- ── metadata JSON (للحقول المستقبلية) ──
ALTER TABLE nas_devices ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';
