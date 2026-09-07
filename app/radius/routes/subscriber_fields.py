"""«إدارة البيانات» — أيُّ حقولِ نموذجِ المشترك تظهر لهذه الشبكة.

مسارٌ واحدٌ يعرض السجلَّ ويحفظه: ‎/admin/radius/subscriber-fields‎.
المنطقُ كلُّه في ``services/subscriber_form_fields`` — هنا العرضُ والحفظ
والتسجيل في سجلّ التدقيق فقط.
"""
from __future__ import annotations

from flask import (
    Blueprint, flash, redirect, render_template, request, session, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import audit_repo
from ..services import subscriber_form_fields as sff


def register_subscriber_fields_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/subscriber-fields", "subscriber_fields",
                    subscriber_fields, methods=["GET", "POST"])


def _tid() -> int:
    return session.get("tenant_id") or DEFAULT_TENANT_ID


def subscriber_fields():
    tenant_id = _tid()
    if request.method == "POST":
        # 🔑 مربّعُ اختيارٍ غيرُ مؤشَّرٍ لا يُرسَل أصلًا في HTML، فالمُرسَلُ
        #    هو «المُشعَل» وما عداه مُطفأ. لهذا نمرّر القائمةَ كاملةً إلى
        #    الخدمة بدل تحديثِ ما وصل وحدَه — وإلّا لما أمكن إطفاءُ حقلٍ أبدًا.
        visible = set(request.form.getlist("visible"))
        changed = sff.set_visibility(
            tenant_id, visible, by=session.get("admin_id") or 0)
        if changed:
            audit_repo.record(
                tenant_id=tenant_id,
                actor=(session.get("admin_name")
                       or session.get("admin_user") or "anonymous"),
                action="subscriber_fields_update", target_type="settings",
                target_id="subscriber_form.fields",
                payload={"hidden": list(sff.hidden_keys(tenant_id))})
            flash("حُفظت إعداداتُ ظهور الحقول (%d تغييرًا). تسري فورًا على "
                  "نموذجَي إضافة وتعديل المشترك." % changed, "success")
        else:
            flash("لا تغييرات.", "info")
        return redirect(url_for("radius.subscriber_fields"))

    vis = sff.visibility(tenant_id)
    groups = []
    for g in sff.GROUPS:
        rows = [{**f, "on": vis.get(f["key"], True)} for f in g["fields"]]
        groups.append({**g, "rows": rows,
                       "on_count": sum(1 for r in rows if r["on"])})
    total = len(sff.ALL_FIELDS)
    shown = sum(1 for on in vis.values() if on)
    return render_template("radius/subscriber_fields.html",
                           groups=groups, total=total, shown=shown,
                           hidden=total - shown, core=sorted(sff.CORE))
