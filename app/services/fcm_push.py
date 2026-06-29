"""مُرسِل الدفع الخادمي عبر Firebase Cloud Messaging (FCM).

يُكمِّل سطوح الإشعار الأخرى (اللوحة + مركز الإشعارات داخل التطبيق +
إشعارات ويندوز): حين يُكتب إشعار في الجرس (panel_notifications) يُدفَع
أيضًا إلى أجهزة المستأجر الجوّالة عبر FCM.

أمان الاعتماد (حرج)
-------------------
ملفّ حساب الخدمة من Firebase **سرّ** (يحوي مفتاحًا خاصًّا). لا يُوضع في
المستودع أبدًا. يُحمَّل وقت التشغيل من مسار على الخادم عبر متغيّر بيئة:

    FIREBASE_CREDENTIALS_PATH=/etc/hoberadius/firebase-adminsdk.json
    # أو متغيّر Google القياسي:
    GOOGLE_APPLICATION_CREDENTIALS=/etc/hoberadius/firebase-adminsdk.json

السلوك عند غياب الاعتماد/الحزمة
-------------------------------
كل شيء خلف فحص «الاعتماد موجود». إن غاب المسار، أو غاب الملفّ، أو لم
تُثبَّت حزمة ``firebase-admin``، أو فشلت التهيئة لأي سبب → المُرسِل
**يُعطَّل بهدوء** (no-op): لا انهيار، ولا إغراق بالأخطاء (يُسجَّل سطر
واحد). تبقى اللوحة + مركز الإشعارات + إشعارات ويندوز تعمل بلا دفع.

الاستيراد لـ ``firebase_admin`` كسول (داخل الدوال) كي يُستورَد هذا
الملفّ ويُختبَر حتى لو لم تكن الحزمة مثبَّتة.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Mapping, Optional, Sequence

_LOG = logging.getLogger(__name__)

# ترتيب الأولوية لمتغيّرات بيئة مسار الاعتماد.
_CREDENTIAL_ENV_VARS = ("FIREBASE_CREDENTIALS_PATH", "GOOGLE_APPLICATION_CREDENTIALS")

# تشغيل/إيقاف يدويّ اختياري (لا يُفعّل الدفع بل يُتيح إيقافه رغم وجود
# الاعتماد). الافتراضي «مُفعَّل متى وُجد الاعتماد».
_DISABLE_ENV_VAR = "HOBERADIUS_FCM_DISABLED"

# حالة التهيئة المُخبَّأة (مرّة واحدة لكل عملية). محميّة بقفل كي لا
# يُهيّأ تطبيقان عند أوّل إرسال متزامن.
_lock = threading.Lock()
_init_done = False
_enabled = False
_app = None  # firebase_admin.App


def credentials_path() -> str:
    """أوّل مسار اعتماد صالح، أو '' إن لم يوجد ملفّ.

    الترتيب: الاعتماد المرفوع من اللوحة (ملفّ instance/ ← نسخة قاعدة البيانات)
    أوّلًا، ثم متغيّرات البيئة (توافق رجعيّ). فبمجرّد الرفع من الواجهة يَعمل
    الدفع دون أيّ خطوة خادم."""
    try:
        from app.services import fcm_credentials
        path = fcm_credentials.resolve_credential_path()
        if path and os.path.isfile(path):
            return path
    except Exception:  # noqa: BLE001 — لا يَكسر الارتداد للبيئة أبدًا
        _LOG.debug("fcm_credentials resolve failed", exc_info=True)
    for var in _CREDENTIAL_ENV_VARS:
        raw = (os.environ.get(var) or "").strip()
        if raw and os.path.isfile(raw):
            return raw
    return ""


def library_available() -> bool:
    """هل حزمة ``firebase-admin`` قابلة للاستيراد على الخادم؟

    تُفصَل عن وجود الاعتماد كي تُظهر اللوحة رسالة دقيقة («المكتبة غير مثبّتة»
    مقابل «لم يُرفَع اعتماد»). آمنة الاستدعاء دائمًا."""
    try:
        import firebase_admin  # noqa: F401, WPS433 — فحص توفّر فقط
        return True
    except Exception:  # noqa: BLE001
        return False


def _manually_disabled() -> bool:
    return (os.environ.get(_DISABLE_ENV_VAR) or "").strip().lower() in (
        "1", "true", "yes", "on")


def reset_for_test() -> None:
    """يُعيد ضبط الحالة المُخبَّأة — للاختبارات فقط (تَغيّر متغيّرات البيئة)."""
    global _init_done, _enabled, _app
    with _lock:
        _init_done = False
        _enabled = False
        _app = None


def _ensure_init() -> bool:
    """يُهيّئ تطبيق Firebase Admin مرّة واحدة (كسول، مُخبَّأ).

    يُرجع True إن كان المُرسِل مُفعَّلًا (حزمة + اعتماد + تهيئة ناجحة).
    أيّ فشل → يُعطَّل بهدوء ويُخبَّأ القرار (سطر تسجيل واحد)."""
    global _init_done, _enabled, _app
    if _init_done:
        return _enabled
    with _lock:
        if _init_done:
            return _enabled
        _init_done = True
        _enabled = False

        if _manually_disabled():
            _LOG.info("FCM push disabled via %s", _DISABLE_ENV_VAR)
            return False

        path = credentials_path()
        if not path:
            _LOG.info(
                "FCM push disabled: no credential file at "
                "FIREBASE_CREDENTIALS_PATH / GOOGLE_APPLICATION_CREDENTIALS")
            return False

        try:
            import firebase_admin  # noqa: WPS433 — كسول عمدًا
            from firebase_admin import credentials
        except Exception as exc:  # noqa: BLE001 — الحزمة غير مثبَّتة ⇒ تعطيل
            _LOG.info("FCM push disabled: firebase-admin not importable (%s)", exc)
            return False

        try:
            cred = credentials.Certificate(path)
            # اسم تطبيق مخصّص كي لا يتصادم مع تهيئة افتراضية أخرى.
            try:
                _app = firebase_admin.get_app("hoberadius-fcm")
            except ValueError:
                _app = firebase_admin.initialize_app(cred, name="hoberadius-fcm")
            _enabled = True
            _LOG.info("FCM push enabled (credential: %s)", path)
        except Exception as exc:  # noqa: BLE001 — تهيئة فاشلة ⇒ تعطيل بهدوء
            _LOG.warning("FCM push disabled: init failed (%s)", exc)
            _enabled = False
        return _enabled


def is_enabled() -> bool:
    """هل المُرسِل مُفعَّل (اعتماد موجود + تهيئة ناجحة)؟ آمن الاستدعاء دائمًا."""
    try:
        return _ensure_init()
    except Exception:  # noqa: BLE001 — لا يُفترض، لكن لا نكسر المُتّصِل أبدًا
        return False


def _coerce_data(data: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """حُمولة FCM data يجب أن تكون نصوصًا فقط — نُحوّل القيم لنصوص."""
    out: dict[str, str] = {}
    for k, v in (data or {}).items():
        if v is None:
            continue
        out[str(k)] = str(v)
    return out


def send_to_tokens(tokens: Sequence[str], title: str, body: str,
                   data: Optional[Mapping[str, Any]] = None) -> dict:
    """يُرسِل إشعار FCM multicast إلى الرموز المُعطاة.

    يُرجع dict تشخيصيًّا دائمًا (لا يَرمي أبدًا):
      {ok, sent, failed, invalid_tokens, disabled?, reason?}

    invalid_tokens: الرموز التي أبلغ FCM أنها غير مُسجَّلة/غير صالحة —
    على المُتّصِل تقليمها من المخزن. عند تعطيل المُرسِل أو غياب الرموز
    يُرجَع no-op بلا أيّ نداء شبكي."""
    toks = [str(t).strip() for t in (tokens or []) if str(t).strip()]
    if not toks:
        return {"ok": False, "disabled": False, "reason": "no_tokens",
                "sent": 0, "failed": 0, "invalid_tokens": []}
    if not is_enabled():
        return {"ok": False, "disabled": True, "reason": "fcm_disabled",
                "sent": 0, "failed": 0, "invalid_tokens": []}

    try:
        from firebase_admin import messaging  # noqa: WPS433 — كسول
    except Exception as exc:  # noqa: BLE001
        _LOG.info("FCM send skipped: messaging import failed (%s)", exc)
        return {"ok": False, "disabled": True, "reason": "import_failed",
                "sent": 0, "failed": 0, "invalid_tokens": []}

    payload = _coerce_data(data)
    try:
        message = messaging.MulticastMessage(
            tokens=toks,
            notification=messaging.Notification(title=title or "", body=body or ""),
            data=payload,
        )
        # send_each_for_multicast هو البديل الحديث لـ send_multicast
        # (المُهمَل في إصدارات أحدث). نُفضّله ونَرتدّ عند غيابه.
        sender = (getattr(messaging, "send_each_for_multicast", None)
                  or getattr(messaging, "send_multicast", None))
        if sender is None:  # pragma: no cover — توافق دفاعيّ
            return {"ok": False, "disabled": True, "reason": "no_sender",
                    "sent": 0, "failed": 0, "invalid_tokens": []}
        resp = sender(message, app=_app)
    except Exception as exc:  # noqa: BLE001 — فشل شبكي/خادمي ⇒ لا نكسر المُتّصِل
        _LOG.warning("FCM send failed (%s)", exc)
        return {"ok": False, "disabled": False, "reason": "send_error",
                "sent": 0, "failed": len(toks), "invalid_tokens": []}

    invalid = _collect_invalid(resp, toks)
    success = int(getattr(resp, "success_count", 0) or 0)
    failure = int(getattr(resp, "failure_count", 0) or 0)
    return {"ok": True, "disabled": False, "reason": "sent",
            "sent": success, "failed": failure, "invalid_tokens": invalid}


def _collect_invalid(resp, tokens: Sequence[str]) -> list[str]:
    """يَستخرج الرموز التي أبلغ FCM أنها غير مُسجَّلة/غير صالحة من ردّ
    الـmulticast، كي يُقلّمها المُتّصِل."""
    invalid: list[str] = []
    responses = list(getattr(resp, "responses", None) or [])
    for idx, r in enumerate(responses):
        if getattr(r, "success", False):
            continue
        if idx >= len(tokens):
            continue
        exc = getattr(r, "exception", None)
        if _is_invalid_token_error(exc):
            invalid.append(tokens[idx])
    return invalid


def _is_invalid_token_error(exc) -> bool:
    """هل الاستثناء يَدلّ على رمز يجب حذفه (غير مُسجَّل/غير صالح/عدم تطابق)؟

    يُطابِق بالنوع إن أمكن، ويَرتدّ لمطابقة الاسم نصًّا كي لا يَعتمد على
    إصدار firebase-admin بعينه."""
    if exc is None:
        return False
    try:
        from firebase_admin import messaging  # noqa: WPS433
        invalid_types = tuple(
            t for t in (
                getattr(messaging, "UnregisteredError", None),
                getattr(messaging, "SenderIdMismatchError", None),
            ) if t is not None
        )
        if invalid_types and isinstance(exc, invalid_types):
            return True
        try:
            from firebase_admin import exceptions as fb_exceptions
            if isinstance(exc, getattr(fb_exceptions, "InvalidArgumentError", ())):
                return True
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    name = type(exc).__name__.lower()
    return ("unregistered" in name or "invalidargument" in name
            or "senderidmismatch" in name)


__all__ = [
    "credentials_path",
    "library_available",
    "is_enabled",
    "send_to_tokens",
    "reset_for_test",
]
