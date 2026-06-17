-- «نمط السماح» (Allow-mode) — مكمّل لـ«التحكم بالدخول» (feat/access-control)
-- يونيو 2026. إضافي/خامل: لا يلمس أي جدول قائم. كل الميزة OFF افتراضيًّا
-- (الصفّ غير موجود = لا نمط سماح = السلوك المعتاد).
--
-- ⚠️ تنبيه ترقيم: على main يصل الترقيم إلى 124. الرقم 125 محجوز بفرعين
-- غير مدموجين (feat/anti-mac-clone و feat/store-chat-attachment-idle-reminder)
-- لجدولين مختلفين تمامًا — لذلك أخذنا 126 لتفادي ثالث تصادم. عند الدمج المرتّب
-- أعِد الترقيم إذا تطلّب الأمر (راجع _MIGRATION_ALIASES في الـrunner).
--
-- ─────────────────────────────────────────────────────────────────────────
-- الفكرة: «نمط السماح» (allow-mode) هو المكمّل العكسي لـ«قائمة الحظر»
-- (blocklist). يُطبَّق على نطاق عرض/باقة (plan) أو حزمة بطاقات (card_batch)
-- ويختار المسؤول واحدًا من ثلاثة أنماط:
--
--   1) open       — بدون ربط أجهزة. حدّ الجلسات المتزامنة من العرض/الباقة
--                   كما هو. مناسب للبطاقات العامّة المتنقّلة (بطاقة الدفع
--                   في محل، بطاقة الكاشير، إلخ).
--   2) tofu       — Trust-On-First-Use: على أوّل دخول ناجح نَلْزَم MAC الجهاز
--                   إلى الحساب تلقائيًّا. عدد الأجهزة محدود بـmax_devices.
--                   بعد الامتلاء، الأجهزة الجديدة تُرفض (مضادّ مشاركة البطاقة).
--   3) manual     — قائمة سماح يدوية. الافتراضي رفض (default-deny). يضيف
--                   المسؤول الأجهزة بنفسه قبل أي دخول. مناسب لمكتب/مبنى مغلق.
--
-- صفّ السياسة (allow_mode_policies) لكل (مستأجر، نطاق، معرّف نطاق). فريد.
-- جدول الأجهزة (allow_mode_devices) ملحق بسياسة، ويدعم username='' كحقل
-- مشترك (الجهاز متاح لكل مستخدمي النطاق — مفيد للمكاتب التي تتشارك أجهزة)،
-- أو username='specific' للأجهزة المربوطة بحساب واحد فقط (TOFU دائمًا).
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS allow_mode_policies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    scope_type    TEXT    NOT NULL,                -- plan | card_batch
    scope_id      INTEGER NOT NULL,
    mode          TEXT    NOT NULL DEFAULT 'open', -- open | tofu | manual
    max_devices   INTEGER NOT NULL DEFAULT 1,      -- 0/سالب = بلا حدّ (TOFU)
    active        INTEGER NOT NULL DEFAULT 1,
    note          TEXT    NOT NULL DEFAULT '',
    created_by    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT '',
    updated_at    TEXT    NOT NULL DEFAULT '',
    UNIQUE(tenant_id, scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS ix_amp_tenant_scope
    ON allow_mode_policies (tenant_id, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS ix_amp_tenant_active
    ON allow_mode_policies (tenant_id, active);

-- جهاز ضمن سياسة. username='' = مشترك بين كل مستخدمي النطاق (للأنماط
-- اليدوية في المكاتب). username='specific' = ربط شخصي (TOFU، أو يدوي خاص).
CREATE TABLE IF NOT EXISTS allow_mode_devices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id     INTEGER NOT NULL,
    username      TEXT    NOT NULL DEFAULT '',    -- '' = مشترك، 'x' = شخصي
    mac           TEXT    NOT NULL,                -- AA:BB:CC:DD:EE:FF UPPER
    source        TEXT    NOT NULL DEFAULT 'manual', -- manual | auto
    label         TEXT    NOT NULL DEFAULT '',    -- اسم وصفي للمسؤول
    last_seen_at  TEXT    NOT NULL DEFAULT '',
    use_count     INTEGER NOT NULL DEFAULT 0,
    created_by    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT '',
    UNIQUE(policy_id, username, mac)
);

CREATE INDEX IF NOT EXISTS ix_amd_policy
    ON allow_mode_devices (policy_id);
CREATE INDEX IF NOT EXISTS ix_amd_policy_user
    ON allow_mode_devices (policy_id, username);
CREATE INDEX IF NOT EXISTS ix_amd_policy_mac
    ON allow_mode_devices (policy_id, mac);
