"""صفحة «إعدادات النظام والبيئة» — تحرير مفاتيح كانت تُضبط من الطرفية (env)
من واجهة الإدارة مباشرةً. القراءة في كل مكان تتبع: DB → env → الافتراضي.

مقصورة على المدير الرئيسي/السوبر (إعدادات على مستوى النسخة/النشر).
الأسرار تُخزَّن مشفّرة وتُعرض مُقنّعة (لا تغادر القيمة الخادم)."""
from __future__ import annotations

from flask import (abort, flash, redirect, render_template, request,
                   session, url_for)

from ..core import env_settings
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import audit_repo


def register_system_settings_routes(bp) -> None:
    bp.add_url_rule("/settings/system", "system_settings_page",
                    system_settings_page, methods=["GET", "POST"])


def _require_super() -> None:
    if not session.get("is_super_admin"):
        abort(403)


def system_settings_page():
    _require_super()
    admin_id = session.get("admin_id") or 0
    actor = session.get("admin_name") or session.get("admin_user") or "anonymous"

    if request.method == "POST":
        changed: list[str] = []
        for s in env_settings.REGISTRY:
            field = "set__" + s.key
            if s.secret:
                # سرّ: حقل فارغ = إبقاء القيمة الحالية؛ صندوق المسح = حذف التجاوز
                if request.form.get("clear__" + s.key):
                    env_settings.clear_value(s.key)
                    changed.append(s.key + " (مُسح)")
                    continue
                raw = (request.form.get(field) or "").strip()
                if raw:
                    env_settings.set_value(s.key, raw, by=admin_id)
                    changed.append(s.key)
            elif s.kind == "bool":
                val = "1" if request.form.get(field) else "0"
                env_settings.set_value(s.key, val, by=admin_id)
                changed.append(s.key)
            else:
                raw = (request.form.get(field) or "").strip()
                if raw == "":
                    env_settings.clear_value(s.key)   # فارغ = العودة للبيئة/الافتراضي
                else:
                    env_settings.set_value(s.key, raw, by=admin_id)
                changed.append(s.key)

        audit_repo.record(
            tenant_id=DEFAULT_TENANT_ID, actor=actor,
            action="system_settings_update", target_type="system_settings",
            target_id="env", payload={"changed": changed})
        flash("حُفظت إعدادات النظام. بعض المفاتيح (مثل نمط RADIUS) تأخذ مفعولها "
              "بعد إعادة تشغيل الخدمة.", "success")
        return redirect(url_for("radius.system_settings_page"))

    return render_template("radius/system_settings.html",
                           groups=env_settings.ui_groups())
