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
    # وراثة الدور: إعادة المدير لوراثة أساس دوره (إزالة تجاوزاته الفرديّة).
    bp.add_url_rule("/business-operators/manager/<int:entity_id>/reset-grants", "business_operator_reset_grants", business_operator_reset_grants, methods=["POST"])
    # F3: مدراء فرعيّون — إنشاء تحت الأب + تفويض جزءٍ من صلاحياته (بسقف).
    bp.add_url_rule("/business-operators/sub-managers", "sub_manager_create", sub_manager_create, methods=["POST"])
    bp.add_url_rule("/business-operators/sub-managers/<int:child_id>/delegate", "sub_manager_delegate", sub_manager_delegate, methods=["POST"])
    # طابور اعتماد الإجراءات عالية القيمة (المالك).
    bp.add_url_rule("/approvals", "manager_approvals", manager_approvals_page, methods=["GET"])
    bp.add_url_rule("/approvals/<int:approval_id>/approve", "manager_approval_approve", manager_approval_approve, methods=["POST"])
    bp.add_url_rule("/approvals/<int:approval_id>/reject", "manager_approval_reject", manager_approval_reject, methods=["POST"])


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
    rate_catalog = []
    if entity_type == "manager":
        from ..services import manager_presets as _p
        from ..services import manager_grants as _mg
        presets = _p.list_presets(tenant_id=_tid())
        _rd = (profile.get("limits") or {}).get("rate_daily") or {}
        rate_catalog = [
            {"key": k, "label": _mg.ACTION_REGISTRY.get(k, {}).get("label", k),
             "value": int(_rd.get(k) or 0)}
            for k in _RATE_LIMIT_ACTIONS
        ]
    # دور المدير — لبانر «الأفعال والرؤية موروثة من الدور X» + رابط ضبطه.
    mgr_role = None
    if entity_type == "manager":
        try:
            from ..db.repos import admins_repo
            _a = admins_repo.get_admin(int(entity_id))
            _rid = getattr(_a, "role_id", None) if _a else None
            if _rid:
                mgr_role = admins_repo.get_role(int(_rid))
        except Exception:  # noqa: BLE001
            mgr_role = None
    return render_template(
        "radius/business_operator_profile.html",
        profile=profile,
        section_catalog=section_catalog,
        section_states=section_states,
        field_catalog=field_catalog,
        action_catalog=action_catalog,
        limits_catalog=limits_catalog,
        presets=presets,
        rate_catalog=rate_catalog,
        mgr_role=mgr_role,
    )


# تسميات الكيانات القابلة للتحكّم الحقليّ (عربيّة).
_ENTITY_LABELS = {"subscriber": "المشترك", "offer": "العرض", "batch": "الباقة (الحزمة)"}

# كيانات «مالك فقط» افتراضًا يَفتح المالك تعديلَها لمديرٍ صراحةً (منح فعل
# «تعديل»). المشترك يُدار عبر وصول القسم لا فعلًا مستقلًّا هنا.
_EDIT_ACTION_ENTITIES = ("offer", "batch")
# كياناتٌ تُمنح فيها «الإضافة» أيضًا (لا الحزم — تأليفُها مالكيٌّ بنيويّ)
_CREATE_ACTION_ENTITIES = ("offer",)

# A2: الأفعال التي يُتاح لها حدّ معدّل يوميّ (money-ish/حسّاسة).
_RATE_LIMIT_ACTIONS = ("subscriber.loan", "subscriber.renew", "subscriber.quota",
                       "subscriber.balance_add", "subscriber.payment")


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
        supports_create = entity in _CREATE_ACTION_ENTITIES
        out.append({
            "entity": entity,
            "label": _ENTITY_LABELS.get(entity, entity),
            "control_on": control_on,
            "fields": fields,
            "supports_edit_action": supports_edit,
            "edit_allowed": bool(supports_edit and _mg.action_allowed(
                manager_id, entity, "edit", tenant_id=_tid())),
            "supports_create_action": supports_create,
            "create_allowed": bool(supports_create and _mg.action_allowed(
                manager_id, entity, "create", tenant_id=_tid())),
        })
    return out


