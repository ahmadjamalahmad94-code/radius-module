-- 015_admins_rbac_ext
-- RM-H6: توسعة admins + roles بحقول profile + RBAC مرئية

-- ── Admins: profile fields ──
ALTER TABLE admins ADD COLUMN phone TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN last_login_ip TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN profile_notes TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN avatar_url TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN tags TEXT NOT NULL DEFAULT '';
ALTER TABLE admins ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';

-- ── Roles: لون لتمييز الـ role في الـ UI ──
ALTER TABLE roles ADD COLUMN color TEXT NOT NULL DEFAULT '#2BAACC';
ALTER TABLE roles ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';
