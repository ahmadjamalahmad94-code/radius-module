-- 098_admin_plan_prices.sql
-- أسعار العروض الخاصة بالمدراء («حسب المدير والاتفاق»):
-- سعر متفاوض عليه لكل (مدير × عرض) يتجاوز سعر العرض الرسمي عند تسعير
-- تفعيل/تجديد مشتركي ذلك المدير. غياب الصف = السعر الافتراضي للعرض.
-- أولوية التسعير النهائية: custom_price للمشترك > سعر المدير > سعر العرض.
-- Additive only — لا تعديل على أي جدول قائم.

CREATE TABLE IF NOT EXISTS admin_plan_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL DEFAULT 1,
    admin_id    INTEGER NOT NULL,              -- المدير (admins.id)
    plan_id     INTEGER NOT NULL,              -- العرض (access_plans.id)
    price       REAL    NOT NULL,              -- السعر الخاص المتفق عليه (>0)
    updated_at  TEXT    NOT NULL,              -- آخر تعديل (ISO UTC)
    updated_by  TEXT    NOT NULL DEFAULT '',   -- من عدّل (اسم المشغّل)
    FOREIGN KEY (admin_id) REFERENCES admins(id)       ON DELETE CASCADE,
    FOREIGN KEY (plan_id)  REFERENCES access_plans(id) ON DELETE CASCADE,
    UNIQUE (tenant_id, admin_id, plan_id)
);

CREATE INDEX IF NOT EXISTS ix_admin_plan_prices_admin
    ON admin_plan_prices (tenant_id, admin_id);
CREATE INDEX IF NOT EXISTS ix_admin_plan_prices_plan
    ON admin_plan_prices (tenant_id, plan_id);
