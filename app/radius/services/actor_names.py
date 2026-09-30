"""اسم عرضٍ مقروء لحقل «الفاعل» الخام (F08-L).

الفاعل المخزَّن قد يكون:
* ``api-token:N`` — طلب من التطبيق/الربط بمفتاح API. مفاتيح دخول التطبيق
  (اسمها ``login:<username>:…``) تُعرَض «تطبيق — <اسم المدير>» (المدير الذي
  أنشأ المفتاح، created_by)، وبقيّة المفاتيح «مفتاح: <اسم المفتاح>».
* رقم مدير (``12``) → اسمه الكامل.
* ``system`` / ``system:<job>`` → «النظام» / «النظام: <المهمّة>».
* ``ui`` → «عملية واجهة (تلقائي)».
* ``unknown`` / فارغ → «غير معروف».
* غير ذلك (اسم دخول مدير) → الاسم الكامل إن وُجد، وإلّا كما هو.

الحلّ يُخزَّن لكل طلب (flask.g) فلا تتكرّر الاستعلامات لصفوف جدولٍ طويل.
لا يرفع أبدًا — أيّ خطأ يُرجع النصّ الأصليّ.
"""
from __future__ import annotations

from typing import Any

_SYSTEM_JOBS_AR = {
    "backup-scheduler": "مجدول النسخ الاحتياطي",
    "reconciler": "المطابقة التلقائيّة",
    "worker": "العامل الخلفيّ",
}


def _cache() -> dict:
    try:
        from flask import g
        c = getattr(g, "_actor_names_cache", None)
        if c is None:
            c = {}
            g._actor_names_cache = c
        return c
    except Exception:  # noqa: BLE001 — خارج سياق طلب
        return {}


def _db():
    from ..db.connection import db
    return db()


def _admin_name_by_id(aid: int) -> str:
    try:
        r = _db().execute("SELECT full_name, username FROM admins WHERE id = ?",
                          (int(aid),)).fetchone()
        if r:
            return (r["full_name"] or r["username"] or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _token_label(token_id: str) -> str:
    if not token_id.isdigit():
        return "تطبيق / مفتاح ربط"
    try:
        r = _db().execute("SELECT name, created_by FROM api_tokens WHERE id = ?",
                          (int(token_id),)).fetchone()
    except Exception:  # noqa: BLE001
        r = None
    if not r:
        return f"مفتاح ربط #{token_id}"
    name = str(r["name"] or "").strip()
    who = _admin_name_by_id(int(r["created_by"] or 0)) if r["created_by"] else ""
    if name.startswith("login:"):
        # مفتاح جلسة التطبيق: login:<username>:<stamp>
        if not who:
            parts = name.split(":")
            who = parts[1] if len(parts) > 1 else ""
        return f"تطبيق — {who}" if who else "تطبيق"
    if name:
        return f"مفتاح: {name}"
    return f"تطبيق — {who}" if who else f"مفتاح ربط #{token_id}"


def actor_display(actor: Any) -> str:
    """اسم عرضٍ عربيّ مقروء لقيمة «الفاعل» الخام."""
    raw = str(actor if actor is not None else "").strip()
    cache = _cache()
    if raw in cache:
        return cache[raw]
    out = raw
    try:
        low = raw.lower()
        if not raw or low in ("unknown", "none", "null", "-", "—"):
            out = "غير معروف"
        elif low == "system":
            out = "النظام"
        elif low == "ui":
            out = "عملية واجهة (تلقائي)"
        elif low.startswith("system:"):
            job = raw.split(":", 1)[1].strip()
            out = "النظام: " + (_SYSTEM_JOBS_AR.get(job) or job.replace("-", " ").replace("_", " "))
        elif low.startswith("api-token"):
            rest = raw[len("api-token"):]
            tail = rest[1:].strip() if rest[:1] in (":", "-") else ""
            out = _token_label(tail) if tail and tail.lower() != "env" else "تطبيق / مفتاح ربط"
        elif raw.isdigit():
            out = _admin_name_by_id(int(raw)) or f"مدير #{raw}"
        else:
            try:
                r = _db().execute("SELECT full_name FROM admins WHERE username = ?",
                                  (raw,)).fetchone()
                if r and (r["full_name"] or "").strip():
                    out = r["full_name"].strip()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — العرض لا يكسر شيئًا
        out = raw
    cache[raw] = out
    return out


_TOKEN_REF = None


def humanize_actor_refs(text: Any) -> str:
    """يستبدل كلّ «api-token:N» داخل نصٍّ حرّ (جسم إشعارٍ قديم كُتب قبل
    الإصلاح: «بواسطة: api-token:78») باسم العرض المقروء."""
    global _TOKEN_REF
    s = "" if text is None else str(text)
    if "api-token" not in s:
        return s
    import re
    if _TOKEN_REF is None:
        _TOKEN_REF = re.compile(r"api-token(?::|-)?(\d+|env)?")
    try:
        return _TOKEN_REF.sub(lambda m: actor_display(m.group(0)), s)
    except Exception:  # noqa: BLE001
        return s


__all__ = ["actor_display", "humanize_actor_refs"]
