"""Admins + Roles routes.

RM-H6: extends admins form with profile fields (phone, notes, avatar,
tags) and adds roles CRUD (create/edit/delete with color picker and
grouped permissions). Also adds /admins/profile-summary read-only view.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..db.repos import admins_repo
from ..services.admins import get_admins_service


def _notify_panel_of_admin_change(*, deleted_admin_id: int | None = None) -> None:
    """Fire an admins-report to the licensing panel right after a local admin
    write (create/update/delete). Best-effort: swallows every error so a bridge
    outage never breaks a local write. Passes ``deleted_admin_id`` for a
    differential tombstone; otherwise a full-snapshot report.

    This closes the loop on «deleted-still-shows»: the panel sees the change
    immediately instead of waiting for the periodic worker cadence.
    """
    try:
        from ..services.license_admin_inventory_report import (
            report_admins_best_effort,
        )
        report_admins_best_effort(deleted_admin_id=deleted_admin_id)
    except Exception:  # noqa: BLE001
        pass


def register_admins_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/admins", "admins_list", admins_list, methods=["GET"])
    bp.add_url_rule("/admins/new", "admins_new", admins_new, methods=["GET"])
    bp.add_url_rule("/admins", "admins_create", admins_create, methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>/edit", "admins_edit", admins_edit, methods=["GET"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_update", admins_update, methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>/delete", "admins_delete", admins_delete, methods=["POST"])
    bp.add_url_rule("/admins/profile-summary", "admins_profile_summary",
                    admins_profile_summary, methods=["GET"])
    bp.add_url_rule("/roles", "roles_list", roles_list, methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_update", roles_update, methods=["POST"])
    # RM-H6: roles CRUD
    bp.add_url_rule("/roles/new", "roles_new", roles_new, methods=["GET"])
    bp.add_url_rule("/roles", "roles_create", roles_create, methods=["POST"])
    bp.add_url_rule("/roles/<int:role_id>/edit", "roles_edit", roles_edit, methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>/save", "roles_save", roles_save, methods=["POST"])
    bp.add_url_rule("/roles/<int:role_id>/delete", "roles_delete", roles_delete, methods=["POST"])
    # وراثة الأفعال/الرؤية: محرّر أساس الدور (يَرثه كل مدير من دوره)
    bp.add_url_rule("/roles/<int:role_id>/grants", "roles_grants", roles_grants, methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>/grants", "roles_grants_save", roles_grants_save, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _actor_id():
    aid = session.get("admin_id")
    return int(aid) if aid else None


def _scope_tenant():
    """Security B-13: tenant the panel admin list is limited to, or ``None``
    for the owner / co-owner (server-wide). Everyone else — «مدير عام»
    included — sees only admins of his current tenant."""
    from ..auth.owner import is_owner_like
    aid = _actor_id()
    if aid and is_owner_like(aid):
        return None
    from flask import g
    return int(session.get("tenant_id") or getattr(g, "tenant_id", 1) or 1)


def _scoped(admins):
    scope = _scope_tenant()
    if scope is None:
        return list(admins)
    allowed = admins_repo.admin_ids_in_tenant(scope)
    return [a for a in admins if int(a.id) in allowed]


def _require_in_scope(admin_id: int) -> None:
    scope = _scope_tenant()
    if scope is not None and not admins_repo.admin_in_tenant(int(admin_id), scope):
        abort(404)


def _join_creator_tenant(admin) -> None:
    """Security B-13: a new admin joins the creator's current tenant."""
    try:
        from ..core.tenant import TenantMembership
        from ..db.repos import tenants_repo
        from flask import g
        tid = int(session.get("tenant_id") or getattr(g, "tenant_id", 1) or 1)
        tenants_repo.add_membership(TenantMembership(
            id=None, tenant_id=tid, admin_id=int(admin.id),
            role_id=getattr(admin, "role_id", None), status="active",
            invited_by=int(_actor_id() or 0)))
    except Exception:  # noqa: BLE001 — never fail the create on this
        pass


