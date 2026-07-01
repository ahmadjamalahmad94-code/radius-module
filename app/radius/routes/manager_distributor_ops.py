"""Manager/distributor operational profile routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.manager_distributor_ops import ManagerDistributorError, ManagerDistributorOpsService


def register_manager_distributor_ops_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/business-operators", "business_operators", business_operators, methods=["GET"])
    bp.add_url_rule("/business-operators/<entity_type>/<int:entity_id>", "business_operator_profile", business_operator_profile, methods=["GET"])
    bp.add_url_rule("/business-operators/<entity_type>/<int:entity_id>/policy", "business_operator_policy", business_operator_policy, methods=["POST"])
    bp.add_url_rule("/business-operators/<entity_type>/<int:entity_id>/recharge", "business_operator_recharge", business_operator_recharge, methods=["POST"])
    # F2: قوالب الصلاحيات — إنشاء/حذف + تطبيق على مدير (بضغطة).
    bp.add_url_rule("/business-operators/presets", "manager_presets_create", manager_presets_create, methods=["POST"])
    bp.add_url_rule("/business-operators/presets/<int:preset_id>/delete", "manager_presets_delete", manager_presets_delete, methods=["POST"])
    bp.add_url_rule("/business-operators/manager/<int:entity_id>/apply-preset", "business_operator_apply_preset", business_operator_apply_preset, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _service() -> ManagerDistributorOpsService:
    return ManagerDistributorOpsService(tenant_id=_tid())


def business_operators():
    service = _service()
    return render_template(
        "radius/business_operators.html",
        managers=service.list_scope(entity_type="manager"),
        distributors=service.list_scope(entity_type="distributor"),
    )


def business_operator_profile(entity_type: str, entity_id: int):
    try:
        profile = _service().profile(entity_type=entity_type, entity_id=entity_id)
    except ManagerDistributorError:
        return redirect(url_for("radius.business_operators"))
    # مصفوفة الأقسام (3 حالات) + التحكّم الحقليّ — للمدير فقط.
    section_catalog = []
    section_states = ()
    field_catalog = []
    action_catalog = []
    limits_catalog = []
    if entity_type == "manager":
        from ..services import manager_grants as _mg
        section_catalog = _mg.section_catalog(int(entity_id), tenant_id=_tid())
        section_states = _mg.SECTION_STATES
        field_catalog = _build_field_catalog(int(entity_id))
        action_catalog = _mg.action_catalog(int(entity_id), tenant_id=_tid())
        limits_catalog = _mg.limits_catalog(int(entity_id), tenant_id=_tid())
    presets = []
    if entity_type == "manager":
        from ..services import manager_presets as _p
        presets = _p.list_presets(tenant_id=_tid())
    return render_template(
        "radius/business_operator_profile.html",
        profile=profile,
        section_catalog=section_catalog,
        section_states=section_states,
        field_catalog=field_catalog,
        action_catalog=action_catalog,
        limits_catalog=limits_catalog,
        presets=presets,
    )


# تسميات الكيانات القابلة للتحكّم الحقليّ (عربيّة).
_ENTITY_LABELS = {"subscriber": "المشترك", "offer": "العرض", "batch": "الباقة (الحزمة)"}

# كيانات «مالك فقط» افتراضًا يَفتح المالك تعديلَها لمديرٍ صراحةً (منح فعل
# «تعديل»). المشترك يُدار عبر وصول القسم لا فعلًا مستقلًّا هنا.
_EDIT_ACTION_ENTITIES = ("offer", "batch")


def _build_field_catalog(manager_id: int) -> list[dict]:
    """قائمة الكيانات + حقولها القابلة للمنح + الحالة الحاليّة — لقائمة الإعداد.

    ``control_on`` = التحكّم الحقليّ مُفعَّل لهذا الكيان (فقط الحقول المؤشَّرة
    قابلة للتعديل). مُطفأ = كل الحقول قابلة للتعديل (سلوك اليوم). ``edit_action``
    (offer/batch) = هل يُسمح للمدير بتعديل الكيان أصلًا (opt-in، افتراضه مالك فقط)."""
    from ..services import manager_grants as _mg
    out: list[dict] = []
    for entity in _mg.FIELD_REGISTRY:
        granted = _mg.field_grants(manager_id, entity, tenant_id=_tid())
        control_on = granted is not None
        fields = [
            {"key": f["key"], "label": f["label"],
             "granted": bool(control_on and f["key"] in granted)}
            for f in _mg.entity_field_defs(entity)
        ]
        supports_edit = entity in _EDIT_ACTION_ENTITIES
        out.append({
            "entity": entity,
            "label": _ENTITY_LABELS.get(entity, entity),
            "control_on": control_on,
            "fields": fields,
            "supports_edit_action": supports_edit,
            "edit_allowed": bool(supports_edit and _mg.action_allowed(
                manager_id, entity, "edit", tenant_id=_tid())),
        })
    return out


def business_operator_policy(entity_type: str, entity_id: int):
    try:
        permissions = {
            key: request.form.get(key) in {"1", "on", "true", "yes"}
            for key in (
                "can_create_batch",
                "can_create_subscriber",
                "can_activate_subscriber",
                "can_give_free_days",
                "can_give_trial_days",
                "can_give_loan",
                "can_manage_distributors",
                "can_view_all_subscribers",
                "can_view_all_card_batches",
                "can_see_wholesale",
                "can_see_password",
                "can_import_batches",
            )
        }
        def _nonneg_int(name):
            try:
                return max(0, int(request.form.get(name) or 0))
            except (TypeError, ValueError):
                return 0
        limits = {
            "max_free_days": int(request.form.get("max_free_days") or 0),
            "max_trial_days": int(request.form.get("max_trial_days") or 0),
            "loan_wallet_deducted": request.form.get("loan_wallet_deducted") in {"1", "on", "true", "yes"},
            # المرحلة A: السقوف الرقميّة (0 = بلا حدّ).
            "max_subscribers": _nonneg_int("max_subscribers"),
            "max_cards_total": _nonneg_int("max_cards_total"),
            "max_cards_daily": _nonneg_int("max_cards_daily"),
            # المرحلة F: تاريخ انتهاء المنوحات (فارغ = دائم).
            "grants_expire_at": (request.form.get("grants_expire_at") or "").strip(),
        }
        _service().set_policy(
            entity_type=entity_type,
            entity_id=entity_id,
            permissions=permissions,
            limits=limits,
            profit_share_percent=float(request.form.get("profit_share_percent") or 0),
            credit_limit=request.form.get("credit_limit") or "0",
            require_approval_above=request.form.get("require_approval_above") or "0",
        )
        # المستوى 1: وصول القسم (3 حالات) — للمدير فقط. حقول النموذج اسمها
        # ``section_<name>`` وقيمتها open/locked/hidden. غير المُرسَل = open.
        if entity_type == "manager":
            from ..services import manager_grants as _mg
            section_map = {
                name: request.form.get(f"section_{name}")
                for name in _mg.section_names()
                if request.form.get(f"section_{name}")
            }
            _mg.set_section_access(int(entity_id), section_map, tenant_id=_tid(),
                                   by=int(session.get("admin_id") or 0))
            # المستوى 3: التحكّم الحقليّ لكل كيان. ``field_control_<entity>``
            # يُفعّل الحصر؛ عندها ``field_<entity>_<key>`` المؤشَّرة = المسموحة.
            # غير المُفعَّل = التحكّم مطفأ (كل الحقول قابلة للتعديل).
            _yes = {"1", "on", "true", "yes"}
            for entity in _mg.FIELD_REGISTRY:
                if request.form.get(f"field_control_{entity}") in _yes:
                    granted = [
                        f["key"] for f in _mg.entity_field_defs(entity)
                        if request.form.get(f"field_{entity}_{f['key']}") in _yes
                    ]
                    _mg.set_field_grants(int(entity_id), entity, granted, tenant_id=_tid())
                else:
                    _mg.set_field_grants(int(entity_id), entity, None, tenant_id=_tid())
            # بوّابة فعل «تعديل» للكيانات المالكيّة (offer/batch) — opt-in.
            # ``action_edit_<entity>`` مؤشَّر = يُسمح للمدير بتعديلها.
            for entity in _EDIT_ACTION_ENTITIES:
                allow_edit = request.form.get(f"action_edit_{entity}") in _yes
                _mg.set_action_grants(
                    int(entity_id), entity,
                    {"edit": True} if allow_edit else None, tenant_id=_tid())
            # الأفعال بلا علَم (يَحرسها RBAC أو افتراضها OFF مثل أفعال المتجر):
            # نُخزّن التجاوز الصريح فقط عندما يُخالف الافتراض (يُبقي الصفّ نظيفًا)،
            # ويَدعم الاتجاهين: تفعيل فعلٍ افتراضه OFF، أو إطفاء فعلٍ افتراضه ON.
            for akey in _mg.rbac_action_keys():
                checked = request.form.get(f"action_{akey}") in _yes
                default = bool(_mg.ACTION_REGISTRY.get(akey, {}).get("default", True))
                _mg.set_action_override(
                    int(entity_id), akey,
                    None if checked == default else checked, tenant_id=_tid())
        flash("تم تحديث صلاحيات وحدود المشغل.", "success")
    except (ManagerDistributorError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_operator_profile", entity_type=entity_type, entity_id=entity_id))


# ─── F2: قوالب الصلاحيات ───────────────────────────────────────────────────
def manager_presets_create():
    """يُنشئ قالبًا: من منوحات مدير مصدر (source_manager_id) أو فارغًا."""
    from ..services import manager_presets as _p
    try:
        src = request.form.get("source_manager_id")
        _p.create_preset(
            request.form.get("name") or "",
            tenant_id=_tid(),
            source_manager_id=int(src) if src and str(src).isdigit() else None,
            by=int(session.get("admin_id") or 0),
        )
        flash("تم حفظ قالب الصلاحيات.", "success")
    except _p.ManagerPresetError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("radius.business_operators"))


def manager_presets_delete(preset_id: int):
    from ..services import manager_presets as _p
    _p.delete_preset(preset_id, tenant_id=_tid())
    flash("تم حذف القالب.", "success")
    return redirect(request.referrer or url_for("radius.business_operators"))


def business_operator_apply_preset(entity_id: int):
    """يُطبّق قالبًا على مدير (يَستبدل منوحاته)، ثم يُمكن للمالك التعديل."""
    from ..services import manager_presets as _p
    try:
        _p.apply_preset(int(request.form.get("preset_id") or 0), int(entity_id),
                        tenant_id=_tid())
        flash("تم تطبيق القالب على المدير. يمكنك التعديل الآن.", "success")
    except _p.ManagerPresetError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_operator_profile",
                            entity_type="manager", entity_id=entity_id))


def business_operator_recharge(entity_type: str, entity_id: int):
    try:
        _service().recharge_wallet(
            entity_type=entity_type,
            entity_id=entity_id,
            amount=request.form.get("amount") or "0",
            method=request.form.get("method") or "cash",
            actor=_actor(),
        )
        flash("تم شحن محفظة المشغل.", "success")
    except (ManagerDistributorError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_operator_profile", entity_type=entity_type, entity_id=entity_id))
