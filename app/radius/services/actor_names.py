"""اسم عرضٍ مقروء لحقل «الفاعل» الخام (F08-L).

الفاعل المخزَّن قد يكون:
* ``api-token:N`` — طلب من التطبيق/الربط بمفتاح API. **أيّ** مفتاحٍ معروفٍ
  منشئه (created_by، أو ``login:<username>:…`` لمفتاح دخول التطبيق) يُعرَض
  «تطبيق — <اسم المدير>» (قاعدة موحّدة: الويب، الـAPI ``*_name``، الإشعارات،
  السجلّ). مفتاحٌ بلا منشئ معروف ⇒ «مفتاح: <اسم المفتاح>» ثمّ «مفتاح ربط #N».
* رقم مدير (``12``) → اسمه الكامل.
* ``system`` / ``system:<job>`` → «النظام» / «النظام: <المهمّة>».
* ``ui`` → «عملية واجهة (تلقائي)».
* ``unknown`` / فارغ → «غير معروف».
* غير ذلك (اسم دخول مدير) → الاسم الكامل إن وُجد، وإلّا كما هو.

الحلّ يُخزَّن لكل طلب (flask.g) فلا تتكرّر الاستعلامات لصفوف جدولٍ طويل.
لا يرفع أبدًا — أيّ خطأ يُرجع النصّ الأصليّ.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

from typing import Any

_SYSTEM_JOBS_AR = {
    "backup-scheduler": N_("مجدول النسخ الاحتياطي"),
    "reconciler": N_("المطابقة التلقائيّة"),
    "worker": N_("العامل الخلفيّ"),
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
        return N_("تطبيق / مفتاح ربط")
    try:
        r = _db().execute("SELECT name, created_by FROM api_tokens WHERE id = ?",
                          (int(token_id),)).fetchone()
    except Exception:  # noqa: BLE001
        r = None
    if not r:
        return _tr('مفتاح ربط #%(token_id)s', token_id=token_id)
    name = str(r["name"] or "").strip()
    who = _admin_name_by_id(int(r["created_by"] or 0)) if r["created_by"] else ""
    if name.startswith("login:"):
        # مفتاح جلسة التطبيق: login:<username>:<stamp>
        if not who:
            parts = name.split(":")
            who = parts[1] if len(parts) > 1 else ""
        return _tr('تطبيق — %(who)s', who=who) if who else N_("تطبيق")
    if who:
        # fix3 integration: مفتاح ربطٍ أنشأه مدير ⇒ اسم المدير (لا اسم المفتاح).
        return _tr('تطبيق — %(who)s', who=who)
    if name:
        return _tr('مفتاح: %(name)s', name=name)
    return _tr('مفتاح ربط #%(token_id)s', token_id=token_id)


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
            out = N_("غير معروف")
        elif low == "system":
            out = N_("النظام")
        elif low == "ui":
            out = N_("عملية واجهة (تلقائي)")
        elif low.startswith("system:"):
            job = raw.split(":", 1)[1].strip()
            out = _tr("النظام: ") + (_SYSTEM_JOBS_AR.get(job) or job.replace("-", " ").replace("_", " "))
        elif low.startswith("api-token"):
            rest = raw[len("api-token"):]
            tail = rest[1:].strip() if rest[:1] in (":", "-") else ""
            out = _token_label(tail) if tail and tail.lower() != "env" else N_("تطبيق / مفتاح ربط")
        elif raw.isdigit():
            out = _admin_name_by_id(int(raw)) or _tr('مدير #%(raw)s', raw=raw)
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


# ── API: اسم العرض بجانب الفاعل الخام (fix3 integration) ─────────────────────
# كلّ ردّ ``ok()`` في /api يمرّ هنا: لكلّ مفتاح فاعلٍ خام (actor/created_by/…)
# يُضاف ``<المفتاح>_name`` بالاسم المقروء (القيمة الخام تبقى كما هي — التطبيق
# قد يرشّح بها)، والنصوص الحرّة (title/body/notes…) التي تحمل «api-token:N»
# تُستبدل بـ«تطبيق — <المدير>». الحلّ مخزَّن لكل طلب فلا يتكرّر الاستعلام.
API_ACTOR_KEYS = frozenset({
    "actor", "created_by", "performed_by", "updated_by", "deleted_by",
    "approved_by", "requested_by", "settled_by", "voided_by", "reversed_by",
    "archived_by", "restored_by", "revoked_by",
})
API_TEXT_KEYS = frozenset({
    "title", "body", "message", "description", "note", "notes", "summary",
    "text", "detail", "details_text", "label", "actor_label",
})
_MAX_DEPTH = 8


def _actor_like(v: Any) -> bool:
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, int):
        return v > 0
    return isinstance(v, str) and bool(v.strip())


def enrich_api_payload(data: Any, _depth: int = 0) -> Any:
    """يُضيف ``actor_name`` (و``created_by_name``…) في مكانه ويعيد ``data``.
    لا يرفع أبدًا."""
    if _depth > _MAX_DEPTH:
        return data
    try:
        if isinstance(data, dict):
            for k in list(data.keys()):
                v = data[k]
                if isinstance(v, (dict, list)):
                    enrich_api_payload(v, _depth + 1)
                elif k in API_ACTOR_KEYS and _actor_like(v):
                    nk = f"{k}_name"
                    if nk not in data:
                        data[nk] = actor_display(v)
                elif (k in API_TEXT_KEYS and isinstance(v, str)
                        and "api-token" in v):
                    data[k] = humanize_actor_refs(v)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    enrich_api_payload(item, _depth + 1)
    except Exception:  # noqa: BLE001 — العرض لا يكسر الردّ
        pass
    return data


__all__ = ["API_ACTOR_KEYS", "API_TEXT_KEYS", "actor_display",
           "enrich_api_payload", "humanize_actor_refs"]
