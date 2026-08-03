-- إبطال الجلسات: ختم يتغيّر مع كل تغيير لكلمة المرور.
-- الجلسة تحمل نسخة الختم لحظة الدخول؛ أي اختلاف = جلسة ميتة فورًا
-- (تغيير كلمة المرور يطرد كل الأجهزة المفتوحة على الحساب).
-- انظر routes/blueprint.py::_guard و auth/session_helpers.py.
ALTER TABLE admins ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
