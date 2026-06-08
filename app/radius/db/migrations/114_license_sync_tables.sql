-- 113_license_sync_tables — جداول مزامنة لوحة التراخيص مع radius-module العميل.
--
-- البنية ثلاثية الطبقات:
--   لوحة التراخيص (radius-module-admin)
--     ↕ نفق WireGuard إداري (API فقط، بدون ترافيك بيانات)
--   لوحة العميل (radius-module)  ← هذه الهجرة
--     ↕ RADIUS عبر وكيل مركزي
--   عقد CHR المخصّصة
--
-- ما تفعله هذه الجداول:
--   license_snapshot        — نسخة من حدود الترخيص الحالية (تُحدَّث بالمزامنة)
--   service_allocation_mirror — نسخة من تخصيصات الخدمة المُرسَلة من لوحة التراخيص
--   vpn_account             — حسابات VPN ينشئها العميل في حدود التخصيص
--   wireguard_data_service  — إعداد خدمة WireGuard البيانات المحلية
--   wireguard_peer          — Peers ضمن خدمة WireGuard البيانات
--   service_audit_log       — سجل تدقيق محلي لكل تغيير على الخدمات
--
-- قواعد أمان مُدمَجة في التصميم:
--   * هذه الجداول لا تخزّن Private Keys أو Passwords نصية صريحة
--   * حدود service_allocation_mirror تأتي من لوحة التراخيص فقط (لا يعدّلها العميل)
--   * vpn_account لا يتجاوز max_accounts في ServiceAllocation
--   * wireguard_peer لا يتجاوز max_peers في ServiceAllocation
--   * wg-mgmt و wg-data واجهات منفصلة تمامًا

-- ─────────────────────────────────────────────────────────────────
-- 1. license_snapshot
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS license_snapshot (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               INTEGER NOT NULL DEFAULT 1,
    -- المعرّف الخارجي للترخيص في لوحة التراخيص
    remote_license_id       INTEGER NOT NULL,
    -- حدود عامة
    max_subscribers         INTEGER,
    max_cards               INTEGER,
    max_active_users        INTEGER,
    max_routers             INTEGER,
    -- حالة الترخيص: active | suspended | expired | cancelled
    license_status          TEXT    NOT NULL DEFAULT 'active',
    -- نوع الترخيص / الخطة
    plan_name               TEXT    NOT NULL DEFAULT '',
    -- تواريخ
    starts_at               TEXT,
    expires_at              TEXT,
    -- وقت آخر مزامنة من لوحة التراخيص
    synced_at               TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Hash للتحقق من صحة البيانات (sha256 للـ payload)
    payload_hash            TEXT    NOT NULL DEFAULT '',
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_license_snapshot_tenant
    ON license_snapshot(tenant_id);

-- ─────────────────────────────────────────────────────────────────
-- 2. service_allocation_mirror
-- ─────────────────────────────────────────────────────────────────
-- مرآة للتخصيصات المُرسَلة من لوحة التراخيص.
-- العميل لا يعدّل هذا الجدول مباشرةً — يقرأه فقط لمعرفة حدوده.
CREATE TABLE IF NOT EXISTS service_allocation_mirror (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               INTEGER NOT NULL DEFAULT 1,
    -- المعرّف الخارجي في لوحة التراخيص
    remote_allocation_id    INTEGER NOT NULL,
    -- نوع الخدمة: sstp | pptp | l2tp_ipsec | ikev2_ipsec | ip_change | site_exit | wireguard_data
    service_type            TEXT    NOT NULL,
    -- الحالة المُرسَلة من لوحة التراخيص: pending | active | suspended | expired | cancelled
    status                  TEXT    NOT NULL DEFAULT 'pending',
    -- CHR المُخصَّص (اسم + IP عام) — للعرض فقط
    chr_node_name           TEXT    NOT NULL DEFAULT '',
    chr_node_public_ip      TEXT    NOT NULL DEFAULT '',
    -- حدود الخدمة
    speed_limit_mbps        INTEGER NOT NULL DEFAULT 0,
    max_accounts            INTEGER NOT NULL DEFAULT 0,
    max_peers               INTEGER NOT NULL DEFAULT 0,
    transfer_limit_bytes    INTEGER,  -- NULL = بلا حد
    -- انتهاء الخدمة
    expires_at              TEXT,
    -- آخر مزامنة
    synced_at               TEXT    NOT NULL DEFAULT (datetime('now')),
    payload_hash            TEXT    NOT NULL DEFAULT '',
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tenant_id, remote_allocation_id)
);

CREATE INDEX IF NOT EXISTS ix_sam_tenant_service
    ON service_allocation_mirror(tenant_id, service_type, status);

-- ─────────────────────────────────────────────────────────────────
-- 3. vpn_account
-- ─────────────────────────────────────────────────────────────────
-- حسابات VPN ينشئها العميل داخل حدود التخصيص.
-- المستخدم النهائي يتصل بـ CHR بصيغة: username@realm
-- التحقق يمر: CHR → وكيل RADIUS → RADIUS هذا العميل → Accept/Reject
CREATE TABLE IF NOT EXISTS vpn_account (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               INTEGER NOT NULL DEFAULT 1,
    -- ربط بالتخصيص المحلي
    allocation_mirror_id    INTEGER NOT NULL REFERENCES service_allocation_mirror(id),
    -- بيانات الحساب
    username                TEXT    NOT NULL,  -- بدون @realm (يُضاف تلقائيًا)
    -- كلمة المرور مشفّرة (Fernet أو bcrypt) — لا نخزّن نصًا صريحًا
    password_hash           TEXT    NOT NULL DEFAULT '',
    -- معلومات العرض
    display_name            TEXT    NOT NULL DEFAULT '',
    max_concurrent          INTEGER NOT NULL DEFAULT 1,
    -- السرعة الخاصة بهذا الحساب (NULL = يرث حد التخصيص)
    speed_limit_mbps        INTEGER,
    -- الحالة: active | suspended | expired
    status                  TEXT    NOT NULL DEFAULT 'active',
    notes                   TEXT    NOT NULL DEFAULT '',
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tenant_id, username, allocation_mirror_id)
);

