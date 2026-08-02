-- بوابة فحص الكروت للموزّع: كلمة مرور دخول البوابة (hash فقط، لا نص صريح).
-- NULL = لا دخول للبوابة. الدخول يتطلب أيضًا صلاحية cards.check في
-- permissions_json وحالة الموزّع active — انظر routes/customer_portals.py.
ALTER TABLE distributors ADD COLUMN portal_password_hash TEXT;
