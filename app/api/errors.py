"""صلابة الـ API على مستوى التطبيق.

1) لا صفحة HTML 500 تحت ``/api/``: أيّ استثناء لم تعالجه النقطة يعود بغلاف
   JSON الموحّد (``fail(...)``) ويُسجَّل بتتبّعه الكامل.
     • «database is locked/busy» → 503 ``server_busy`` «الخادم مشغول، أعد
       المحاولة» مع ``Retry-After`` (عابر — التطبيق يعيد المحاولة).
     • ``RadiusError`` غير الملتقط → حالته الخاصة (422 للقيم غير الصالحة…).
     • غير ذلك → 500 ``server_error`` برسالة عربية.
   مسارات الويب (غير ``/api/``) تبقى كما كانت (يُعاد رفع الاستثناء).

2) JSON لا يُخرج ``Infinity``/``NaN`` أبدًا (ليست JSON صالحًا؛ صفّ واحد فاسد
   كان يُسقط قائمة المشتركين في التطبيق) — تُستبدل بـ ``null``.

3) نهاية كل طلب: معاملة SQLite تُركت مفتوحة على اتصال الـ thread تُرجَع
   (وإلا حجزت قفل الكتابة لكل الطلبات التالية).
"""
from __future__ import annotations

import logging

from flask import Flask, request
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException

from ..radius.core.errors import RadiusError
from ..radius.core.numbers import json_dumps_safe
from .responses import fail

_LOG = logging.getLogger("app.api.errors")

SERVER_BUSY_MESSAGE = "الخادم مشغول الآن، أعد المحاولة بعد لحظات."
SERVER_ERROR_MESSAGE = "حدث خطأ غير متوقع في الخادم. أعد المحاولة، وإن تكرّر فأبلغ الدعم الفنّي."


class SafeJSONProvider(DefaultJSONProvider):
    """``jsonify``/``tojson`` بلا Infinity/NaN (تصير null)."""

    def dumps(self, obj, **kwargs):  # noqa: ANN001
        return json_dumps_safe(obj, super().dumps, **kwargs)


def _is_api_request() -> bool:
    try:
        return request.path.startswith("/api/")
    except RuntimeError:  # خارج سياق الطلب
        return False


def api_exception_response(exc: BaseException):
    """الردّ JSON لاستثناءٍ غير معالَج تحت ``/api/``."""
    from ..radius.db.connection import is_lock_error

    if is_lock_error(exc):
        _LOG.warning("API %s %s: database busy (%s)", request.method, request.path, exc)
        resp, status = fail("server_busy", SERVER_BUSY_MESSAGE, status=503,
                            details={"retryable": True})
        resp.headers["Retry-After"] = "2"
        return resp, status
    if isinstance(exc, RadiusError):
        status = int(getattr(exc, "http_status", 500) or 500)
        if status >= 500:
            _LOG.exception("API %s %s: %s", request.method, request.path, exc,
                           exc_info=exc)
        code = getattr(exc, "code", "radius_error") or "radius_error"
        if status in (400, 422) and code in ("radius_validation_error",):
            code = "validation_error"
        return fail(code, exc.message, status=status, details=exc.details or None)
    _LOG.error("unhandled API error %s %s", request.method, request.path, exc_info=exc)
    return fail("server_error", SERVER_ERROR_MESSAGE, status=500)


def install_api_error_handlers(app: Flask) -> None:
    app.json_provider_class = SafeJSONProvider
    app.json = SafeJSONProvider(app)

    @app.errorhandler(Exception)
    def _api_unhandled_exception(exc):  # noqa: ANN001
        if isinstance(exc, HTTPException):
            return exc  # السلوك الافتراضيّ (404/405/413 لها معالجاتها)
        if not _is_api_request():
            raise exc  # الويب: كما كان (صفحة 500 + التسجيل الافتراضيّ)
        return api_exception_response(exc)

    @app.teardown_request
    def _release_leaked_sqlite_transaction(_exc=None):  # noqa: ANN001
        try:
            from ..radius.db.connection import release_leaked_transaction
            release_leaked_transaction()
        except Exception:  # noqa: BLE001 — التنظيف لا يكسر الردّ
            _LOG.warning("release_leaked_transaction failed", exc_info=True)


__all__ = [
    "SERVER_BUSY_MESSAGE", "SERVER_ERROR_MESSAGE", "SafeJSONProvider",
    "api_exception_response", "install_api_error_handlers",
]
