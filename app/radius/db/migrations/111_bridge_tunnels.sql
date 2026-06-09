-- 110_bridge_tunnels — الطرف المستهلِك لأنفاق CHR عبر الجسر الموقّع.
--
-- ⚠️ أمان (RADIUS مباع للعملاء): هذا الجدول لا يخزّن أي أسرار CHR/نفق خام
-- إطلاقًا — لا كلمة مرور SSTP ولا سر IPsec. يُحفظ فقط:
--   * بيانات وصفية تُعرض في قسم «الأنفاق» (اسم/نوع/حالة/عنوان/مستخدم).
--   * بصمة مرجعية لا رجعية للسر (secret_ref = "ref:<sha256[:12]>") لكشف
--     تغيّر كلمة المرور بين المزامنات دون الاحتفاظ بالسر نفسه.
-- كلمة المرور المستلمة من طلب النفق تُعاد لمرة واحدة للعرض/الحقن المحلي ثم
-- لا تُكتب في القاعدة (نفس نمط «العرض مرة واحدة» في migration 092).
--
-- مصدر الحقيقة هو لوحة التراخيص: العميل يطلب نفقًا ويزامن القائمة ويؤكّد
-- (ack) ما خزّنه. لا توليد بيانات اعتماد CHR في لوحة العميل.
--
-- دورة الحياة في status:
--   'active'    — النفق فعّال على الجسر.
--   'suspended' — موقوف مؤقتًا (نعطّله محليًا لكن نُبقي السجل).
--   'revoked'   — ملغى نهائيًا (نحذفه محليًا عند المزامنة).

CREATE TABLE IF NOT EXISTS bridge_tunnels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    -- مُعرّف النفق على الجسر/الـCHR (مفتاح المطابقة عند المزامنة/الـack).
    remote_name     TEXT    NOT NULL,
    tunnel_type     TEXT    NOT NULL DEFAULT 'sstp', -- sstp | pptp | l2tp | ipsec
    status          TEXT    NOT NULL DEFAULT 'active',-- active | suspended | revoked
    source          TEXT    NOT NULL DEFAULT 'synced',-- requested | synced
    -- اسم مستخدم SSTP/PPP (ليس سرًّا) — يُحقن محليًا لتشغيل النفق.
    username        TEXT    NOT NULL DEFAULT '',
    -- بصمة مرجعية لا رجعية للسر فقط (ليست السر) — انظر رأس الملف.
    secret_ref      TEXT    NOT NULL DEFAULT '',
    remote_address  TEXT    NOT NULL DEFAULT '',
    vpn_subnet      TEXT    NOT NULL DEFAULT '',
    -- 1 بعد إرسال ack للجسر بأن هذا النفق خُزِّن محليًا (يوقف إعادة كلمة المرور).
    acked           INTEGER NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1, -- 0 عند suspended (تعطيل محلي)
    notes           TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL DEFAULT '',
    last_synced_at  TEXT    NOT NULL DEFAULT '',
    UNIQUE (tenant_id, remote_name)
);

CREATE INDEX IF NOT EXISTS ix_bridge_tunnels_status
    ON bridge_tunnels (tenant_id, status);
