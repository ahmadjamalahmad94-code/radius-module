"""Users (subscribers) routes — CRUD + extras.

RM-H1: extended with full AdvRadius fields.
Hybrid storage:
  - الحقول الـ queryable كأعمدة DB حقيقية (subscribers.* — انظر migration 011)
  - الحقول المتقدمة (MikroTik attrs, vendor-specific) في metadata JSON مُجمَّع
    {mikrotik:{}, radius:{}, advanced:{}, notifications:{}}
"""
from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import ACCOUNT_STATUSES, USER_TYPES
from ..core.errors import RadiusError
from ..core.types import Subscriber
from ..services.plans import get_plans_service
from ..services.users import get_users_service
from .speed_rules_ui import handle_embedded_speed_rule, speed_rules_panel


# ════════════════════════════════════════════════════════════════
# RM-H1: metadata structure (نفس بنية HobeHub لتسهيل المقارنة)
# ════════════════════════════════════════════════════════════════
_META_GROUPS = {
    "mikrotik": [
        "mikrotik_filter_chain",
        "mikrotik_address_list",
        "mikrotik_framed_route",
        "mikrotik_user_group",
        "mikrotik_winbox_group",
        "mikrotik_queue_priority",
    ],
    "radius": [
        "framed_pool",
        "ppp_attributes_extra",
        "acct_interim_interval_sec",
    ],
    "advanced": [
        "temporary_speed_from",
        "temporary_speed_to",
    ],
    "notifications": [
        # placeholders للمرحلة القادمة
    ],
}
_META_FIELDS = [f for g in _META_GROUPS.values() for f in g]


def _grouped_to_flat(grouped: dict) -> dict:
    out = {}
    for grp in (grouped or {}).values():
        if isinstance(grp, dict):
            out.update(grp)
    return out


def _flat_to_grouped(flat: dict) -> dict:
    grouped = {g: {} for g in _META_GROUPS}
    for grp, fields in _META_GROUPS.items():
        for f in fields:
            v = flat.get(f, "")
            if v not in (None, ""):
                grouped[grp][f] = v
    return grouped


def _parse_metadata(raw: str | dict | None) -> dict:
    """يحوّل metadata من DB (str JSON) إلى dict مُجمَّع. fallback آمن."""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}") or {}
        except (ValueError, TypeError):
            data = {}
    for g in _META_GROUPS:
        data.setdefault(g, {})
    return data


