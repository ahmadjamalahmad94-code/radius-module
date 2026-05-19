-- 011_subscribers_advradius_ext
-- RM-H1: توسعة جدول subscribers بحقول AdvRadius (Hybrid: أعمدة DB + metadata JSON)
-- يضيف ~25 عمود + metadata JSON. كل ALTER يكون idempotent عبر migrations_runner.

-- ── حساب الإنترنت: override للسرعة على مستوى المستخدم ──
ALTER TABLE subscribers ADD COLUMN bandwidth_control_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN download_speed_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN upload_speed_kbps INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN custom_speed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN temporary_speed INTEGER NOT NULL DEFAULT 0;

-- ── الشبكة: DNS/Caller-ID/Connection file ──
ALTER TABLE subscribers ADD COLUMN caller_id TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN primary_dns_ppp TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN secondary_dns_ppp TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN device_connection_file TEXT NOT NULL DEFAULT '';

-- ── معلومات شخصية إضافية ──
ALTER TABLE subscribers ADD COLUMN nationality TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN country TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN payment_method TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN payment_reference TEXT NOT NULL DEFAULT '';

-- ── الكوتا والوقت — overrides على مستوى المستخدم ──
ALTER TABLE subscribers ADD COLUMN total_connection_time_min INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN daily_connection_time_min INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN download_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN upload_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN combined_quota_mb INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN connection_time_limit_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN quota_limit_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN equal_share_download INTEGER NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN equal_share_upload INTEGER NOT NULL DEFAULT 0;

-- ── أيام العمل + عدد الأجهزة + MACs إضافية ──
ALTER TABLE subscribers ADD COLUMN working_days TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN device_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE subscribers ADD COLUMN allowed_macs TEXT NOT NULL DEFAULT '';

-- ── metadata JSON (للحقول المتقدمة: MikroTik attrs, notifications channels, إلخ) ──
-- SQLite ما عنده JSONB — نستخدم TEXT مع helpers في Python.
-- البنية المتفق عليها: {"mikrotik":{}, "radius":{}, "advanced":{}, "notifications":{}}
ALTER TABLE subscribers ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';