def _super_role_id():
    from ..core.constants import ROLE_SUPER_ADMIN
    r = admins_repo.get_role_by_name(ROLE_SUPER_ADMIN)
    return r.id if r else None


def _is_super_role(role_id) -> bool:
    return bool(role_id and admins_repo.role_is_super(admins_repo.get_role(int(role_id))))


def _roles_for_actor(roles):
    """D12: غير المالك يرى في قائمة الأدوار ما لا يتجاوز صلاحياته فقط."""
    from ..auth.owner import is_owner_like
    if is_owner_like():
        return roles
    mine = set(get_admins_service().permissions_of(admins_repo.get_admin(_actor_id()))) \
        if _actor_id() else set()
    out = []
    for r in roles:
        probe = type("A", (), {"role_id": r.id})()
        if set(admins_repo.admin_permissions(probe)) <= mine:
            out.append(r)
    return out


def _role_beyond_actor(role) -> bool:
    """هل يحمل الدورُ مفتاحًا لا يملكه الفاعل؟ (سقفُ التفويض يَرفض حفظَه.)"""
    aid = _actor_id()
    try:
        from ..auth.owner import _perms_of, _role_perms, is_owner_like_id
        if aid is None or is_owner_like_id(aid):
            return False
        return not (_role_perms(int(role.id)) <= _perms_of(int(aid)))
    except Exception:  # noqa: BLE001 — fail-open: الخادمُ هو الحَكَم
        return False


def _form_perms(*, role=None):
    """D14: محرّر الأدوار يعرض المفاتيح الحيّة فقط (القديمة الميّتة مخفيّة).

    F2 (r5perms): ولا يَعرض مفتاحًا **لا يستطيع الفاعلُ منحَه**. سقفُ
    التفويض (`owner.assert_role_within_actor`) يرفض أيَّ حفظٍ يحمل مفتاحًا
    لا يملكه الفاعل، فكان حاملُ `admins.edit` يرى ٦٦ مربّعًا ويضغط «إنشاء
    الدور» فيعود بفلاشٍ «لا يمكنك منح صلاحيات لا تملكها» ولا يُنشأ شيء —
    وهو عنصرٌ مرئيٌّ يرفضه الخادم.

    عند **تعديل** دورٍ قائم نُبقي مفاتيحَه الحاليّةَ معروضةً حتى لو كانت
    خارج سقفِ الفاعل: حجبُها يُسقطها صامتةً من الحفظِ التالي (خفضُ صلاحيّةٍ
    بلا طلب). الخادمُ يَرفض ذلك الحفظَ أصلًا، والنموذجُ يُعطَّل عرضًا."""
    from ..core.constants import EDITABLE_PERMISSIONS
    aid = _actor_id()
    try:
        from ..auth.owner import _perms_of, is_owner_like_id
        if aid is None or is_owner_like_id(aid):
            return EDITABLE_PERMISSIONS
        mine = set(_perms_of(int(aid)))   # نفسُ مصدرِ سقفِ التفويض
    except Exception:  # noqa: BLE001 — fail-open: الخادمُ هو الحَكَم
        return EDITABLE_PERMISSIONS
    keep = set(mine)
    if role is not None:
        try:
            keep |= set(getattr(role, "permissions", None) or ())
        except Exception:  # noqa: BLE001
            pass
    return tuple(p for p in EDITABLE_PERMISSIONS if p in keep)


def admins_list():
    svc = get_admins_service()
    admins = _scoped(svc.list_admins())
    roles_seq = svc.list_roles()
    roles = {r.id: r for r in roles_seq}
    return render_template(
        "radius/admins_list.html", admins=admins, roles=roles,
        # قائمة الأدوار كما هي (ترتيبًا) لقائمة «الدور» داخل صندوق «إضافة مدير» العائم
        roles_all=_roles_for_actor(roles_seq),
        can_grant_owner=_can_grant_owner(),
        # ?new=1 يفتح الصندوق العائم «إضافة مدير» تلقائيًا (الرابط القديم /admins/new يبقى حيًّا)
        open_new_modal=(request.args.get("new") == "1"),
    )


