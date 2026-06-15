-- «اتصال بيانات» (feat/data-connection-oneclick) — يونيو 2026.
-- إضافي/خامل بالكامل: لا يلمس أي مسار حيّ قائم.
--
-- (1) subscribers.transport — يُثبّت حقل النقل الذي عرّفته 2a
--     (feat/accel-ppp-radius-attrs) في الـdataclass فقط. الافتراضي
--     'chr_mikrotik' = المسار القديم حرفيًّا؛ 'vps_accel' = يخدمه
--     accel-ppp مباشرة على الـVPS (شيفت Filter-Id بسرعة 5 ميجابت).
--     ⚠️ SQLite لا يدعم ADD COLUMN IF NOT EXISTS؛ هذا العمود جديد كليًّا.
ALTER TABLE subscribers ADD COLUMN transport TEXT NOT NULL DEFAULT 'chr_mikrotik';

-- (2) data_connection_wg_peers — نموذج قرين WireGuard لاتصال بيانات v7.
--     صفّ لكل قرين: المفتاح العام للعميل (نُخزّنه)، عنوان النفق المُسنَد من
--     مجمّع WG لكل VPS، ونقطة النهاية. **لا نُخزّن المفتاح الخاص للعميل
--     إطلاقًا** — يُولَّد ويُعرض مرّة واحدة داخل السكربت ثم يُنسى.
--
--     علمان LAB-PENDING (افتراضهما 0 = لم يُطبَّق على الـVPS الحيّ بعد):
--       applied_to_vps — هل دُفع القرين فعلًا إلى واجهة WG على الـVPS؟
--       queue_applied  — هل طُبِّق سقف 5 ميجابت (queue/tc) لهذا القرين؟
--     كلاهما متابعة مخبرية؛ النموذج والسكربت يُبنيان الآن.
CREATE TABLE IF NOT EXISTS data_connection_wg_peers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    subscriber_id   INTEGER NOT NULL,
    username        TEXT    NOT NULL DEFAULT '',
    public_key      TEXT    NOT NULL DEFAULT '',
    assigned_ip     TEXT    NOT NULL DEFAULT '',
    endpoint_host   TEXT    NOT NULL DEFAULT '',
    endpoint_port   INTEGER NOT NULL DEFAULT 0,
    allowed_address TEXT    NOT NULL DEFAULT '0.0.0.0/0',
    speed_kbit      INTEGER NOT NULL DEFAULT 5120,
    applied_to_vps  INTEGER NOT NULL DEFAULT 0,   -- LAB-PENDING
    queue_applied   INTEGER NOT NULL DEFAULT 0,   -- LAB-PENDING
    status          TEXT    NOT NULL DEFAULT 'pending',
    created_at      TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_data_connection_wg_peers_tenant
    ON data_connection_wg_peers (tenant_id, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_data_connection_wg_peers_ip
    ON data_connection_wg_peers (tenant_id, assigned_ip);
