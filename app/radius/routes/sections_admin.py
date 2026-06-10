"""واجهة الإدارة لأعلام الأقسام (إخفاء/تعطيل قسم بكامله).

  • GET  /sections          → قائمة الأقسام + علاماتها الحالية + أزرار قلب.
  • POST /sections          → حفظ علامات قسم واحد (hidden/disabled).
  • POST /sections/reset    → إعادة قسم واحد إلى علاماته الافتراضية.

كل العمليات مقصورة على المدير الرئيسي/السوبر — مفروضة في `_PERM_GUARDED`
في `blueprint.py` (`sections_admin_page`/`sections_admin_save`/`sections_admin_reset`
كلّها → `__super__`). إن دخل غير السوبر مباشرةً يصله 403 من نفس الحارس
المركزي بلا منطق مكرّر هنا.

الحارس الجديد لأعلام القسم نفسه يُطبَّق على المسار قبل بلوغ المعالج، لذا
عند تعطيل قسم «sections_admin» لا يُمكن الوصول لهذه الصفحة إلا للسوبر —
وهو السلوك المطلوب: المالك دائماً يستعيد التحكّم.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..auth.section_flags import (
    list_sections, reset_to_defaults, set_flags,
)


def _current_admin_id_safe() -> int:
    try:
        from ..auth.session_helpers import current_admin_id
        aid = current_admin_id()
        return int(aid) if aid else 0
    except Exception:  # noqa: BLE001
        return 0


def sections_admin_page():
    return render_template(
        "radius/sections_admin.html",
        sections=list_sections(),
    )


def sections_admin_save():
    name = (request.form.get("section") or "").strip()
    hidden_raw   = (request.form.get("hidden") or "").strip().lower()
    disabled_raw = (request.form.get("disabled") or "").strip().lower()
    # «1»/«on»/«true» → True، أي شيء آخر (بما في ذلك غياب الحقل) → False.
    # هذه ليست checkboxات HTML قياسية بل toggle مكتوب بقيمة صريحة من JS
    # حتى لا تَعتمد القائمة على تفاصيل الـbrowser في إرسال checkboxات
    # غير المُحدَّدة.
    hidden   = hidden_raw   in ("1", "true", "on", "yes")
    disabled = disabled_raw in ("1", "true", "on", "yes")
    try:
        set_flags(name, hidden=hidden, disabled=disabled, by=_current_admin_id_safe())
        flash("تم تحديث حالة القسم.", "success")
    except KeyError:
        flash("قسم غير معروف.", "error")
    except Exception as exc:  # noqa: BLE001
        flash(f"تعذّر التحديث: {exc}", "error")
    return redirect(url_for("radius.sections_admin_page"))


def sections_admin_reset():
    name = (request.form.get("section") or "").strip()
    try:
        reset_to_defaults(name, by=_current_admin_id_safe())
        flash("تم إرجاع القسم إلى علاماته الافتراضية.", "success")
    except KeyError:
        flash("قسم غير معروف.", "error")
    except Exception as exc:  # noqa: BLE001
        flash(f"تعذّر التحديث: {exc}", "error")
    return redirect(url_for("radius.sections_admin_page"))


def register_sections_admin_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/sections",        "sections_admin_page",  sections_admin_page,  methods=["GET"])
    bp.add_url_rule("/sections/save",   "sections_admin_save",  sections_admin_save,  methods=["POST"])
    bp.add_url_rule("/sections/reset",  "sections_admin_reset", sections_admin_reset, methods=["POST"])