def admins_new():
    # نموذج الإنشاء أصبح صندوقًا عائمًا داخل صفحة القائمة —
    # الرابط القديم يبقى حيًّا ويفتح النافذة تلقائيًا عبر ?new=1.
    return redirect(url_for("radius.admins_list", new=1))


def _s(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _can_grant_owner() -> bool:
    from ..auth.owner import is_owner_like
    return is_owner_like()


def admins_create():
    svc = get_admins_service()
    from ..auth.owner import OwnerGuardError, assert_role_within_actor
    want_super = bool(request.form.get("is_super_user"))
    want_co = bool(request.form.get("is_co_owner"))
    try:
        role_id = int(request.form.get("role_id") or 0) or None
    except (TypeError, ValueError):
        role_id = -1
    if want_super:
        role_id = _super_role_id()
    # دورٌ مجهول يُرفَض (422) — لا يُنشأ مديرٌ بدورٍ معلَّق. بلا دور = الأقلّ
    # صلاحيةً (services.admins.create_admin)، أبدًا لا «مدير عام».
    if role_id is not None and (role_id <= 0 or admins_repo.get_role(int(role_id)) is None):
        flash(_tr("الدور المحدد غير موجود — اختر دورًا للمدير."), "error")
        return render_template("radius/admins_form.html",
            admin=None, roles=svc.list_roles(), is_new=True), 422
    try:
        if (want_super or want_co) and not _can_grant_owner():
            raise OwnerGuardError(
                _tr("منح صلاحيات المالك (شريك) أو «سوبر يوزر» مقصورٌ على المالك أو الشريك."))
        if role_id:
            probe = type("A", (), {"role_id": role_id})()
            assert_role_within_actor(_actor_id(), admins_repo.admin_permissions(probe))
    except OwnerGuardError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.admins_list"))
    try:
        a = svc.create_admin(
            actor=_actor(),
            username=_s("username"),
            password=_s("password"),
            full_name=_s("full_name"),
            email=_s("email"),
            mobile=_s("mobile"),
            role_id=role_id,
            enabled=bool(request.form.get("enabled")),
            # RM-H6: profile fields (passed via repo since service signature may not accept)
        )
        # update profile fields via repo (service.create_admin doesn't take them)
        profile = {
            "phone":         _s("phone"),
            "profile_notes": _s("profile_notes"),
            "tags":          _s("tags"),
        }
        if any(profile.values()):
            try: admins_repo.update_admin(a.id, **profile)
            except Exception: pass
        if want_super:
            admins_repo.update_admin(a.id, is_super_admin=True)
        if want_co:
            admins_repo.set_co_owner(a.id, True)
        _join_creator_tenant(a)
    except (ValueError, RadiusError) as e:
        flash(str(e), "error")
        return render_template("radius/admins_form.html",
            admin=None, roles=svc.list_roles(), is_new=True), 400
    # admins-report v2 — post-CRUD trigger: notify the panel immediately so
    # the new admin appears there without waiting for the periodic worker.
    _notify_panel_of_admin_change()
    flash(_tr('تم إنشاء المدير «%(username)s».', username=a.username), "success")
    return redirect(url_for("radius.admins_list"))


def admins_edit(admin_id: int):
    svc = get_admins_service()
    a = svc.get_admin(admin_id)
    if not a: abort(404)
    _require_in_scope(admin_id)
    return render_template("radius/admins_form.html", admin=a,
                           roles=_roles_for_actor(svc.list_roles()), is_new=False,
                           can_grant_owner=_can_grant_owner(),
                           admin_is_super_role=_is_super_role(a.role_id),
                           target_is_original_owner=admins_repo.is_original_owner(a.id))


def admins_update(admin_id: int):
    _require_in_scope(admin_id)
    svc = get_admins_service()
    changes = {}
    for k in ("full_name","email","mobile",
              # RM-H6 profile fields
              # (avatar_url أُزيل — قرار المالك 2026-10-06: لا شيء يعرضه)
              "phone","profile_notes","tags"):
        v = request.form.get(k)
        if v is not None: changes[k] = v.strip()
    if request.form.get("role_id"):
        try: changes["role_id"] = int(request.form["role_id"])
        except (TypeError, ValueError): pass
    # fix3 (F01 F15): only a form that carried the toggle changes «enabled» —
    # a crafted POST without it silently disabled the target admin.
    if "enabled" in request.form or request.form.get("enabled_present"):
        changes["enabled"] = bool(request.form.get("enabled"))
    password = (request.form.get("password") or "").strip()
    # تطبيق profile fields عبر repo مباشرة لتجنب تقييد الـ service
    profile_keys = ("phone","profile_notes","tags")
    profile_changes = {k: changes.pop(k) for k in list(changes) if k in profile_keys}

    # ── D12: مستوى الوصول (سوبر يوزر / شريك) + حماية المالك الأصليّ ──
    existing = admins_repo.get_admin(admin_id)
    if existing is None:
        abort(404)
    super_change = co_change = False
    want_co = bool(getattr(existing, "is_co_owner", False))
    if request.form.get("access_level_present"):
        want_super = bool(request.form.get("is_super_user"))
        cur_super = _is_super_role(existing.role_id)
        if want_super != cur_super:
            super_change = True
            if want_super:
                changes["role_id"] = _super_role_id()
            elif changes.get("role_id") in (None, _super_role_id()):
                changes["role_id"] = admins_repo.least_privileged_role_id()
            changes["is_super_admin"] = want_super
        if not admins_repo.is_original_owner(admin_id):
            want_co = bool(request.form.get("is_co_owner"))
            co_change = want_co != bool(getattr(existing, "is_co_owner", False))
    from ..auth.owner import OwnerGuardError, assert_can_modify_admin
    try:
        new_role = changes.get("role_id")
        assert_can_modify_admin(
            _actor_id(), admin_id,
            new_role_id=new_role if new_role != existing.role_id else None,
            co_owner_change=co_change, super_change=super_change)
    except OwnerGuardError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.admins_list"))

    # ── Per-manager monetary credit caps — SUPER-ADMIN ONLY (server-side).
    # The section is hidden for non-supers; if one POSTs the caps anyway → 403.
    from ..auth.session_helpers import is_super_admin
    cap_changes: dict = {}
    if request.form.get("credit_caps_present"):
        if not is_super_admin():
            abort(403)
        from ..services.business_os_finance import money_to_minor
        cap_changes = {
            "debt_cap_enabled": bool(request.form.get("debt_cap_enabled")),
            "debt_cap_minor": money_to_minor(request.form.get("debt_cap_amount") or 0),
            "loan_cap_enabled": bool(request.form.get("loan_cap_enabled")),
            "loan_cap_minor": money_to_minor(request.form.get("loan_cap_amount") or 0),
        }
    try:
        svc.update_admin(actor=_actor(), admin_id=admin_id,
                         password=password or None, **changes)
        if profile_changes:
            try: admins_repo.update_admin(admin_id, **profile_changes)
            except Exception: pass
        if cap_changes:
            admins_repo.update_admin(admin_id, **cap_changes)
        if co_change:
            admins_repo.set_co_owner(admin_id, want_co)
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error"); return redirect(url_for("radius.admins_list"))
    # admins-report v2 — post-CRUD trigger for edit/deactivate.
    _notify_panel_of_admin_change()
    flash(_tr("تم التحديث."), "success")
    return redirect(url_for("radius.admins_list"))


def admins_delete(admin_id: int):
    _require_in_scope(admin_id)
    from ..auth.owner import OwnerGuardError, assert_can_modify_admin
    try:
        assert_can_modify_admin(_actor_id(), admin_id, deleting=True)
        if _actor_id() == int(admin_id):
            raise OwnerGuardError(_tr("لا يمكنك حذف حسابك أنت."))
    except OwnerGuardError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.admins_list"))
    try:
        get_admins_service().delete_admin(actor=_actor(), admin_id=admin_id)
        # admins-report v2 — differential tombstone right after the delete;
        # the next periodic full-snapshot reconciles the full roster anyway.
        _notify_panel_of_admin_change(deleted_admin_id=int(admin_id))
        flash(_tr("تمت أرشفة المدير. يمكنك استعادته من سلة المحذوفات."), "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.admins_list"))


