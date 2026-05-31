"""
Settings — إعدادات النظام لكل tenant (key/value).
"""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import audit_repo, tenants_repo


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


# مفاتيح معيارية نعرضها في الواجهة (key, label, default)
_SETTINGS_KEYS = [
    ("system.name",             "اسم النظام",            "HobeRadius"),
    ("branding.logo_url",       "رابط الشعار",         ""),
    ("branding.primary_color",  "اللون الأساسي",        "#2BAACC"),
    ("radius.default_country",   "الدولة (موقع النظام)", ""),
    ("billing.currency",        "العملة (JOD / ILS / USD / IQD / SAR / EGP / AED)", "JOD"),
    ("billing.timezone_offset", "فارق توقيت النظام بالساعات (مثال: 3 تعني +3)", "3"),
    ("billing.tax_pct",         "ضريبة %",              "0"),
    ("auth.allow_password_reset", "السماح بإعادة تعيين كلمة المرور", "1"),
    ("cards.default_username_length", "طول اسم البطاقة الافتراضي",   "8"),
    ("cards.default_password_length", "طول كلمة مرور البطاقة الافتراضي", "6"),
    ("quota.threshold_alerts",  "نِسَب تنبيه الكوتا (CSV)",  "80,95,100"),
    ("api.rate_limit_per_minute","حد الـ API (req/min)",  "60"),
    ("webhook.target_url",      "URL الـ webhook (اختياري)", ""),
    ("webhook.secret",          "السر الموقِّع",          ""),
    ("mikrotik.default_router_id","رقم MT الافتراضي",     ""),
    ("session.timeout_minutes", "مهلة جلسة الإدارة (دقيقة)", "60"),
    ("display.records_per_page","صفوف الصفحة الافتراضية", "20"),
    # عنوان الـ VPS العام — يُستخدم في معالج «اتصال عن بُعد» لبناء
    # روابط Winbox/SSH/WebFig/API من خارج الشبكة (المنافذ
    # 51000-51199 عبر nginx-stream). اتركه فارغاً للرجوع إلى env
    # var HOBERADIUS_PUBLIC_HOST أو عنوان WG داخلي.
    ("infra.public_host",       "عنوان VPS العام (لروابط الاتصال عن بُعد)", ""),
]


def register_settings_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/settings", "settings_page",
                    settings_page, methods=["GET", "POST"])


def settings_page():
    tenant_id = _tid()
    if request.method == "POST":
        actor = session.get("admin_name") or session.get("admin_user") or "anonymous"
        admin_id = session.get("admin_id") or 0
        changed: dict[str, str] = {}
        for key, _label, _default in _SETTINGS_KEYS:
            if key in request.form:
                val = request.form[key].strip()
                old = tenants_repo.get_setting(tenant_id, key, "")
                if val != old:
                    tenants_repo.set_setting(tenant_id, key, val, by=admin_id)
                    changed[key] = val
        if changed:
            audit_repo.record(tenant_id=tenant_id, actor=actor, action="settings_update",
                              target_type="settings", target_id=",".join(changed.keys()),
                              payload={"changed": list(changed.keys())})
            flash(f"تم حفظ {len(changed)} إعدادًا.", "success")
        else:
            flash("لا تغييرات.", "info")
        return redirect(url_for("radius.settings_page"))

    rows = []
    for key, label, default in _SETTINGS_KEYS:
        rows.append({
            "key": key, "label": label,
            "value": tenants_repo.get_setting(tenant_id, key, default),
        })
    return render_template("radius/settings_page.html", items=rows)
