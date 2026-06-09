"""store_key — مفتاح تطبيق متجر المايكروتيك (App / Client Key).

المشكلة: نقاط /api/v1/store/* مفتوحة على أي أصل (CORS=*) لأن صفحة
المتجر تعمل من IP الراوتر المجهول مسبقًا — فأي طرف يعرف عنوان
السيرفر يستطيع نظريًا استدعاء login/packages مباشرة (تخمين، إغراق…).
توكن الجلسة (store_token) يحمي العمليات الخاصة بعد الدخول فقط.

الحل: مفتاح سرّي واحد لكل مستأجر يُحقن في store.html المنشور
(مكان {{STORE_KEY}}) وترسله الصفحة في ترويسة X-Store-Key مع كل نداء.
الـ API يرفض (403) أي طلب لا يحمل المفتاح الصحيح — فقط المتجر
المنشور (الذي يحمله) يتصل، لا أداة خارجية عشوائية.

حدوده (وثِّقها للمستخدم): المفتاح مرئي في مصدر صفحة على الراوتر —
فهو يصدّ الاستدعاء العشوائي/الآلي لا متطفّلًا يفتح الصفحة ويقرأ
مصدرها. الحماية الحقيقية للعمليات الحساسة تبقى: store_token بعد
تسجيل الدخول + جدار walled-garden الذي لا يسمح بالوصول للسيرفر إلا
من داخل شبكة الهوت سبوت.

التخزين: إعداد المستأجر network.store_api_key (نفس آلية بقية
الإعدادات — tenant_settings). يُولَّد عند أول نشر للمتجر، ويُدوَّر
من صفحة الإعدادات (تدويره يتطلب إعادة نشر store.html).
"""
from __future__ import annotations

import hmac
import re
import secrets

from ..db.repos import tenants_repo

# اسم الإعداد + ترويسة النقل + طول المفتاح (token_urlsafe(24) ≈ 32 حرفًا).
STORE_KEY_SETTING = "network.store_api_key"
STORE_KEY_HEADER = "X-Store-Key"
_KEY_NBYTES = 24

# المفتاح من token_urlsafe = [A-Za-z0-9_-] فقط — آمن للحقن في سلسلة
# JS بلا أي هروب. نطبّق هذا النمط أيضًا عند التعقيم قبل الحقن.
_KEY_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_\-]")


def _generate() -> str:
    return secrets.token_urlsafe(_KEY_NBYTES)


def sanitize_key(value: str) -> str:
    """يزيل أي محرف خارج [A-Za-z0-9_-] — وقاية عند الحقن في store.html
    حتى لو ضُبطت قيمة يدوية غريبة في الإعداد."""
    return _KEY_SANITIZE_RE.sub("", str(value or ""))


def get_store_key(tenant_id: int = 1) -> str:
    """المفتاح المخزَّن للمستأجر (أو "" إن لم يُولَّد بعد). قراءة فقط."""
    return (tenants_repo.get_setting(
        int(tenant_id), STORE_KEY_SETTING, "") or "").strip()


def get_or_create_store_key(tenant_id: int = 1, *, by: int = 0) -> str:
    """يعيد المفتاح المخزَّن أو يولّد واحدًا ويحفظه — يُستدعى من مسار
    النشر/الحزمة فيصبح المفتاح موجودًا (والفرض فعّالًا) لحظة نشر المتجر."""
    key = get_store_key(tenant_id)
    if not key:
        key = _generate()
        tenants_repo.set_setting(int(tenant_id), STORE_KEY_SETTING, key, by=by)
    return key


def rotate_store_key(tenant_id: int = 1, *, by: int = 0) -> str:
    """يولّد مفتاحًا جديدًا يحلّ محل القديم — القديم يتوقف فورًا،
    فيجب إعادة نشر store.html بالمفتاح الجديد."""
    key = _generate()
    tenants_repo.set_setting(int(tenant_id), STORE_KEY_SETTING, key, by=by)
    return key


def store_key_required(tenant_id: int = 1) -> bool:
    """هل الفرض فعّال؟ (يصبح كذلك بمجرد وجود مفتاح مخزَّن)."""
    return bool(get_store_key(tenant_id))


def verify_store_key(provided: str, tenant_id: int = 1) -> bool:
    """يقارن المفتاح المُرسَل بالمخزَّن (مقارنة ثابتة الزمن).

    عندما لا يوجد مفتاح مخزَّن بعد (تثبيت قديم/قبل أول نشر) نعيد True
    (لا فرض) حفاظًا على التوافق — الفرض يبدأ تلقائيًا فور توليد مفتاح.
    """
    expected = get_store_key(tenant_id)
    if not expected:
        return True
    return hmac.compare_digest(str(provided or ""), expected)


__all__ = [
    "STORE_KEY_SETTING",
    "STORE_KEY_HEADER",
    "sanitize_key",
    "get_store_key",
    "get_or_create_store_key",
    "rotate_store_key",
    "store_key_required",
    "verify_store_key",
]
