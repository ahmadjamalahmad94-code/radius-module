-- MT34 — اسم المدير يتفرّد **لكل شبكة** لا عالميًّا.
--
-- العلّة: وُلد ``admins`` في الهجرة 001 قبل أن يصير النظام متعدّد الجهات،
-- فحمل ``username TEXT UNIQUE`` — تفرّدًا عالميًّا. وبقيّة النظام صحيحة
-- أصلًا: المشتركون والكروت والباقات والموزّعون كلّها ``UNIQUE(tenant_id, …)``.
--
-- الأثر قبل الإصلاح: مالك الشبكة (ب) يُمنَع من تسمية مديره ``ahmad`` لأن
-- الشبكة (أ) سبقته — «admin 'ahmad' already exists». مَنعٌ وإفشاءٌ معًا:
-- عَلِم أن شبكةً أخرى تستخدم الاسم. وهذا يناقض أساس المنتج: كل شبكة
-- «ريديوس» مستقلّ لا يعلم بوجود غيره.
--
-- العلاج: عمود ``tenant_id`` على المدير + ``UNIQUE(COALESCE(tenant_id,0),
-- username)``. فـ``ahmad`` في الشبكة 6 و``ahmad`` في الشبكة 7 حسابان
-- مختلفان تمامًا بكلمتَي مرور مختلفتين. و``tenant_id IS NULL`` = حساب
-- المزوّد العامّ (يدخل أي شبكة للدعم).
--
-- SQLite لا يُسقط قيد UNIQUE على عمود (فهرسٌ ضمنيّ)، فلا مفرّ من إعادة
-- بناء الجدول: نسخة بنفس الأعمدة وترتيبها + tenant_id في آخرها، فينجح
-- ``SELECT *, NULL`` بلا تعداد ٣٨ عمودًا. المفاتيح الأجنبيّة الستّة تُشير
-- إلى ``admins.id`` (لا username) والمعرّفات محفوظة، فلا تنكسر.

PRAGMA foreign_keys = OFF;

CREATE TABLE admins_mt34 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT DEFAULT '',
    email           TEXT DEFAULT '',
    mobile          TEXT DEFAULT '',
    role_id         INTEGER,
    is_super_admin  INTEGER NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_login_at   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT,
    phone           TEXT NOT NULL DEFAULT '',
    last_login_ip   TEXT NOT NULL DEFAULT '',
    profile_notes   TEXT NOT NULL DEFAULT '',
    avatar_url      TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '',
    metadata        TEXT NOT NULL DEFAULT '{}',
    deleted_at      TEXT,
    deleted_by      TEXT NOT NULL DEFAULT '',
    delete_reason   TEXT NOT NULL DEFAULT '',
    archive_source  TEXT NOT NULL DEFAULT '',
    archive_policy_id INTEGER,
    retention_expires_at TEXT,
    auto_archive_at TEXT,
    external_identity_provider TEXT NOT NULL DEFAULT '',
    external_subject TEXT NOT NULL DEFAULT '',
    external_password_hash_scheme TEXT NOT NULL DEFAULT '',
    external_password_version INTEGER NOT NULL DEFAULT 0,
    managed_by_license_admin INTEGER NOT NULL DEFAULT 0,
    external_updated_at TEXT NOT NULL DEFAULT '',
    locale          TEXT NOT NULL DEFAULT '',
    debt_cap_enabled INTEGER NOT NULL DEFAULT 0,
    debt_cap_minor  INTEGER NOT NULL DEFAULT 0,
    loan_cap_enabled INTEGER NOT NULL DEFAULT 0,
    loan_cap_minor  INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    parent_admin_id INTEGER,
    -- الجديد (آخر عمود كي يعمل SELECT *, NULL):
    tenant_id       INTEGER,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE SET NULL
);

INSERT INTO admins_mt34 SELECT *, NULL FROM admins;

DROP TABLE admins;
ALTER TABLE admins_mt34 RENAME TO admins;

-- الفهارس السابقة (كانت على الجدول القديم فذهبت مع DROP).
CREATE INDEX IF NOT EXISTS idx_admins_deleted ON admins(deleted_at, enabled);
CREATE INDEX IF NOT EXISTS ix_admins_external_identity
  ON admins (external_identity_provider, external_subject);
CREATE INDEX IF NOT EXISTS ix_admins_managed_by_license_admin
  ON admins (managed_by_license_admin, enabled);
CREATE INDEX IF NOT EXISTS ix_admins_parent ON admins (parent_admin_id);

-- ربط كل مدير بشبكته من عضويّته (أوّل عضوية = شبكته).
UPDATE admins
   SET tenant_id = (SELECT m.tenant_id FROM tenant_memberships m
                     WHERE m.admin_id = admins.id
                     ORDER BY m.tenant_id LIMIT 1)
 WHERE tenant_id IS NULL;

-- حساب المزوّد (الجذر، أصغر معرّف) يبقى عامًّا: يدخل أي شبكة للدعم.
UPDATE admins SET tenant_id = NULL
 WHERE id = (SELECT MIN(id) FROM admins);

-- التفرّد الجديد: لكل شبكة على حدة (COALESCE كي تشترك الحسابات العامّة
-- في نطاقٍ واحد بدل أن يُفلت كلُّ NULL من القيد).
CREATE UNIQUE INDEX IF NOT EXISTS idx_admins_username_per_tenant
  ON admins (COALESCE(tenant_id, 0), username);

CREATE INDEX IF NOT EXISTS ix_admins_tenant ON admins (tenant_id, enabled);

PRAGMA foreign_keys = ON;
