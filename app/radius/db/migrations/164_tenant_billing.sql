-- 164_tenant_billing — حقول فوترة الجهة للوحة المزوّد (استضافة دائمة).
-- المزوّد يتابع لكل جهة: مجاني/مدفوع، المبلغ، مدفوع حتى متى، وملاحظة.
ALTER TABLE tenants ADD COLUMN billing_mode   TEXT NOT NULL DEFAULT 'free';  -- free | paid
ALTER TABLE tenants ADD COLUMN billing_amount REAL NOT NULL DEFAULT 0;       -- المبلغ (بعملة الجهة)
ALTER TABLE tenants ADD COLUMN paid_until     TEXT;                          -- ISO date أو NULL
ALTER TABLE tenants ADD COLUMN billing_note   TEXT NOT NULL DEFAULT '';