def register_users_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/users", "users_list", users_list, methods=["GET"])
    bp.add_url_rule("/users/new", "users_new", users_new, methods=["GET"])
    bp.add_url_rule("/users", "users_create", users_create, methods=["POST"])
    bp.add_url_rule("/users/<username>/profile", "users_profile", users_profile, methods=["GET"])
    bp.add_url_rule("/users/<username>/edit", "users_edit", users_edit, methods=["GET"])
    bp.add_url_rule("/users/<username>", "users_update", users_update, methods=["POST"])
    bp.add_url_rule("/users/<username>/delete", "users_delete", users_delete, methods=["POST"])
    bp.add_url_rule("/users/<username>/toggle", "users_toggle", users_toggle, methods=["POST"])
    bp.add_url_rule("/users/<username>/extend", "users_extend", users_extend, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _normalize_connection_schedule(raw: str) -> str:
    """Round-trip the schedule JSON via access_schedule.serialize so we
    store the canonical, validated form (or "" for empty)."""
    if not raw:
        return ""
    try:
        from ..core.access_schedule import serialize
        return serialize(raw)
    except Exception:  # noqa: BLE001
        return ""


def _derive_working_days_from_form() -> str:
    """Compute the working_days CSV cache from the submitted schedule JSON."""
    raw = (request.form.get("connection_schedule") or "").strip()
    if not raw:
        return ""
    try:
        from ..core.access_schedule import derive_working_days
        return derive_working_days(raw)
    except Exception:  # noqa: BLE001
        return ""


def _form_dto(*, sub_id: int | None = None) -> Subscriber:
    """يجمع كل حقول الـ Subscriber form (الأساسية + RM-H1 الموسَّعة + metadata)."""
    def _i(n, d=0):
        try: return int(request.form.get(n) or d)
        except (TypeError, ValueError): return d
    def _b(n):
        return request.form.get(n, "") in ("1", "on", "true", "yes")
    def _s(n):
        return (request.form.get(n) or "").strip()

    plan_id = request.form.get("plan_id")
    manager_id = request.form.get("manager_id")

    # service_type — multi-checkbox (hotspot + pppoe). Falls back to the
    # legacy single field for back-compat with old POSTs.
    svc_types = request.form.getlist("service_type")
    has_hs   = "hotspot" in svc_types or "Hotspot" in svc_types
    has_pppoe = "pppoe" in svc_types
    if has_hs and has_pppoe:
        service_type = "both"
    elif has_pppoe:
        service_type = "pppoe"
    elif has_hs:
        service_type = "hotspot"
    else:
        # legacy single-select fallback
        service_type = _s("service_type") or "hotspot"

    # metadata: نجمع الحقول المسطّحة من الـ form ثم نُجمّعها
    flat_meta = {}
    for mf in _META_FIELDS:
        v = _s(mf)
        if v:
            flat_meta[mf] = v
    meta_json = json.dumps(_flat_to_grouped(flat_meta), ensure_ascii=False)

    return Subscriber(
        id=sub_id,
        # حساب الإنترنت أساسي — user_type is always "subscriber" on this
        # form (the subscribers form is subscribers-only; cards have their
        # own batch flow).
        username=_s("username"),
        password=_s("password"),
        user_type="subscriber",
        service_type=service_type,
        plan_id=int(plan_id) if plan_id else None,
        manager_id=int(manager_id) if manager_id else None,
        group=_s("group"),
        pool=_s("pool"),
        status=_s("status") or "enabled",
        auto_renewal=_b("auto_renewal"),
        # PPPoE
        pppoe_username=_s("pppoe_username"),
        pppoe_password=_s("pppoe_password"),
        pppoe_ip=_s("pppoe_ip"),
        # شخصي
        full_name=_s("full_name"),
        father_name=_s("father_name"),
        mobile=_s("mobile"),
        email=_s("email"),
        national_id=_s("national_id"),
        nationality=_s("nationality"),
        country=_s("country"),
        city=_s("city"),
        district=_s("district"),
        state=_s("state"),
        zip=_s("zip"),
        address=_s("address"),
        payment_method=_s("payment_method"),
        payment_reference=_s("payment_reference"),
        # شبكة
        mac_lock=_s("mac_lock") or None,
        static_ip=_s("static_ip") or None,
        vlan_id=_i("vlan_id"),
        override_concurrent=_i("override_concurrent"),
        caller_id=_s("caller_id"),
        primary_dns_ppp=_s("primary_dns_ppp"),
        secondary_dns_ppp=_s("secondary_dns_ppp"),
        device_connection_file=_s("device_connection_file"),
        # سرعة (override per-user)
        bandwidth_control_enabled=_b("bandwidth_control_enabled"),
        download_speed_kbps=_i("download_speed_kbps"),
        upload_speed_kbps=_i("upload_speed_kbps"),
        custom_speed=_b("custom_speed"),
        temporary_speed=_b("temporary_speed"),
        # كوتا/وقت (override)
        total_connection_time_min=_i("total_connection_time_min"),
        daily_connection_time_min=_i("daily_connection_time_min"),
        download_quota_mb=_i("download_quota_mb"),
        upload_quota_mb=_i("upload_quota_mb"),
        combined_quota_mb=_i("combined_quota_mb"),
        connection_time_limit_enabled=_b("connection_time_limit_enabled"),
        quota_limit_enabled=_b("quota_limit_enabled"),
        equal_share_download=_b("equal_share_download"),
        equal_share_upload=_b("equal_share_upload"),
        # أيام + أجهزة + MACs — connection_schedule is the source of truth;
        # working_days is a derived CSV cache for legacy consumers.
        connection_schedule=_normalize_connection_schedule(_s("connection_schedule")),
        working_days=_derive_working_days_from_form(),
        device_count=_i("device_count", 1) or 1,
        allowed_macs=_s("allowed_macs"),
        # metadata JSON
        metadata=meta_json,
        # ربط — beneficiary_ref (HobeHub link) input was removed from the
        # visible form, but the template keeps it as a hidden field so
        # the existing value round-trips on edit and is empty on create.
        beneficiary_ref=_s("beneficiary_ref"),
        remark=_s("remark"),
    )


def _sub_with_meta_for_template(sub: Subscriber) -> dict:
    """يحوّل sub إلى dict + يسطّح metadata للوصول البسيط من القالب."""
    from dataclasses import asdict
    d = asdict(sub)
    grouped = _parse_metadata(sub.metadata)
    flat = _grouped_to_flat(grouped)
    for f in _META_FIELDS:
        d.setdefault(f, flat.get(f, ""))
    return d


# ─────────────── views ───────────────

def users_list():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip() or None
    plan_id = request.args.get("plan_id")
    plan_id = int(plan_id) if plan_id else None
    items = get_users_service().list(status=status, plan_id=plan_id, search=q, limit=1000)
    plans = list(get_plans_service().list(limit=500))

    # DHCP fingerprints (migration 026) — bulk look-up by mac_lock for
    # the subscribers on this page. Renders the device name/OS in a new
    # column next to the username. Subscribers without a mac_lock get
    # a dash. We use mac_lock (not the latest observed MAC) so the data
    # is deterministic and doesn't churn between renders.
    dhcp_by_username = {}
    try:
        from ..db.repos import device_fingerprints_repo
        tid = _tid()
        macs = [u.mac_lock for u in items if getattr(u, "mac_lock", None)]
        if macs:
            fp_by_mac = device_fingerprints_repo.get_many_by_macs(tid, macs)
            for u in items:
                m = (getattr(u, "mac_lock", "") or "").lower()
                if m and m in fp_by_mac:
                    dhcp_by_username[u.username] = fp_by_mac[m]
    except Exception:  # noqa: BLE001
        # Never break the subscribers list because of fingerprint lookup.
        dhcp_by_username = {}

    return render_template("radius/users_list.html",
        items=items, plans=plans, q=q, status=status, plan_id=plan_id,
        statuses=ACCOUNT_STATUSES,
        dhcp_by_username=dhcp_by_username)


def _form_select_options() -> dict:
    """Admins + subscriber_groups for the form dropdowns. Both wrapped so
    a broken sub-repo never breaks the form render. See SERVICES_COOKBOOK §16."""
    tid = _tid()
    try:
        from ..db.repos import admins_repo
        # admins are global (not tenant-scoped) in this codebase.
        admins = [a for a in admins_repo.list_admins()
                  if getattr(a, "status", "active") == "active"]
    except Exception:  # noqa: BLE001
        admins = []
    try:
        from ..db.repos import subscriber_groups_repo
        sgroups = subscriber_groups_repo.list_groups(tid)
    except Exception:  # noqa: BLE001
        sgroups = []
    return {"admins": admins, "subscriber_groups": sgroups}


def _new_subscriber_speed_panel():
    """Empty-list panel shown on the «add new subscriber» page so the
    operator can compose a first rule alongside the subscriber.
    subscriber_username="" is the trigger for new-mode rendering."""
    return {
        "target_type": "subscriber",
        "plan_id": None,
        "subscriber_username": "",
        "card_batch_id": None,
        "subscriber_group_id": None,
        "return_to": request.path if request else "",
        "title": "قواعد السرعة",
        "help_text": (
            "اختياري — أضيفي قاعدة سرعة مجدولة هنا وستُحفظ تلقائيًا "
            "مع المشترك عند الضغط على «حفظ المشترك» أسفل الصفحة."
        ),
        "rules": [],
        "presets": [],
    }


def users_new():
    plans = list(get_plans_service().list(limit=500))
    empty = Subscriber(id=None, username="", password="", status="enabled")
    return render_template("radius/users_form.html",
        sub=_sub_with_meta_for_template(empty),
        plans=plans, statuses=ACCOUNT_STATUSES, user_types=USER_TYPES,
        is_new=True, speed_rules_panel=_new_subscriber_speed_panel(),
        **_form_select_options())


def users_create():
    dto = _form_dto()
    try:
        saved = get_users_service().create(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(e.message, "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=True,
            speed_rules_panel=_new_subscriber_speed_panel(),
            **_form_select_options()), 400

    # Inline first speed-rule (optional): if the form has rule fields
    # filled, create it now that the subscriber row exists. We bypass
    # handle_embedded_speed_rule because it requires _speed_rule_action
    # — here the operator clicked the main «حفظ» button, not a panel one.
    if (request.form.get("sr_starts_at_time") or "").strip():
        try:
            from ..services.operations import get_operations_service
            from .speed_rules_ui import _days_from_form
            get_operations_service().create_bandwidth_schedule(
                tenant_id=_tid(), actor=_actor(),
                data={
                    "target_type": "subscriber",
                    "subscriber_username": saved.username,
                    "name": (request.form.get("sr_name") or "قاعدة سرعة").strip(),
                    "starts_at_time": request.form.get("sr_starts_at_time"),
                    "ends_at_time": request.form.get("sr_ends_at_time"),
                    "days_csv": _days_from_form(request.form, "sr_days"),
                    "speed_down_kbps": request.form.get("sr_speed_down_kbps") or 0,
                    "speed_up_kbps":   request.form.get("sr_speed_up_kbps") or 0,
                    "restore_mode": (request.form.get("sr_restore_mode")
                                     or "profile_default"),
                    "priority": request.form.get("sr_priority") or 100,
                    "notes": request.form.get("sr_notes") or "",
                    "metadata": {"embedded_target": "subscriber",
                                 "created_with_subscriber": True},
                },
            )
        except RadiusError as e:
            flash(
                f"تم إنشاء المشترك لكن قاعدة السرعة فشلت: {e.message}",
                "warning",
            )

    flash(f"تم إنشاء المستخدم «{saved.username}».", "success")
    return redirect(url_for("radius.users_list"))


def users_profile(username: str):
    """Subscriber 360° view — premium read-mostly profile page.

    Gathers every public-facing data slice for one subscriber and
    hands it to the template. The template owns presentation
    (tabs, hero, KPIs); this function owns DATA aggregation only.

    All queries are READ-ONLY. Mutating actions on this page go
    through existing routes (users_toggle / users_extend / users_delete
    / cards.disconnect / etc.) — see SERVICES_COOKBOOK §14.
    """
    from ..db.connection import db
    from ..db.repos import (
        accounting_repo, audit_repo, cards_repo, invoices_repo, plans_repo,
        subscribers_repo,
    )

    tid = _tid()
    sub_obj = subscribers_repo.get_subscriber(tid, username)
    if not sub_obj:
        abort(404)

    plan = plans_repo.get_plan(tid, sub_obj.plan_id) if sub_obj.plan_id else None

    # ── 1. Sessions — same query the Card Checker uses for cards;
    #    callingstationid + nasporttype + bytes give us the full row.
    try:
        session_rows = cards_repo.list_card_accounting(tid, username, limit=200)
    except Exception:
        session_rows = []

    # ── 2. Audit events targeting this subscriber.
    #    audit_repo doesn't have a per-target filter yet — pull recent and
    #    filter in-memory (cheap for the typical 200-row window).
    try:
        all_events = audit_repo.recent(tid, limit=500)
        events = [
            e for e in all_events
            if (e.get("target_type") == "subscriber" and e.get("target_id") == username)
            or (e.get("target_type") == "card" and e.get("payload", {}).get("username") == username)
        ][:100]
    except Exception:
        events = []

    # Split: actions BY this user vs actions ON this user
    manager_events = [e for e in events if e.get("actor", "").lower() != username.lower()][:50]
    own_events     = [e for e in events if e.get("actor", "").lower() == username.lower()][:50]

    # ── 3. Invoices for this subscriber.
    try:
        invoices = invoices_repo.list_all(tid, limit=200)
        invoices = [i for i in invoices if (
            getattr(i, "subscriber_id", None) == sub_obj.id
            or getattr(i, "username", "") == username
        )][:50]
    except Exception:
        invoices = []

    # ── 4. Cards used by this subscriber.
    try:
        used_cards = db().execute(
            "SELECT id, username, password, batch_id, used, revoked, "
            "       expire_at, first_used_at, used_by_mac "
            "  FROM cards "
            " WHERE tenant_id = ? AND used_by_subscriber_id = ? "
            " ORDER BY first_used_at DESC LIMIT 50",
            (tid, sub_obj.id),
        ).fetchall()
        used_cards = [dict(r) for r in used_cards]
    except Exception:
        used_cards = []

    # ── 5. Payments + loans + ledger.
    try:
        payments = accounting_repo.list_payments(
            tid, subscriber_id=sub_obj.id, limit=50,
        )
    except Exception:
        payments = []
    try:
        loans = accounting_repo.list_loans(
            tid, subscriber_id=sub_obj.id, limit=50,
        )
    except Exception:
        loans = []

    # ── 6. Aggregates for the KPI strip.
    # Bytes used: sum of acctinputoctets + acctoutputoctets for THIS username.
    try:
        agg_row = db().execute(
            """SELECT COALESCE(SUM(acctinputoctets), 0)  AS dn,
                      COALESCE(SUM(acctoutputoctets), 0) AS up,
                      COALESCE(SUM(acctsessiontime), 0)  AS total_secs,
                      COUNT(*)                            AS n_sessions,
                      SUM(CASE WHEN acctstoptime IS NULL THEN 1 ELSE 0 END) AS online
                 FROM radacct
                WHERE tenant_id = ? AND username = ?""",
            (tid, username),
        ).fetchone()
        agg = dict(agg_row) if agg_row else {}
    except Exception:
        agg = {}

    # ── 7. Quota limits — prefer subscriber override, fall back to plan.
    quota_dn_mb = sub_obj.download_quota_mb or (plan.quota_total_mb if plan else 0) or 0
    quota_up_mb = sub_obj.upload_quota_mb or 0
    quota_total_mb = sub_obj.combined_quota_mb or (quota_dn_mb + quota_up_mb)
    used_bytes = (agg.get("dn") or 0) + (agg.get("up") or 0)
    used_mb    = used_bytes / (1024 * 1024)
    remaining_mb = max(0, quota_total_mb - used_mb) if quota_total_mb else 0

    speed_dn = sub_obj.download_speed_kbps or (plan.speed_down_kbps if plan else 0) or 0
    speed_up = sub_obj.upload_speed_kbps or (plan.speed_up_kbps   if plan else 0) or 0

    profile = {
        "agg":           agg,
        "quota_dn_mb":   quota_dn_mb,
        "quota_up_mb":   quota_up_mb,
        "quota_total_mb": quota_total_mb,
        "used_mb":       used_mb,
        "remaining_mb":  remaining_mb,
        "speed_dn":      speed_dn,
        "speed_up":      speed_up,
        "balance":       sub_obj.balance or 0,
        "online_now":    int(agg.get("online") or 0),
        "n_sessions":    int(agg.get("n_sessions") or 0),
        "total_secs":    int(agg.get("total_secs") or 0),
    }

    return render_template(
        "radius/users_profile.html",
        sub=sub_obj,
        plan=plan,
        profile=profile,
        session_rows=session_rows,
        events=events,
        manager_events=manager_events,
        own_events=own_events,
        invoices=invoices,
        used_cards=used_cards,
        payments=payments,
        loans=loans,
    )


def users_edit(username: str):
    try:
        sub = get_users_service().get(username)
    except RadiusError:
        abort(404)
    plans = list(get_plans_service().list(limit=500))
    return render_template("radius/users_form.html",
        sub=_sub_with_meta_for_template(sub),
        plans=plans, statuses=ACCOUNT_STATUSES,
        user_types=USER_TYPES,
        is_new=False,
        **_form_select_options(),
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="subscriber",
            plan_id=sub.plan_id,
            subscriber_username=username,
            return_to=request.path,
            title="قواعد سرعة هذا المشترك",
            help_text="هذه القواعد أعلى أولوية من قواعد حزمة البطاقات والعرض. استخدمها عندما تريد سرعة خاصة لهذا الحساب في أوقات محددة.",
        ))


def users_update(username: str):
    if request.form.get("_speed_rule_action"):
        try:
            sub = get_users_service().get(username)
            handle_embedded_speed_rule(
                tenant_id=_tid(),
                actor=_actor(),
                form=request.form,
                target_type="subscriber",
                plan_id=sub.plan_id,
                subscriber_username=username,
            )
            flash("تم تنفيذ إجراء قواعد السرعة لهذا المشترك.", "success")
        except RadiusError as e:
            flash(e.message, "error")
        return redirect(url_for("radius.users_edit", username=username))

    dto = _form_dto()
    # احرص أن الـ username لا يتغير عن المسار
    from dataclasses import replace
    dto = replace(dto, username=username)
    try:
        get_users_service().update(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(e.message, "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=False, speed_rules_panel=None), 400
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.users_list"))


def users_delete(username: str):
    try:
        get_users_service().delete(actor=_actor(), username=username)
        flash("تمت الأرشفة. يمكنك الاستعادة من سلة المحذوفات.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_toggle(username: str):
    try:
        u = get_users_service().get(username)
        if u.status == "enabled":
            get_users_service().disable(actor=_actor(), username=username)
            flash("تم التعطيل.", "warning")
        else:
            get_users_service().enable(actor=_actor(), username=username)
            flash("تم التفعيل.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_extend(username: str):
    minutes = request.form.get("minutes")
    try:
        m = int(minutes)
        get_users_service().extend_time(actor=_actor(), username=username, minutes=m)
        flash(f"تم تمديد الحساب {m} دقيقة.", "success")
    except (TypeError, ValueError):
        flash("قيمة دقائق غير صحيحة", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))
