"""MT22 — توجيه المسار باسم الشبكة (path-based multi-tenancy).

كل جهة تُوصَل تحت بادئة اسمها في المسار:
    panel.hoberadius.com/<slug>/admin/radius/...   ← لوحة مدير الجهة
    panel.hoberadius.com/<slug>/portal/...          ← بوابة مشتركيها + سوق البطاقات

الآلية (بلا لمس أيّ url_for في التطبيق):
  طبقة WSGI تكشف أوّل مقطع من المسار؛ إن كان slug جهة معروفة:
    • تُزيله من PATH_INFO وتُضيفه إلى **SCRIPT_NAME**، فيُصبح التطبيق كأنه
      «مُركّب» تحت /<slug> — و Werkzeug يُضيف SCRIPT_NAME تلقائيًّا لكل
      رابط يولّده url_for، فتبقى بادئة الشبكة في كلّ الصفحات دون تعديل كود.
    • تضع علامة environ يقرؤها tenant_resolver لتحديد الجهة (authoritative).
المسارات المحجوزة (admin/portal/static/api...) لا تُعامَل كأسماء شبكات.
"""
from __future__ import annotations

import sqlite3
import time

# أوّل مقاطع لا تُعدّ أسماء شبكات إطلاقًا (مسارات التطبيق العليا + المزوّد).
_RESERVED = {
    "admin", "portal", "p", "static", "api", "health", "healthz",
    "favicon.ico", "robots.txt", ".well-known", "_license", "set-locale",
}

_ENV_KEY = "hoberadius.tenant_slug"

# كاش أسماء الشبكات (قراءة مباشرة من DB — الطبقة تعمل قبل سياق Flask).
_CACHE: dict = {"at": 0.0, "slugs": set()}
_TTL = 30.0


def _valid_slugs() -> set:
    now = time.time()
    if _CACHE["slugs"] and (now - _CACHE["at"]) < _TTL:
        return _CACHE["slugs"]
    try:
        from ..db.connection import _resolve_db_path
        con = sqlite3.connect(_resolve_db_path(), timeout=2)
        try:
            rows = con.execute(
                "SELECT slug FROM tenants WHERE COALESCE(status,'') != 'closed' "
                "AND slug != 'default'").fetchall()
        finally:
            con.close()
        _CACHE["slugs"] = {r[0] for r in rows if r[0]}
        _CACHE["at"] = now
    except Exception:  # noqa: BLE001 — أي خطأ = لا أسماء (تمرير عاديّ)
        pass
    return _CACHE["slugs"]


def invalidate_slug_cache() -> None:
    """يُستدعى عند إنشاء/حذف جهة كي يظهر رابطها فورًا."""
    _CACHE["at"] = 0.0
    _CACHE["slugs"] = set()


class TenantPathMiddleware:
    """يحوّل /<slug>/rest → SCRIPT_NAME=/<slug>, PATH_INFO=/rest + علامة الجهة."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or ""
        seg, _, rest = path.lstrip("/").partition("/")
        if seg and seg not in _RESERVED and seg in _valid_slugs():
            environ[_ENV_KEY] = seg
            environ["SCRIPT_NAME"] = (environ.get("SCRIPT_NAME", "") or "") + "/" + seg
            environ["PATH_INFO"] = "/" + rest
        return self.app(environ, start_response)


def slug_from_environ(environ) -> str:
    return environ.get(_ENV_KEY, "") if environ else ""