# ─────────────── roles ───────────────

def roles_list():
    svc = get_admins_service()
    roles = svc.list_roles()
    perms = svc.all_permissions()
    return render_template("radius/roles_list.html", roles=roles, perms=perms)


def roles_update(role_id: int):
    """legacy: permissions-only update."""
    refused = _reject_unknown_permissions(url_for("radius.roles_list"))
    if refused is not None:
        return refused
    svc = get_admins_service()
    _existing = admins_repo.get_role(role_id)
    chosen = _merge_role_permissions(_existing, request.form.getlist("permissions"))
    try:
        from ..auth.owner import assert_role_within_actor
        assert_role_within_actor(_actor_id(), request.form.getlist("permissions"))
        svc.update_role_permissions(actor=_actor(), role_id=role_id, perms=chosen)
        flash(_tr("تم تحديث الصلاحيات."), "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.roles_list"))


# ════════════════════════════════════════════════════════════════
# RM-H6: roles CRUD + admins profile-summary
# ════════════════════════════════════════════════════════════════

def admins_profile_summary():
    """صفحة ملخّص قراءة فقط لكل المدراء."""
    svc = get_admins_service()
    admins = _scoped(svc.list_admins())
    roles = {r.id: r for r in svc.list_roles()}
    perms = svc.all_permissions()
    total = len(admins)
    active_count = sum(1 for a in admins if a.enabled)
    with_role = sum(1 for a in admins if a.role_id)
    with_login = sum(1 for a in admins if a.last_login_at)
    return render_template("radius/admins_profile_summary.html",
        admins=admins, roles=roles, perms_count=len(perms),
        stats={"total": total, "active": active_count,
                "with_role": with_role, "with_login": with_login})


