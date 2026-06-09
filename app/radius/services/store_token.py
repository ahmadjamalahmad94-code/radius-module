"""store_token — توكنات متجر المايكروتيك الموقّعة.

متجر البطاقات على الراوتر (store.html) صفحة ثابتة تعمل من ملفات
الهوت سبوت ولا تملك جلسة كوكيز مع سيرفر الراديوس (origin مختلف).
بعد تسجيل الدخول عبر POST /api/v1/store/login تحصل الصفحة على
توكن موقّع قصير العمر تخزّنه في sessionStorage وترسله مع كل طلب
في ترويسة Authorization: Bearer.

التوقيع عبر itsdangerous (تأتي مع Flask نفسها — لا تبعية جديدة):
  - URLSafeTimedSerializer بمفتاح التطبيق السري (FLASK_SECRET).
  - الحمولة: {card_user_id, tenant_id} فقط — لا بيانات حساسة.
  - الصلاحية تُفحص عند فك التوكن (max_age) — افتراضيًا 12 ساعة،
    قابلة للضبط عبر متغيّر البيئة HOBERADIUS_STORE_TOKEN_TTL
    (بالثواني).

لماذا ليس api_tokens العادية؟ تلك توكنات إدارة كاملة الصلاحيات
(admin:full) تُمنح للمشغّل — أما توكن المتجر فهو هوية «مستخدم
بطاقة» واحد محدود النطاق، يموت وحده بانتهاء صلاحيته ولا يُخزَّن
في قاعدة البيانات أصلًا.
"""
from __future__ import annotations

import os
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


# مساحة الملح (salt) تعزل توقيعات المتجر عن أي استخدام آخر لنفس
# المفتاح السري — توكن متجر لا يصلح أبدًا كتوقيع لشيء آخر.
_SALT = "hoberadius.store.card-user.v1"

# الصلاحية الافتراضية: 12 ساعة — جلسة يوم عمل واحدة على الراوتر.
_DEFAULT_TTL_SECONDS = 12 * 60 * 60


class StoreTokenError(ValueError):
    """توكن متجر غير صالح أو منتهي الصلاحية."""

    def __init__(self, code: str, message_ar: str) -> None:
        super().__init__(message_ar)
        self.code = code
        self.message_ar = message_ar


def token_ttl_seconds() -> int:
    """صلاحية التوكن بالثواني — من البيئة أو الافتراضي (12 ساعة)."""
    raw = (os.environ.get("HOBERADIUS_STORE_TOKEN_TTL") or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return _DEFAULT_TTL_SECONDS


def _secret_key() -> str:
    """مفتاح التوقيع — نفس مفتاح التطبيق السري.

    داخل سياق Flask نقرأ app.secret_key (المصدر الموثوق)، وخارجه
    (اختبارات الوحدات) نسقط إلى متغيّر البيئة FLASK_SECRET ثم
    الافتراضي التطويري — نفس سلسلة القيم في app/__init__.py.
    """
    try:
        from flask import current_app

        key = current_app.secret_key
        if key:
            return str(key)
    except RuntimeError:  # خارج سياق تطبيق (اختبارات)
        pass
    return os.environ.get("FLASK_SECRET", "dev-secret-change-me")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt=_SALT)


def issue_store_token(*, card_user_id: int, tenant_id: int = 1) -> str:
    """يصدر توكن متجر موقّعًا لمستخدم بطاقة محدد."""
    return _serializer().dumps(
        {"cu": int(card_user_id), "t": int(tenant_id or 1)}
    )


def verify_store_token(token: str) -> dict[str, Any]:
    """يفك التوكن ويتحقق من التوقيع والصلاحية.

    يعيد {card_user_id, tenant_id} أو يرفع StoreTokenError برمز
    آمن (token_expired / token_invalid) ورسالة عربية جاهزة للعرض.
    """
    raw = str(token or "").strip()
    if not raw:
        raise StoreTokenError("token_missing", "سجّل الدخول أولاً.")
    try:
        data = _serializer().loads(raw, max_age=token_ttl_seconds())
    except SignatureExpired:
        raise StoreTokenError(
            "token_expired", "انتهت الجلسة — سجّل الدخول من جديد."
        ) from None
    except BadSignature:
        raise StoreTokenError(
            "token_invalid", "جلسة غير صالحة — سجّل الدخول من جديد."
        ) from None
    if not isinstance(data, dict) or "cu" not in data:
        raise StoreTokenError(
            "token_invalid", "جلسة غير صالحة — سجّل الدخول من جديد."
        )
    return {
        "card_user_id": int(data.get("cu") or 0),
        "tenant_id": int(data.get("t") or 1),
    }


__all__ = [
    "StoreTokenError",
    "issue_store_token",
    "verify_store_token",
    "token_ttl_seconds",
]
