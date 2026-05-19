-- 012_plans_advradius_ext
-- RM-H3: توسعة access_plans بحقول AdvRadius (Hybrid: أعمدة DB + metadata JSON)
-- نفس النمط المتبَّع في 011_subscribers_advradius_ext.

-- ── السرعة المتقدمة (CIR + Speed control toggle) ──
ALTER TABLE access_plans ADD COLUMN speed_control_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN cir_down_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN cir_up_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN burst_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN nightly_unlimited_enabled INTEGER NOT NULL DEFAULT 0;

-- ── كوتا شهرية/يومية مفصَّلة (download+upload+combined) ──
-- موجود سابقًا: quota_total_mb / quota_daily_mb / quota_monthly_mb (إجمالية)
-- نضيف الـ download/upload منفصلة + combined
ALTER TABLE access_plans ADD COLUMN monthly_download_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN monthly_upload_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN monthly_combined_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN daily_download_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN daily_upload_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN daily_combined_quota_mb INTEGER NOT NULL DEFAULT 0;

-- ── سلوك الاستخدام ──
ALTER TABLE access_plans ADD COLUMN single_use_once INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN max_consumption_times INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN ticket_validity_days INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN working_hours_limit INTEGER NOT NULL DEFAULT 0;

-- ── خدمات الـ NAS (Hotspot / PPPoE toggles) ──
ALTER TABLE access_plans ADD COLUMN hotspot_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_plans ADD COLUMN ppp_enabled INTEGER NOT NULL DEFAULT 0;

-- ── ساعات العرض (إضافة لـ allowed_hours_from/to) ──
ALTER TABLE access_plans ADD COLUMN offer_hours_from TEXT NOT NULL DEFAULT '';
ALTER TABLE access_plans ADD COLUMN offer_hours_to TEXT NOT NULL DEFAULT '';

-- ── metadata JSON: subscription/advanced/mikrotik/notifications ──
-- البنية: {"general":{}, "subscription":{}, "advanced":{}, "mikrotik":{}, "notifications":{}}
ALTER TABLE access_plans ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';