def _permission_groups(perms):
    """يصنّف permissions حسب prefix (e.g. 'users.*', 'plans.*')."""
    from collections import defaultdict
    groups = defaultdict(list)
    for p in perms:
        prefix = p.split(".", 1)[0] if "." in p else "general"
        groups[prefix].append(p)
    return dict(sorted(groups.items()))


def roles_new():
    perms = _form_perms()
    return render_template("radius/roles_form.html",
        role=None, perms=perms, is_new=True,
        groups=_permission_groups(perms))


def _unknown_permission_keys() -> list[str]:
    """fix3 (F02 M2): keys the role form posted that are not real permissions."""
    from ..core.constants import ALL_PERMISSIONS
    known = set(ALL_PERMISSIONS)
    return sorted({p for p in request.form.getlist("permissions") if p not in known})


def _merge_role_permissions(role, submitted) -> tuple[str, ...]:
    """SEC r6perms (D01): المحرّر يَعرض المفاتيحَ الحيّةَ فقط
    (``EDITABLE_PERMISSIONS``)، فحفظُ الدور كان يَستبدل مجموعتَه كلَّها بما
    أرسلَته الشبكةُ من مربّعاتٍ محرَّرة — فتُسقَط صامتةً كلُّ صلاحيّةٍ **غير
    محرَّرة** يحملها الدور (``dashboard.view`` و14 مفتاحًا مُهمَلًا). أيُّ حفظٍ
    لصفحةِ الدور — ولو بلا أيِّ تغيير — كان يَمسح ``dashboard.view`` من الدور
    (مؤكَّدٌ: 164 دورًا في مسح r6perms). نُبقي مفاتيحَ الدورِ القائمةَ التي لا
    يُمثّلها المحرّرُ، فيَحكم النموذجُ المفاتيحَ المحرَّرةَ وحدَها ولا يُسقِط ما
    لا يَعرِضه. (لا يَمنح شيئًا جديدًا: الإبقاءُ على ما كان فقط.)"""
    from ..core.constants import EDITABLE_PERMISSIONS
    editable = set(EDITABLE_PERMISSIONS)
    existing = set(getattr(role, "permissions", None) or ())
    preserved = existing - editable      # مفاتيحُ الدورِ خارجَ شبكةِ المحرّر
    return tuple(dict.fromkeys(list(submitted) + sorted(preserved)))


