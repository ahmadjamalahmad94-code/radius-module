"""Manager/distributor operational profile routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.manager_distributor_ops import ManagerDistributorError, ManagerDistributorOpsService
from ..core.numbers import strict_float  # Infinity/NaN → ValueError (422/flash)


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


def _distributor_owner_filter():
    """None = كل الموزّعين (المالك/الشريك/«مدير عام»)، وإلّا معرّف المدير نفسه."""
    if session.get("is_super_admin"):
        return None
    from ..services.distributor_scope import list_owner_filter
    return list_owner_filter(int(session.get("admin_id") or 0), tenant_id=_tid())


def business_operators():
    service = _service()
    return render_template(
        "radius/business_operators.html",
        managers=service.list_scope(entity_type="manager"),
        distributors=service.list_scope(entity_type="distributor",
                                        owner_admin_id=_distributor_owner_filter()),
    )


def business_operator_profile(entity_type: str, entity_id: int):
    if entity_type == "distributor":
        # عزل المِلكية: ملفّ موزّعٍ لا يتبع المدير (وليس «مدير عام») = 403.
        _owner = _distributor_owner_filter()
        if _owner is not None:
            from flask import abort
            from ..db.repos import operations_repo
            _d = operations_repo.get_distributor(_tid(), int(entity_id))
            if _d is not None and int(_d.get("admin_id") or 0) != int(_owner):
                abort(403)
    try:
        profile = _service().profile(entity_type=entity_type, entity_id=entity_id)
    except ManagerDistributorError:
        return redirect(url_for("radius.business_operators"))
    # مصفوفة الأقسام (3 حالات) + التحكّم الحقليّ — للمدير فقط.
    section_catalog = []
    section_states = ()
    field_catalog = []
    action_catalog = []
    tri_catalog = []
    limits_catalog = []
    if entity_type == "manager":
        from ..services import manager_grants as _mg
        section_catalog = _mg.section_catalog(int(entity_id), tenant_id=_tid())
        section_states = _mg.SECTION_STATES
        field_catalog = _build_field_catalog(int(entity_id))
        action_catalog = _mg.action_catalog(int(entity_id), tenant_id=_tid())
        tri_catalog = _mg.tristate_catalog(int(entity_id), tenant_id=_tid())
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
        tri_catalog=tri_catalog,
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


def _policy_actor_guard(entity_id: int) -> tuple[int, bool]:
    """(الفاعل، هل بمقام المالك). غير المالك لا يعدّل صلاحيات نفسه ولا حساب
    مالكٍ/شريك — ترفع OwnerGuardError برسالة عربيّة."""
    from ..auth.owner import OwnerGuardError, assert_can_modify_admin, is_owner_like
    actor = int(session.get("admin_id") or 0)
    actor_owner = bool(session.get("is_super_admin")) or (bool(actor) and is_owner_like(actor))
    if not actor_owner:
        if not actor or actor == int(entity_id):
            raise OwnerGuardError("لا يمكنك تعديل صلاحياتك بنفسك — يعدّلها المالك.")
        assert_can_modify_admin(actor, int(entity_id))
    return actor, actor_owner


def business_operator_policy(entity_type: str, entity_id: int):
    from ..auth.owner import OwnerGuardError
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
        # القيم الرقميّة تُتحقَّق أوّلًا — فلا يُحفَظ نصف النموذج ثم يُرفَض.
        profit_share = strict_float(request.form.get("profit_share_percent") or 0)
        saved = None
        if entity_type == "manager":
            from ..services import manager_grants as _mg
            actor, actor_owner = _policy_actor_guard(int(entity_id))
            # «حسب الدور / مسموح / ممنوع» لكل فعلٍ وعلَم (tri_<key>): يُخزَّن
            # التجاوز الصريح فقط («حسب الدور» يحذفه) — فيَبقى الموروث حيًّا ولا
            # يغيّر حفظُ الصفحة بلا تعديل أيَّ صلاحية (D01/D02). النماذج القديمة
            # (مربّعات can_*/action_*) تُفهَم كما كانت.
            saved = _mg.save_manager_overrides(
                int(entity_id), request.form, tenant_id=_tid(),
                actor_id=actor, actor_owner=actor_owner)
            permissions = saved["flags"]
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
            profit_share_percent=profit_share,
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
            # بوّابة «تعديل/إضافة» الكيانات المالكيّة (offer/batch) صارت صفوفًا
            # ثلاثيّة في مصفوفة الأفعال. العقد القديم (مربّع action_edit_<entity>)
            # يُطبَّق فقط حين لا يُرسَل الحقل الثلاثيّ لذلك الكيان.
            legacy = set(saved.get("legacy_entities") or ()) if saved else set()
            if _mg.tri_input_name("batch.edit") not in request.form:
                legacy.add("batch")
            for entity in _EDIT_ACTION_ENTITIES:
                if entity not in legacy:
                    continue
                ops = {}
                if request.form.get(f"action_edit_{entity}") in _yes:
                    ops["edit"] = True
                # «إضافة» بوّابةٌ مستقلّةٌ عن «تعديل»: مديرٌ يُصحّح سعرًا ليس
                # بالضرورة مَن يؤلّف عرضًا جديدًا. فتُمنحان منفصلتين.
                if entity in _CREATE_ACTION_ENTITIES and                         request.form.get(f"action_create_{entity}") in _yes:
                    ops["create"] = True
                _mg.set_action_grants(
                    int(entity_id), entity, ops or None, tenant_id=_tid())
            # D05: المنح الدقيقة تُقرأ حيّةً كل طلب؛ ونزيد ختم الصلاحيات كي تُعاد
            # قراءة ما في الجلسة أيضًا فورًا.
            try:
                from ..db.repos import admins_repo as _ar
                _ar.bump_authz_epoch(admin_ids=[int(entity_id)])
            except Exception:  # noqa: BLE001
                pass
        flash("تم تحديث صلاحيات وحدود المشغل.", "success")
        if saved and saved.get("refused"):
            flash("لم يُمنَح «مسموح» لما لا تملكه أنت: " + "، ".join(saved["refused"][:8]),
                  "warning")
    except OwnerGuardError as exc:
        flash(str(exc), "error")
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
    # الفرعيّ يرث دور مُنشئه (أو الأب المختار من السوبر) — لا أعلى منه. كان
    # بلا دور فيأخذ super_admin (٧٧ صلاحية) فيتجاوز مُنشئَه نفسه.
    _role_src = _actor_id()
    if _actor_is_super() and (request.form.get("parent_admin_id") or "").isdigit():
        _role_src = int(request.form.get("parent_admin_id"))
    _parent_row = admins_repo.get_admin(_role_src) if _role_src else None
    # المالك/الشريك بلا أبٍ مختار: دوره («مدير عام» أو لا دور) ليس سقفًا لمديرٍ
    # فرعيّ — الأقلّ صلاحيةً (لا «مدير عام» ضمنيّ بكل الصلاحيات غير المالكيّة).
    _child_role = getattr(_parent_row, "role_id", None)
    if _child_role is None or (_actor_is_super() and _role_src == _actor_id()):
        _child_role = admins_repo.least_privileged_role_id()
    # دور «مدير عام» لا يُورَّث ضمنيًّا: منحه مقصورٌ على المالك/الشريك، والمدير
    # الفرعيّ بلا دورٍ مختار يبدأ بالأقلّ صلاحيةً ثم يُفوَّض له.
    elif admins_repo.role_is_super(admins_repo.get_role(int(_child_role))):
        _child_role = admins_repo.least_privileged_role_id()
    # دورٌ مختار صراحةً (اختياريّ): موجود، ليس «مدير عام» لغير المالك، وضمن
    # صلاحيات المُنشئ (لا تصعيد).
    _raw_role = (request.form.get("role_id") or "").strip()
    if _raw_role:
        from ..auth.owner import OwnerGuardError, assert_role_within_actor
        _picked = admins_repo.get_role(int(_raw_role)) if _raw_role.isdigit() else None
        try:
            if _picked is None:
                raise OwnerGuardError("الدور المحدد غير موجود.")
            if admins_repo.role_is_super(_picked) and not _actor_is_super():
                raise OwnerGuardError("منح دور «مدير عام» مقصورٌ على المالك أو الشريك.")
            assert_role_within_actor(_actor_id(), tuple(_picked.permissions or ()))
        except OwnerGuardError as exc:
            flash(str(exc), "error")
            return redirect(request.referrer or url_for("radius.business_operators"))
        _child_role = int(_picked.id)
    try:
        child = admins_repo.create_admin(
            username=username, password=password,
            full_name=(request.form.get("full_name") or username),
            role_id=_child_role,
            is_super_admin=False)
        # اربط الأب — parent_admin_id = المُنشئ (أو المُمرَّر للسوبر).
        parent = _actor_id()
        if _actor_is_super() and (request.form.get("parent_admin_id") or "").isdigit():
            parent = int(request.form.get("parent_admin_id"))
        from ..db.connection import db
        db().execute("UPDATE admins SET parent_admin_id=? WHERE id=?", (parent, int(child.id)))
        # الابن ≤ الأب: يبدأ بحدود أبيه الرقميّة وسقف ائتمانه (لا «بلا حدّ»
        # افتراضيًّا لمديرٍ أبوه محدود).
        if parent and not _mg_owner_like(parent):
            _inherit_parent_caps(int(child.id), int(parent))
        flash(f"تم إنشاء المدير الفرعيّ «{username}».", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("radius.business_operators"))


def _mg_owner_like(admin_id: int) -> bool:
    try:
        from ..auth.owner import is_owner_like
        return bool(is_owner_like(int(admin_id)))
    except Exception:  # noqa: BLE001
        return False


def _inherit_parent_caps(child_id: int, parent_id: int) -> None:
    """الابن الجديد يبدأ بحدود أبيه الرقميّة + تاريخ انتهاء منحه + سقف ائتمانه."""
    from ..services import manager_grants as _mg
    try:
        pol = _service().get_policy(entity_type="manager", entity_id=int(parent_id))
        plims = pol.get("limits") or {}
        caps = {k: plims.get(k) for k in (*_mg.DELEGABLE_LIMIT_KEYS, "grants_expire_at")
                if plims.get(k) not in (None, "", 0, "0", "0.00")}
        credit = pol.get("credit_limit")
        if caps or _mg._num(credit) > 0:
            _service().update_policy(
                entity_type="manager", entity_id=int(child_id), limits=caps or None,
                credit_limit=credit if _mg._num(credit) > 0 else None)
            _mg._invalidate_cache()
    except Exception:  # noqa: BLE001 — لا نكسر الإنشاء؛ التفويض يقصّ لاحقًا
        pass


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
    # الأفعال المُشتقّة من الدور (حذف/تمديد/دفعة/قطع…): تُمسّ فقط إن أرسلها
    # النموذج صراحةً — الإطفاء تجاوزٌ صريح، والتشغيل يزيل التجاوز (فيعود للدور،
    # وسقف الأب يسري وقت التشغيل عبر action_permitted).
    want_derived = {k: (request.form.get(f"action_{k}") in _yes)
                    for k in _mg.derived_action_keys() if f"action_{k}" in request.form}
    # الحدود وسقف الائتمان: تُمسّ فقط إن أُرسلت (كانت تُعاد للافتراض عند كل حفظ).
    want_limits = {k: request.form.get(f"limit_{k}")
                   for k in (*_mg.DELEGABLE_LIMIT_KEYS, "grants_expire_at")
                   if f"limit_{k}" in request.form}
    want_credit = request.form.get("credit_limit") if "credit_limit" in request.form else None
    # السقف: للسوبر لا قصّ (يَملك كل شيء)؛ للأب نَقصّ على ما يَملكه.
    if _actor_is_super():
        flags_final, actions_final = want_flags, want_actions
        limits_final = _mg.clamp_delegated_limits(None, want_limits, tenant_id=_tid())
        credit_final = (None if want_credit is None
                        else _mg.clamp_delegated_credit(None, want_credit, tenant_id=_tid()))
    else:
        flags_final, actions_final = _mg.clamp_delegation(
            parent, flags=want_flags, actions=want_actions, tenant_id=_tid())
        limits_final = _mg.clamp_delegated_limits(parent, want_limits, tenant_id=_tid())
        credit_final = (None if want_credit is None
                        else _mg.clamp_delegated_credit(parent, want_credit, tenant_id=_tid()))
    # اكتب على سياسة الابن — **دمجٌ جزئيّ**: ما لم يُرسَل يبقى كما هو (الحدود،
    # الائتمان، الأعلام الأخرى مثل «عرض كل المشتركين»).
    _service().update_policy(entity_type="manager", entity_id=int(child_id),
                             permissions=flags_final, limits=limits_final or None,
                             credit_limit=credit_final)
    for k, v in actions_final.items():
        default = bool(_mg.ACTION_REGISTRY.get(k, {}).get("default", True))
        _mg.set_action_override(int(child_id), k, None if v == default else v, tenant_id=_tid())
    for k, v in want_derived.items():
        _mg.set_action_override(int(child_id), k, None if v else False, tenant_id=_tid())
    _mg._invalidate_cache()
    try:
        from ..db.repos import admins_repo as _ar
        _ar.bump_authz_epoch(admin_ids=[int(child_id)])
    except Exception:  # noqa: BLE001 — الختم تحسين (جلسات الابن تُحدَّث فورًا)
        pass
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
