-- «منع استنساخ MAC» (feat/anti-mac-clone) — يونيو 2026.
-- إضافي/خامل: لا يلمس أي جدول قائم. نمط schema-heal (CREATE TABLE IF NOT EXISTS)
-- آمن للتشغيل المتكرّر. كل الميزة عبارة عن toggle داخل tenant_settings افتراضه OFF،
-- فمن لم يفعّلها لا يرى تغييرًا في سلوك MAC-cookie المعتاد.
--
-- ⚠️ تنبيه ترقيم: فرع feat/store-chat-attachment-idle-reminder (محلّيًا) يستخدم
-- أيضًا الرقم 125. لا تصادم وظيفي (جدول مختلف تمامًا) — عند الدمج المتسلسل
-- أعِد ترقيم أحدهما إلى 126. راجع _MIGRATION_ALIASES في الـrunner.
--
-- الميزة بإيجاز: لإيقاف سرقة جلسة عبر استنساخ MAC (نسخ عنوان MAC والاتصال
-- من جهاز ثانٍ) بدون إيقاف الـMAC-cookie (إذ يكسر تجربة الدخول السلس)، نربط
-- MAC إلى «بصمة جهاز» منذ أول دخول ناجح. ثم نُعيد التحقق على كل auth: نفس
-- MAC + بصمة مطابقة = جهاز حقيقي. نفس MAC + بصمة مختلفة = استنساخ → رفض/تصعيد.

-- (1) mac_clone_bindings — البصمة المُلْزَمة بكل (مستأجر، مستخدم، MAC).
-- صفّ يصف «الجهاز الشرعي» الذي ربطه المستخدم بهذا الـMAC أوّل مرّة. إشاراته
-- مأخوذة وقت أول دخول ناجح من: RADIUS auth attrs (Calling/Called-Station-Id،
-- NAS-Port-Type، Connect-Info)، DHCP lease (hostname، dhcp_class_id، os_family
-- من device_fingerprints)، وUser-Agent عند توفّره من بوابة الكابتيف. حقول
-- السياق (nas_ip، called_station، nas_port) تُمسك «السياق النموذجي» لمساعدة
-- كشف الترحال المستحيل، وليست حصرية للجهاز.
CREATE TABLE IF NOT EXISTS mac_clone_bindings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    username        TEXT    NOT NULL,              -- subscriber/card username
    mac             TEXT    NOT NULL,              -- AA:BB:CC:DD:EE:FF UPPER
    -- إشارات الجهاز (البصمة الجوهرية):
    hostname        TEXT    NOT NULL DEFAULT '',   -- من DHCP (Option-12)
    dhcp_class_id   TEXT    NOT NULL DEFAULT '',   -- من DHCP (Option-60)
    os_family       TEXT    NOT NULL DEFAULT '',   -- android/ios/windows/macos/linux
    device_brand    TEXT    NOT NULL DEFAULT '',
    device_model    TEXT    NOT NULL DEFAULT '',
    ua_hash         TEXT    NOT NULL DEFAULT '',   -- SHA-256 (User-Agent) عند الربط
    ua_sample       TEXT    NOT NULL DEFAULT '',   -- بادئة قصيرة (بدون PII)
    vendor_oui      TEXT    NOT NULL DEFAULT '',   -- أول 3 octets من MAC
    -- سياق نموذجي (يُستخدم في كشف الترحال المستحيل + الجلسات المتزامنة):
    nas_ip          TEXT    NOT NULL DEFAULT '',
    called_station  TEXT    NOT NULL DEFAULT '',   -- AP/SSID/BSSID
    nas_port        TEXT    NOT NULL DEFAULT '',
    nas_port_type   TEXT    NOT NULL DEFAULT '',
    -- دورة الحياة:
    status          TEXT    NOT NULL DEFAULT 'active',   -- active|superseded|suspended
    bind_confidence TEXT    NOT NULL DEFAULT 'medium',   -- low|medium|high
    first_seen_at   TEXT    NOT NULL DEFAULT '',
    last_seen_at    TEXT    NOT NULL DEFAULT '',
    last_verified_at TEXT   NOT NULL DEFAULT '',
    verify_count    INTEGER NOT NULL DEFAULT 0,
    mismatch_count  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(tenant_id, username, mac)
);

CREATE INDEX IF NOT EXISTS ix_macbind_tenant_user
    ON mac_clone_bindings (tenant_id, username);
CREATE INDEX IF NOT EXISTS ix_macbind_tenant_mac
    ON mac_clone_bindings (tenant_id, mac);
CREATE INDEX IF NOT EXISTS ix_macbind_tenant_status
    ON mac_clone_bindings (tenant_id, status);

-- (2) mac_clone_events — سجلّ حدث-لكل-قرار (للتدقيق + جدول الواجهة + التنبيهات).
-- يُكتب على: bind (أول ربط) | verify_ok (دخول لاحق متطابق) | clone_detected
-- (بصمة مختلفة) | stepup_required (مطلوب تصعيد كلمة مرور) | concurrent_kick
-- (جلسة متزامنة من سياق متباعد). signals يُخزَّن JSON قصير يحوي قائمة الإشارات
-- التي تباينت كي تظهر في الواجهة بدون تخمين لاحق.
CREATE TABLE IF NOT EXISTS mac_clone_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    username        TEXT    NOT NULL,
    mac             TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,              -- bind|verify_ok|clone_detected|
                                                   -- stepup_required|concurrent_kick
    decision        TEXT    NOT NULL DEFAULT '',   -- allow|deny|stepup|monitor
    confidence      TEXT    NOT NULL DEFAULT '',   -- low|medium|high
    score           INTEGER NOT NULL DEFAULT 0,    -- 0..100 درجة الخطورة
    signals         TEXT    NOT NULL DEFAULT '',   -- JSON قصير
    nas_ip          TEXT    NOT NULL DEFAULT '',
    called_station  TEXT    NOT NULL DEFAULT '',
    nas_port        TEXT    NOT NULL DEFAULT '',
    reason          TEXT    NOT NULL DEFAULT '',   -- نص مختصر بالعربية
    created_at      TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_macevt_tenant_time
    ON mac_clone_events (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS ix_macevt_tenant_user
    ON mac_clone_events (tenant_id, username, created_at);
CREATE INDEX IF NOT EXISTS ix_macevt_tenant_type
    ON mac_clone_events (tenant_id, event_type, created_at);