def business_operator_policy(entity_type: str, entity_id: int):
    try:
        _yes = {"1", "on", "true", "yes"}
        _flag_keys = (
            "can_create_batch", "can_create_subscriber", "can_activate_subscriber",
            "can_give_free_days", "can_give_trial_days", "can_give_loan",
            "can_manage_distributors", "can_view_all_subscribers",
            "can_view_all_card_batches", "can_see_wholesale", "can_see_password",
            "can_create_sub_managers", "can_see_balance", "can_see_profit",
            "can_import_batches",
        )
        # وراثة الدور: خزّن العلَم الفرديّ **فقط عند مخالفته أساس الدور** — فيَبقى
        # الموروث حيًّا، ولا يُجمّد فتحُ/حفظُ ملفّ المدير وراثتَه. التوزيع لا دور له.
        if entity_type == "manager":
            from ..services import manager_grants as _mg
            from ..services.manager_distributor_ops import DEFAULT_PERMISSIONS as _DP
            role_flags = _mg.role_flags_for_admin(int(entity_id), tenant_id=_tid())
            permissions = {}
            for key in _flag_keys:
                desired = request.form.get(key) in _yes
                baseline = bool(role_flags.get(key, _DP.get(key, False)))
                if desired != baseline:
                    permissions[key] = desired
        else:
            permissions = {key: request.form.get(key) in _yes for key in _flag_keys}
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
            # A2: سقف الإنفاق (money) + معدّلات الأفعال اليوميّة.
            "spend_cap_daily": (request.form.get("spend_cap_daily") or "0").strip() or "0",
            "spend_cap_monthly": (request.form.get("spend_cap_monthly") or "0").strip() or "0",
            "rate_daily": {
                k: _nonneg_int(f"rate_{k}")
                for k in _RATE_LIMIT_ACTIONS
                if _nonneg_int(f"rate_{k}") > 0
            },
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
            # sparse-ضدّ-الدور: خزّن حالة القسم الفرديّة فقط عند مخالفتها حالة
            # الدور الموروثة (وإلّا يَبقى موروثًا). الغياب = مفتوح.
            _role_secs = _mg.role_sections_for_admin(int(entity_id), tenant_id=_tid())
            section_map = {}
            for name in _mg.section_names():
                chosen = request.form.get(f"section_{name}")
                if not chosen:
                    continue
                inherited = _role_secs.get(name, _mg.DEFAULT_SECTION_STATE)
                if chosen != inherited:
                    section_map[name] = chosen
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
                ops = {}
                if request.form.get(f"action_edit_{entity}") in _yes:
                    ops["edit"] = True
                # «إضافة» بوّابةٌ مستقلّةٌ عن «تعديل»: مديرٌ يُصحّح سعرًا ليس
                # بالضرورة مَن يؤلّف عرضًا جديدًا. فتُمنحان منفصلتين.
                if entity in _CREATE_ACTION_ENTITIES and                         request.form.get(f"action_create_{entity}") in _yes:
                    ops["create"] = True
                _mg.set_action_grants(
                    int(entity_id), entity, ops or None, tenant_id=_tid())
            # الأفعال بلا علَم (يَحرسها RBAC أو افتراضها OFF مثل أفعال المتجر):
            # نُخزّن التجاوز الصريح فقط عندما يُخالف الافتراض (يُبقي الصفّ نظيفًا)،
            # ويَدعم الاتجاهين: تفعيل فعلٍ افتراضه OFF، أو إطفاء فعلٍ افتراضه ON.
            for akey in _mg.rbac_action_keys():
                checked = request.form.get(f"action_{akey}") in _yes
                # المقارنة بأساس **الدور الموروث** لا بافتراض السجلّ: نُخزّن
                # التجاوز فقط عند مخالفته الدور، فتَبقى الوراثة حيّة.
                baseline = _mg.role_baseline_action(int(entity_id), akey, tenant_id=_tid())
                _mg.set_action_override(
                    int(entity_id), akey,
                    None if checked == baseline else checked, tenant_id=_tid())
        flash("تم تحديث صلاحيات وحدود المشغل.", "success")
    except (ManagerDistributorError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_operator_profile", entity_type=entity_type, entity_id=entity_id))


def business_operator_reset_grants(entity_id: int):
    """يُعيد المدير لوراثة أساس دوره: يُزيل تجاوزاته الفرديّة (الأعلام/الأفعال/
    الأقسام/الحقول) فتَسري أفعال ورؤية دوره كاملةً. الحدود الرقميّة تبقى."""
    try:
        from ..services import manager_grants as _mg
        _mg.reset_overrides_to_role(int(entity_id), tenant_id=_tid())
        flash("تمت إعادة هذا المدير لوراثة أساس دوره — أُزيلت تجاوزاته الفرديّة "
              "(الحدود الرقميّة باقية).", "success")
    except Exception as exc:  # noqa: BLE001
        flash(str(exc), "error")
    return redirect(url_for("radius.business_operator_profile",
                            entity_type="manager", entity_id=entity_id))


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


# ─── F3: مدراء فرعيّون + تفويض بسقف ────────────────────────────────────────
def _actor_id() -> int:
    return int(session.get("admin_id") or 0)


def _actor_is_super() -> bool:
    return bool(session.get("is_super_admin"))


def sub_manager_create():
    """مدير يَملك can_create_sub_managers يُنشئ مديرًا فرعيًّا تحته (parent).
    السوبر مسموح أيضًا. غير المُنِح → 403 (سطح إنشاء حسّاس)."""
    from flask import abort
    from ..services import manager_grants as _mg
    if not _actor_is_super() and not _mg.can_create_sub_managers(_actor_id(), tenant_id=_tid()):
        abort(403)
    from ..db.repos import admins_repo
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username or len(password) < 8:
        flash("اسم المستخدم مطلوب وكلمة المرور 8 أحرف على الأقل.", "error")
        return redirect(request.referrer or url_for("radius.business_operators"))
    try:
        child = admins_repo.create_admin(
            username=username, password=password,
            full_name=(request.form.get("full_name") or username),
            is_super_admin=False)
        # اربط الأب — parent_admin_id = المُنشئ (أو المُمرَّر للسوبر).
        parent = _actor_id()
        if _actor_is_super() and (request.form.get("parent_admin_id") or "").isdigit():
            parent = int(request.form.get("parent_admin_id"))
        from ..db.connection import db
        db().execute("UPDATE admins SET parent_admin_id=? WHERE id=?", (parent, int(child.id)))
        flash(f"تم إنشاء المدير الفرعيّ «{username}».", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("radius.business_operators"))


def sub_manager_delegate(child_id: int):
    """الأب (أو السوبر) يُفوّض جزءًا من صلاحياته للابن — بسقف: لا يَمنح ما لا
    يَملكه (clamp_delegation). فقط أب الطفل يُفوّض له."""
    from flask import abort
    from ..services import manager_grants as _mg
    parent = _actor_id()
    if not _actor_is_super() and _mg.parent_admin_id(int(child_id)) != parent:
        abort(403)   # لستَ أب هذا المدير الفرعيّ
    # اجمع المطلوب من النموذج: أعلام can_* + أفعال rbac.
    _yes = {"1", "on", "true", "yes"}
    want_flags = {f: (request.form.get(f"flag_{f}") in _yes)
                  for f in ("can_create_subscriber", "can_activate_subscriber",
                            "can_give_loan", "can_import_batches",
                            "can_manage_distributors", "can_see_wholesale",
                            "can_see_password")}
    want_actions = {k: (request.form.get(f"action_{k}") in _yes)
                    for k in _mg.rbac_action_keys()}
    # السقف: للسوبر لا قصّ (يَملك كل شيء)؛ للأب نَقصّ على ما يَملكه.
    if _actor_is_super():
        flags_final, actions_final = want_flags, want_actions
    else:
        flags_final, actions_final = _mg.clamp_delegation(
            parent, flags=want_flags, actions=want_actions, tenant_id=_tid())
    # اكتب على سياسة الابن.
    _service().set_policy(entity_type="manager", entity_id=int(child_id),
                          permissions=flags_final)
    for k, v in actions_final.items():
        default = bool(_mg.ACTION_REGISTRY.get(k, {}).get("default", True))
        _mg.set_action_override(int(child_id), k, None if v == default else v, tenant_id=_tid())
    flash("تم تفويض الصلاحيات للمدير الفرعيّ (ضمن سقف صلاحياتك).", "success")
    return redirect(url_for("radius.business_operator_profile",
                            entity_type="manager", entity_id=child_id))


# ─── طابور اعتماد الإجراءات عالية القيمة (المالك) ──────────────────────────
def manager_approvals_page():
    from ..services import manager_approvals as _ap
    return render_template("radius/manager_approvals.html",
                           pending=_ap.list_pending(tenant_id=_tid()))


def manager_approval_approve(approval_id: int):
    from ..services import manager_approvals as _ap
    try:
        _ap.approve(approval_id, decided_by=int(session.get("admin_id") or 0), tenant_id=_tid())
        flash("تم اعتماد الطلب وتنفيذه.", "success")
    except _ap.ApprovalError as exc:
        flash(str(exc), "error")
    except Exception as exc:  # noqa: BLE001 — أظهِر سبب فشل التنفيذ للمالك
        flash(f"تعذّر تنفيذ الطلب بعد الاعتماد: {exc}", "error")
    return redirect(url_for("radius.manager_approvals"))


def manager_approval_reject(approval_id: int):
    from ..services import manager_approvals as _ap
    try:
        _ap.reject(approval_id, decided_by=int(session.get("admin_id") or 0), tenant_id=_tid())
        flash("تم رفض الطلب.", "success")
    except _ap.ApprovalError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.manager_approvals"))


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
