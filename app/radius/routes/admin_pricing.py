"""أسعار العروض للمدراء — صفحة الإدارة + حفظ/استعادة.

«بقسم الإدارة نعمل أسعار عروض مختلفة للمدراء حسب المدير والاتفاق»:
جدول المدراء مع ملخص أسعارهم الخاصة، ونافذة عائمة (fcw) لكل مدير
تعرض كل العروض مع حقل «السعر الخاص» (فارغ = السعر الافتراضي)،
مع «استعادة» لمدير واحد و«استعادة الكل».

الإنفاذ الفعلي للسعر في accounting.effective_subscriber_price —
هذه الشاشة إدارة بيانات فقط.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..db.repos import admins_repo, plans_repo
from ..services.admin_pricing import AdminPricingService


def register_admin_pricing_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/admins/pricing", "admin_pricing_page",
                    admin_pricing_page, methods=["GET"])
    bp.add_url_rule("/admins/pricing/<int:admin_id>/save", "admin_pricing_save",
                    admin_pricing_save, methods=["POST"])
    bp.add_url_rule("/admins/pricing/<int:admin_id>/reset", "admin_pricing_reset",
                    admin_pricing_reset, methods=["POST"])
    bp.add_url_rule("/admins/pricing/reset-all", "admin_pricing_reset_all",
                    admin_pricing_reset_all, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "system"


def admin_pricing_page():
    svc = AdminPricingService(tenant_id=_tid())
    admins = admins_repo.list_admins()
    plans = plans_repo.list_plans(_tid())
    overrides = svc.all_overrides()

    # فهرسة التجاوزات لكل مدير: admin_id → {plan_id: row} — تمرَّر للقالب
    # ليبني جدول الملخّص ونوافذ التعديل بلا استعلامات إضافية.
    plan_names = {int(p.id): (p.name or f"#{p.id}") for p in plans if p.id}
    by_admin: dict[int, dict[int, dict]] = {}
    for row in overrides:
        by_admin.setdefault(int(row["admin_id"]), {})[int(row["plan_id"])] = row

    return render_template(
        "radius/admin_pricing.html",
        admins=admins,
        plans=plans,
        plan_names=plan_names,
        by_admin=by_admin,
        overrides_count=len(overrides),
        last_update=svc.last_update(),
    )


def admin_pricing_save(admin_id: int):
    """حفظ نافذة «تعديل»: حقل price_<plan_id> لكل عرض —
    فارغ/0 يمسح التجاوز، وقيمة موجبة تثبّته."""
    svc = AdminPricingService(tenant_id=_tid())
    prices: dict[int, str] = {}
    for key, value in request.form.items():
        if not key.startswith("price_"):
            continue
        try:
            plan_id = int(key.split("_", 1)[1])
        except (TypeError, ValueError):
            continue
        prices[plan_id] = (value or "").strip()
    try:
        result = svc.set_prices_bulk(admin_id=admin_id, prices=prices, actor=_actor())
    except (ValueError, RadiusError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("radius.admin_pricing_page"))
    flash(
        f"تم الحفظ: {result['set']} سعرًا خاصًا مثبَّتًا"
        + (f"، و{result['cleared']} أُعيد للافتراضي" if result["cleared"] else "")
        + ".",
        "success",
    )
    return redirect(url_for("radius.admin_pricing_page"))


def admin_pricing_reset(admin_id: int):
    """«استعادة»: مسح كل الأسعار الخاصة لمدير واحد — يعود للأسعار الرسمية."""
    removed = AdminPricingService(tenant_id=_tid()).reset_admin(admin_id=admin_id)
    if removed:
        flash(f"تمت الاستعادة — أُزيل {removed} سعرًا خاصًا لهذا المدير.", "success")
    else:
        flash("لا أسعار خاصة لهذا المدير أصلًا.", "info")
    return redirect(url_for("radius.admin_pricing_page"))


def admin_pricing_reset_all():
    """«استعادة الكل»: مسح كل الأسعار الخاصة لجميع المدراء."""
    removed = AdminPricingService(tenant_id=_tid()).reset_all()
    if removed:
        flash(f"تمت استعادة الكل — أُزيل {removed} سعرًا خاصًا.", "success")
    else:
        flash("لا أسعار خاصة مسجَّلة أصلًا.", "info")
    return redirect(url_for("radius.admin_pricing_page"))
