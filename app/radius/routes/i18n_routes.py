"""مسار مبدّل اللغة — يحفظ اختيار المستخدم فورًا ويعيد التوجيه.

GET /admin/radius/set-locale?locale=en&next=/admin/radius/

السلوك:
    - يتحقق أن اللغة ضمن المدعومة، وإلا يتجاهل بصمت (لا يكسر شيئًا).
    - يحفظ الاختيار في الجلسة (يسري فورًا على كل الصفحات).
    - إن كان هناك مسؤول مسجَّل: يثبّت الاختيار في admins.locale + الجلسة
      حتى يبقى مع زياراته القادمة (best-effort — فشل الحفظ لا يكسر التبديل).
    - يعيد التوجيه إلى next (آمن: مسار داخلي فقط) أو إلى المرجع/الرئيسية.
"""
from __future__ import annotations

from flask import Blueprint, redirect, request, session, url_for

from ..i18n import SUPPORTED_LOCALES


def register_i18n_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/set-locale", "set_locale", set_locale, methods=["GET", "POST"])


def _safe_next(raw: str | None) -> str:
    """يقبل المسارات الداخلية فقط (تبدأ بـ '/' وليست '//') لمنع الـ open-redirect."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return ""


def set_locale():
    locale = (request.values.get("locale") or "").strip().lower()
    if locale in SUPPORTED_LOCALES:
        session["locale"] = locale
        # ثبّت الاختيار على حساب المسؤول إن كان مسجَّلًا — best-effort.
        admin_id = session.get("admin_id")
        if admin_id:
            session["admin_locale"] = locale
            try:
                from ..db.repos import admins_repo
                admins_repo.update_admin(int(admin_id), locale=locale)
            except Exception:  # noqa: BLE001 — الحفظ الدائم لا يكسر التبديل
                pass

    nxt = (
        _safe_next(request.values.get("next"))
        or _safe_next(request.referrer)
        or url_for("radius.dashboard")
    )
    return redirect(nxt)