def _reject_unknown_permissions(back_url: str):
    """422 in Arabic (like POST /api/v1/roles) — nothing is stored."""
    bad = _unknown_permission_keys()
    if not bad:
        return None
    from .status_notice import status_notice
    return status_notice(
        422, _tr("لم يُحفَظ الدور"),
        _tr("صلاحيات غير معروفة: ") + "، ".join(bad) + _tr(" — لم يُحفَظ شيء."),
        back_url=back_url, back_label=N_("رجوع إلى الدور"), code="unknown_permission",
        unknown=bad)


def roles_create():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash(_tr("اسم الدور مطلوب."), "error")
        return redirect(url_for("radius.roles_new"))
    refused = _reject_unknown_permissions(url_for("radius.roles_new"))
    if refused is not None:
        return refused
    try:
        from ..auth.owner import assert_role_within_actor
        assert_role_within_actor(_actor_id(), request.form.getlist("permissions"))
        admins_repo.create_role(
            name=name,
            display_name=(request.form.get("display_name") or "").strip() or name,
            description=(request.form.get("description") or "").strip(),
            permissions=tuple(request.form.getlist("permissions")),
            color=(request.form.get("color") or "#2BAACC").strip(),
        )
        flash(_tr('تم إنشاء الدور «%(name)s» ✓', name=name), "success")
        return redirect(url_for("radius.roles_list"))
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
        return redirect(url_for("radius.roles_new"))


def _role_grants_context(role_id: int) -> dict:
    """Build the «actions + visibility + section access» view-data for a role.

    Shared by the merged edit page so the grants matrix renders inline in the
    same template. Mirrors what the (now redirected) standalone grants page
    used to compute.
    """
    from ..services import manager_grants as _mg
    blob = admins_repo.get_role_granular(role_id)
    flags = blob.get("flags") if isinstance(blob.get("flags"), dict) else {}
    # D09: «رؤية كل المشتركين/الحزم» على الدور = مفتاح RBAC في المصفوفة أعلاه
    # (مربّعٌ واحد) — لا يُكرَّر هنا.
    scope_flags = [
        {"key": k, "label": lbl, "checked": bool(flags.get(k))}
        for k, lbl in _mg.SCOPE_FLAG_REGISTRY.items()
        if k not in _mg.ROLE_RBAC_SCOPE_FLAGS
    ]
    # تقسيم الأفعال المُشتقّة من المفاتيح («حسب المفتاح/مسموح/ممنوع») — مثل
    # «تأكيد الإيداع نعم / السحب لا» لكل مدراء الدور. دور «مدير عام» = كل
    # الصلاحيات غير المالكيّة فلا تقسيم عليه (أساسه مركَّب).
    role = admins_repo.get_role(role_id)
    role_super = bool(admins_repo.role_is_super(role))
    return {
        "action_catalog": _mg.role_action_catalog(blob),
        "scope_flags": scope_flags,
        "section_catalog": _mg.role_section_catalog(blob),
        "derived_catalog": ([] if role_super else _mg.role_derived_catalog(
            blob, getattr(role, "permissions", ()) or ())),
        "role_is_super": role_super,
    }


def roles_edit(role_id: int):
    """Merged role page: basic identity + permissions matrix AND the role's
    inherited actions/visibility/section-access grants — one comprehensive
    page. The standalone /grants page now redirects here."""
    r = admins_repo.get_role(role_id)
    if not r: abort(404)
    perms = _form_perms(role=r)
    return render_template("radius/roles_form.html",
        role=r, perms=perms, is_new=False,
        # F2 (r5perms): دورٌ يحمل مفاتيحَ خارجَ سقفِ تفويضِ الفاعل لا يُحفَظ
        # أبدًا (`owner.assert_role_within_actor` يَرفض كلَّ حفظٍ عليه)، فلا
        # يُقدَّم للفاعلِ نموذجٌ محرَّرٌ وزرُّ حفظٍ يعود بفلاشِ رفض.
        save_blocked_by_delegation=_role_beyond_actor(r),
        groups=_permission_groups(perms),
        **_role_grants_context(role_id))


