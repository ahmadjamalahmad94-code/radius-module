-- نفق تغيير IP (تمرير الترافيك) — خدمة مرخّصة مدفوعة محدودة السرعة لكل راوتر.
--
-- ⚠️ محجوزة لإعادة بناء مركزية: أُزيلت ميزة الأنفاق/التراخيص من لوحة العميل
-- (يُعاد بناؤها مركزياً عبر لوحة التراخيص — قرار معماري). هذه الهجرة تبقى
-- كما هي تفادياً لانحراف المخطط على قواعد طُبّقت عليها؛ الجداول موجودة لكن
-- غير مستخدمة حالياً (لا كود يقرأها/يكتبها بعد إزالة router_ip_tunnel_*).
--
-- هذه الخدمة ليست مجانية وليست بيد المشترك: التفعيل يتم من «لوحة التراخيص»
-- (admin/radius/licensing) بسرعة مطلوبة لكل سرعة سعر. النموذج:
--   طلب الخدمة (requested) → تسعير حسب الشريحة → منح/تفعيل (active) من اللوحة
--   → فرض حد السرعة على الراوتر عند تجهيز النفق.
--
-- يعمل لإصدارَي RouterOS 6 و 7. النفق الفعلي يُبنى فوق بنية الأنفاق القائمة
-- (راجع services/v6_tunnels.py + router_tunnels_repo + migration 092)؛ هذا
-- الجدول يحمل طبقة الترخيص/التسعير/الحالة فقط، لا أسرار النفق.
--
-- قرار التصميم:
--   * ترخيص واحد فعّال لكل راوتر (نضمنه بفهرس جزئي فريد على status='active')،
--     مع السماح بسجلّات تاريخية (مطلوب/معلّق/منتهٍ) للراوتر نفسه للتدقيق.
--   * شرائح السعر تُخزَّن في جدول router_ip_tunnel_speed_prices المبذور أدناه
--     (أبسط وأكثر متانة من تشتيت القيم في الإعدادات؛ قابل للتحرير لاحقًا).

-- ── شرائح السرعة وأسعارها (قابلة للتعديل من اللوحة لاحقًا) ──
CREATE TABLE IF NOT EXISTS router_ip_tunnel_speed_prices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER NOT NULL DEFAULT 1,
    speed_kbps    INTEGER NOT NULL,           -- السرعة بالكيلوبت/ث (2Mbps = 2000)
    price_minor   INTEGER NOT NULL DEFAULT 0, -- السعر بأصغر وحدة عملة (قروش/سنت)
    label         TEXT    NOT NULL DEFAULT '',-- وصف الشريحة المعروض (مثل «2 ميجابت»)
    enabled       INTEGER NOT NULL DEFAULT 1,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT '',
    UNIQUE (tenant_id, speed_kbps)
);

-- بذر شرائح ابتدائية (2/5/10/20 ميجابت) للمستأجر الافتراضي.
-- الأسعار قيم مبدئية يضبطها المشغّل من اللوحة — تبقى idempotent عبر OR IGNORE.
INSERT OR IGNORE INTO router_ip_tunnel_speed_prices
    (tenant_id, speed_kbps, price_minor, label, enabled, sort_order, created_at)
VALUES
    (1,  2000,  500000, '2 ميجابت',  1, 10, ''),
    (1,  5000, 1000000, '5 ميجابت',  1, 20, ''),
    (1, 10000, 1800000, '10 ميجابت', 1, 30, ''),
    (1, 20000, 3200000, '20 ميجابت', 1, 40, '');

-- ── تراخيص نفق تغيير IP لكل راوتر ──
CREATE TABLE IF NOT EXISTS router_ip_tunnel_licenses (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id            INTEGER NOT NULL DEFAULT 1,
    router_id            INTEGER NOT NULL,            -- nas_devices.id
    requested_speed_kbps INTEGER NOT NULL,
    price_minor          INTEGER NOT NULL DEFAULT 0,  -- السعر المثبَّت وقت الطلب
    status               TEXT    NOT NULL DEFAULT 'requested',
        -- requested | active | suspended | expired
    requested_by         TEXT    NOT NULL DEFAULT '', -- اسم المشغّل صاحب الطلب
    granted_by           TEXT    NOT NULL DEFAULT '', -- من فعّل/منح من اللوحة
    requested_at         TEXT    NOT NULL DEFAULT '',
    granted_at           TEXT    NOT NULL DEFAULT '',
    expires_at           TEXT    NOT NULL DEFAULT '', -- فارغ = بلا انتهاء
    notes                TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_router_ip_tunnel_licenses_router
    ON router_ip_tunnel_licenses (tenant_id, router_id, id DESC);

CREATE INDEX IF NOT EXISTS ix_router_ip_tunnel_licenses_status
    ON router_ip_tunnel_licenses (tenant_id, status);

-- ترخيص فعّال واحد كحدّ أقصى لكل راوتر (فهرس جزئي فريد). السجلات بحالات أخرى
-- (مطلوب/معلّق/منتهٍ) تبقى مسموحة للتاريخ والتدقيق.
CREATE UNIQUE INDEX IF NOT EXISTS ux_router_ip_tunnel_licenses_one_active
    ON router_ip_tunnel_licenses (tenant_id, router_id)
    WHERE status = 'active';