CREATE INDEX IF NOT EXISTS ix_vpn_account_tenant_alloc
    ON vpn_account(tenant_id, allocation_mirror_id, status);
CREATE INDEX IF NOT EXISTS ix_vpn_account_username
    ON vpn_account(tenant_id, username);

-- ─────────────────────────────────────────────────────────────────
-- 4. wireguard_data_service
-- ─────────────────────────────────────────────────────────────────
-- إعداد خدمة WireGuard البيانات المحلية على VPS العميل.
-- هذا مستقل تمامًا عن wg-mgmt الإداري.
-- اسم الواجهة المقترح: wg-data أو wg-exit
CREATE TABLE IF NOT EXISTS wireguard_data_service (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               INTEGER NOT NULL DEFAULT 1,
    allocation_mirror_id    INTEGER REFERENCES service_allocation_mirror(id),
    -- اسم الواجهة على نظام التشغيل
    interface_name          TEXT    NOT NULL DEFAULT 'wg-data',
    -- بيانات الاستماع
    listen_port             INTEGER NOT NULL DEFAULT 51820,
    -- Public Key فقط — Private Key لا يُحفظ في قاعدة البيانات
    public_key              TEXT    NOT NULL DEFAULT '',
    -- الشبكة الداخلية للـ peers
    address_range           TEXT    NOT NULL DEFAULT '10.100.0.0/24',
    -- حدود الخدمة (تُملأ من التخصيص)
    speed_limit_mbps        INTEGER NOT NULL DEFAULT 0,
    transfer_limit_bytes    INTEGER,
    max_peers               INTEGER NOT NULL DEFAULT 0,
    -- استخدام الشهر الحالي
    quota_period            TEXT    NOT NULL DEFAULT '',  -- YYYY-MM
    quota_bytes_used        INTEGER NOT NULL DEFAULT 0,
    -- الحالة: active | suspended | quota_exceeded
    status                  TEXT    NOT NULL DEFAULT 'active',
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_wg_data_service_tenant
    ON wireguard_data_service(tenant_id);

-- ─────────────────────────────────────────────────────────────────
-- 5. wireguard_peer
-- ─────────────────────────────────────────────────────────────────
-- Peers ضمن خدمة WireGuard البيانات.
-- ينشئها العميل عبر لوحته — لكل peer حد Peers من التخصيص.
CREATE TABLE IF NOT EXISTS wireguard_peer (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               INTEGER NOT NULL DEFAULT 1,
    service_id              INTEGER NOT NULL REFERENCES wireguard_data_service(id),
    -- Public Key الـ Peer (يُدخله المستخدم أو يُولَّد ويُرسَل كـ QR)
    public_key              TEXT    NOT NULL,
    -- العنوان المخصّص داخل الشبكة الداخلية
    allowed_ips             TEXT    NOT NULL DEFAULT '',
    peer_address            TEXT    NOT NULL DEFAULT '',
    -- بيانات للعرض فقط
    display_name            TEXT    NOT NULL DEFAULT '',
    notes                   TEXT    NOT NULL DEFAULT '',
    -- قيود اختيارية لهذا الـ Peer
    speed_limit_mbps        INTEGER,
    transfer_limit_bytes    INTEGER,
    quota_bytes_used        INTEGER NOT NULL DEFAULT 0,
    quota_period            TEXT    NOT NULL DEFAULT '',
    -- آخر مصافحة (last handshake) — يُحدَّث دوريًا
    last_handshake_at       TEXT,
    -- الحالة: active | suspended | quota_exceeded | revoked
    status                  TEXT    NOT NULL DEFAULT 'active',
    created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(service_id, public_key)
);

CREATE INDEX IF NOT EXISTS ix_wg_peer_service_status
    ON wireguard_peer(service_id, status);

-- ─────────────────────────────────────────────────────────────────
-- 6. service_audit_log
-- ─────────────────────────────────────────────────────────────────
-- سجل تدقيق محلي لكل تغيير على الخدمات (إنشاء / تعطيل / تجاوز كوتة ...).
-- يكمّل سجل التدقيق المركزي في لوحة التراخيص.
CREATE TABLE IF NOT EXISTS service_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    -- نوع الكيان: vpn_account | wireguard_peer | wireguard_data_service | license_snapshot | allocation_mirror
    entity_type     TEXT    NOT NULL,
    entity_id       INTEGER,
    -- الفعل: create | update | delete | suspend | activate | quota_exceeded | sync
    action          TEXT    NOT NULL,
    -- من نفّذ الإجراء: admin_id أو "system" أو "sync"
    actor           TEXT    NOT NULL DEFAULT 'system',
    description     TEXT    NOT NULL DEFAULT '',
    -- بيانات إضافية JSON
    meta_json       TEXT    NOT NULL DEFAULT '{}',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_service_audit_tenant_time
    ON service_audit_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_service_audit_entity
    ON service_audit_log(tenant_id, entity_type, entity_id);