def roles_save(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r: abort(404)
    refused = _reject_unknown_permissions(url_for("radius.roles_edit", role_id=role_id))
    if refused is not None:
        return refused
    try:
        from ..auth.owner import assert_role_within_actor
        assert_role_within_actor(_actor_id(), request.form.getlist("permissions"))
        admins_repo.update_role(
            role_id,
            display_name=(request.form.get("display_name") or "").strip() or r.name,
            description=(request.form.get("description") or "").strip(),
            # SEC r6perms (D01): نُبقي مفاتيحَ الدورِ غيرَ المحرَّرة (التي لا
            # يَعرِضها المحرّر) بدل مسحِها صامتًا عند كلِّ حفظ.
            permissions=_merge_role_permissions(r, request.form.getlist("permissions")),
            color=(request.form.get("color") or "#2BAACC").strip(),
        )
        flash(_tr("تم حفظ التعديلات ✓"), "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    # Stay on the merged page after saving basic-info/permissions.
    return redirect(url_for("radius.roles_edit", role_id=role_id))


def roles_delete(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r: abort(404)
    if r.is_system:
        flash(_tr("لا يمكن أرشفة دور النظام."), "error")
        return redirect(url_for("radius.roles_list"))
    # D22: دورٌ مُسنَد لمدراء لا يُحذف بصمت (كانوا يفقدون كل صلاحياتهم).
    in_use = admins_repo.role_usage_count(role_id)
    if in_use:
        # F02 L2 / D22: a real Arabic 409 page/JSON — not «Redirecting…».
        from .status_notice import status_notice
        return status_notice(
            409, _tr("لا يمكن حذف الدور"),
            _tr('لا يمكن حذف الدور «%(name)s»: مُسنَد إلى %(in_use)s مدير. انقلهم إلى دورٍ آخر أوّلًا.', name=r.display_name or r.name, in_use=in_use),
            back_url=url_for("radius.roles_list"), back_label=N_("قائمة الأدوار"),
            code="role_in_use", admins_count=int(in_use))
    try:
        admins_repo.delete_role(role_id)
        flash(_tr('تمت أرشفة الدور «%(name)s» ✓', name=r.name), "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.roles_list"))


def roles_grants(role_id: int):
    """Legacy standalone grants URL — merged into the role edit page.

    Kept as a permanent 302 so old links/bookmarks keep working; the grants
    matrix now renders inline on radius.roles_edit under the #role-grants
    anchor. RBAC on this endpoint is unchanged (super-admin only)."""
    if not admins_repo.get_role(role_id):
        abort(404)
    return redirect(url_for("radius.roles_edit", role_id=role_id,
                            _anchor="role-grants"), code=302)


def roles_grants_save(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r:
        abort(404)
    from ..services import manager_grants as _mg
    try:
        blob = _mg.parse_grants_form(request.form)
        # «حسب المفتاح / مسموح / ممنوع» للأفعال المُشتقّة (tri_<key>) — غير
        # المُرسَل يحتفظ بقيمته القائمة (نموذج قديم لا يمسح التقسيم).
        blob = _mg.apply_role_derived_form(
            blob, request.form, existing=admins_repo.get_role_granular(role_id))
        admins_repo.set_role_granular(role_id, blob)
        flash(_tr('تم حفظ أساس صلاحيات الدور «%(name)s» — يَرثه كلّ مدير بهذا الدور ✓', name=r.display_name or r.name), "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    # Return to the merged page, anchored on the grants section.
    return redirect(url_for("radius.roles_edit", role_id=role_id,
                            _anchor="role-grants"))
