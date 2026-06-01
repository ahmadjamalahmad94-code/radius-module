"""Users (subscribers) routes — CRUD + extras.

RM-H1: extended with full AdvRadius fields.
Hybrid storage:
  - الحقول الـ queryable كأعمدة DB حقيقية (subscribers.* — انظر migration 011)
  - الحقول المتقدمة (MikroTik attrs, vendor-specific) في metadata JSON مُجمَّع
    {mikrotik:{}, radius:{}, advanced:{}, notifications:{}}
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import ACCOUNT_STATUSES, USER_TYPES
from ..core.errors import RadiusError
from ..core.system_config import default_currency
from ..core.types import Subscriber
from ..services.plans import get_plans_service
from ..services.users import get_users_service
from .speed_rules_ui import create_staged_speed_rules, handle_embedded_speed_rule, speed_rules_panel


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
        "nas_ip_address",
        "nas_port_id",
        "service_name",
    ],
    "advanced": [
        "temporary_speed_from",
        "temporary_speed_to",
        "temporary_speed_duration_minutes",
        "temporary_download_speed_kbps",
        "temporary_upload_speed_kbps",
    ],
    "notifications": [
        # reserved for notification-related subscriber settings
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
    bp.add_url_rule("/subscribers", "subscribers_list", users_list, methods=["GET"])
    bp.add_url_rule("/users/new", "users_new", users_new, methods=["GET"])
    bp.add_url_rule("/users", "users_create", users_create, methods=["POST"])
    bp.add_url_rule("/users/<username>/profile", "users_profile", users_profile, methods=["GET"])
    bp.add_url_rule("/users/<username>/360", "users_360", users_360_by_username, methods=["GET"])
    bp.add_url_rule("/subscribers/<int:subscriber_id>", "subscriber_360", subscriber_360, methods=["GET"])
    bp.add_url_rule(
        "/subscribers/<int:subscriber_id>/renewal-preview",
        "subscriber_renewal_preview",
        subscriber_renewal_preview,
        methods=["POST"],
    )
    bp.add_url_rule("/users/<username>/edit", "users_edit", users_edit, methods=["GET"])
    bp.add_url_rule("/users/<username>", "users_update", users_update, methods=["POST"])
    bp.add_url_rule("/users/<username>/delete", "users_delete", users_delete, methods=["POST"])
    bp.add_url_rule("/users/bulk-delete", "users_bulk_delete", users_bulk_delete, methods=["POST"])
    bp.add_url_rule("/users/<username>/toggle", "users_toggle", users_toggle, methods=["POST"])
    bp.add_url_rule("/users/<username>/extend", "users_extend", users_extend, methods=["POST"])
    bp.add_url_rule("/users/<username>/change-plan", "users_change_plan", users_change_plan, methods=["POST"])
    bp.add_url_rule("/users/<username>/sms", "users_send_sms", users_send_sms, methods=["POST"])
    bp.add_url_rule(
        "/users/<username>/quota/reset-daily",
        "users_quota_reset_daily",
        users_quota_reset_daily,
        methods=["POST"],
    )
    bp.add_url_rule("/users/<username>/quota/topup", "users_quota_topup", users_quota_topup, methods=["POST"])
    bp.add_url_rule("/users/<username>/balance/add", "users_balance_add", users_balance_add, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _form_float(name: str, default: float = 0.0) -> float:
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    return float(raw)


def _default_country() -> str:
    from ..db.repos import tenants_repo

    tenant_id = _tid()
    for key in ("radius.default_country", "tenant.country", "company.country"):
        value = tenants_repo.get_setting(tenant_id, key, "").strip()
        if value:
            return value
    return ""


def _subscriber_login_macs(username: str, *, limit: int = 20) -> list[dict]:
    if not username:
        return []
    try:
        from ..db.connection import db

        rows = db().execute(
            """
            SELECT UPPER(callingstationid) AS mac,
                   COUNT(*) AS sessions,
                   MAX(COALESCE(acctupdatetime, acctstoptime, acctstarttime, '')) AS last_seen_at,
                   SUM(CASE WHEN acctstoptime IS NULL OR acctstoptime = '' THEN 1 ELSE 0 END) AS online_sessions
              FROM radacct
             WHERE tenant_id = ?
               AND username = ?
               AND COALESCE(TRIM(callingstationid), '') != ''
             GROUP BY UPPER(callingstationid)
             ORDER BY last_seen_at DESC, sessions DESC
             LIMIT ?
            """,
            (_tid(), username, int(limit)),
        ).fetchall()
        return [
            {
                "mac": row["mac"] or "",
                "sessions": int(row["sessions"] or 0),
                "last_seen_at": row["last_seen_at"] or "",
                "online_sessions": int(row["online_sessions"] or 0),
            }
            for row in rows
            if row["mac"]
        ]
    except Exception:  # noqa: BLE001
        return []


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
    def _f(n, d=0.0):
        try: return float(request.form.get(n) or d)
        except (TypeError, ValueError): return d

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
    if _b("temporary_speed"):
        started = flat_meta.get("temporary_speed_from") or datetime.utcnow().isoformat(timespec="seconds")
        flat_meta["temporary_speed_from"] = started
        if not flat_meta.get("temporary_speed_to"):
            try:
                duration = int(float(flat_meta.get("temporary_speed_duration_minutes") or 0))
            except (TypeError, ValueError):
                duration = 0
            if duration > 0:
                try:
                    start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if start_dt.tzinfo:
                        start_dt = start_dt.replace(tzinfo=None)
                except ValueError:
                    start_dt = datetime.utcnow()
                flat_meta["temporary_speed_to"] = (
                    start_dt + timedelta(minutes=duration)
                ).isoformat(timespec="seconds")
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
        # سعر مخصّص يتجاوز سعر الباقة (فارغ/0 = استخدم سعر الباقة)
        custom_price=_f("custom_price"),
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
        country=_s("country") or _default_country(),
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
    group_id_raw = (request.args.get("group_id") or "").strip()
    group_id = int(group_id_raw) if group_id_raw.isdigit() else None
    items = get_users_service().list(status=status, plan_id=plan_id, search=q, limit=1000)
    subscriber_groups = []
    selected_group = None
    if group_id:
        try:
            from ..db.repos import subscriber_groups_repo

            selected_group = subscriber_groups_repo.get(_tid(), group_id)
            member_names = set(subscriber_groups_repo.list_member_usernames(_tid(), group_id))
            items = [u for u in items if u.username in member_names]
            subscriber_groups = subscriber_groups_repo.list_groups(_tid())
        except Exception:  # noqa: BLE001
            selected_group = None
            subscriber_groups = []
    else:
        try:
            from ..db.repos import subscriber_groups_repo
            subscriber_groups = subscriber_groups_repo.list_groups(_tid())
        except Exception:  # noqa: BLE001
            subscriber_groups = []
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
        group_id=group_id, subscriber_groups=subscriber_groups,
        selected_group=selected_group,
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
        login_macs=[],
        default_country=_default_country(),
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
            login_macs=[],
            default_country=_default_country(),
            **_form_select_options()), 400

    # Inline first speed-rule (optional): if the form has rule fields
    # filled, create it now that the subscriber row exists. We bypass
    # handle_embedded_speed_rule because it requires _speed_rule_action
    # — here the operator clicked the main «حفظ» button, not a panel one.
    created_rules = 0
    try:
        created_rules = create_staged_speed_rules(
            tenant_id=_tid(),
            actor=_actor(),
            form=request.form,
            target_type="subscriber",
            plan_id=saved.plan_id,
            subscriber_username=saved.username,
            metadata={"created_with_subscriber": True},
        )
    except RadiusError as e:
        flash(
            f"تم إنشاء المشترك لكن إحدى قواعد السرعة فشلت: {e.message}",
            "warning",
        )
    if not created_rules and (request.form.get("sr_starts_at_time") or "").strip():
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

    try:
        from ..db.repos import device_fingerprints_repo
        from ..services.card_checker import _dhcp_device, _session, _utcnow

        session_views = [_session(row, _utcnow()) for row in session_rows]
        macs = [s["mac_address"] for s in session_views if s.get("mac_address")]
        fp_by_mac = device_fingerprints_repo.get_many_by_macs(tid, macs) if macs else {}
        for s in session_views:
            mac_key = (s.get("mac_address") or "").lower()
            s["dhcp_device"] = _dhcp_device(fp_by_mac.get(mac_key))
    except Exception:
        session_views = []
        for row in session_rows:
            online = not row.get("acctstoptime")
            session_views.append({
                "id": row.get("radacctid"),
                "session_id": row.get("acctsessionid") or "",
                "started_at": row.get("acctstarttime"),
                "updated_at": row.get("acctupdatetime"),
                "stopped_at": row.get("acctstoptime"),
                "online": online,
                "duration_seconds": row.get("acctsessiontime") or 0,
                "upload_bytes": row.get("acctinputoctets") or 0,
                "download_bytes": row.get("acctoutputoctets") or 0,
                "mac_address": row.get("callingstationid"),
                "ip_address": row.get("framedipaddress"),
                "nas_address": row.get("nasipaddress"),
                "nas_port": row.get("nasportid"),
                "nas_port_type": row.get("nasporttype"),
                "service_type": row.get("servicetype"),
                "framed_protocol": row.get("framedprotocol"),
                "dhcp_device": None,
            })

    try:
        session_summary = cards_repo.summarize_card_accounting(tid, username)
    except Exception:
        session_summary = {}

    try:
        daily_rows = db().execute(
            """
            SELECT substr(replace(COALESCE(acctstarttime, acctupdatetime, acctstoptime, ''), 'T', ' '), 1, 10) AS day,
                   COUNT(*) AS sessions_count,
                   SUM(CASE WHEN acctstoptime IS NULL THEN 1 ELSE 0 END) AS online_sessions,
                   COALESCE(SUM(acctsessiontime), 0) AS total_seconds,
                   COALESCE(SUM(acctinputoctets), 0) AS upload_bytes,
                   COALESCE(SUM(acctoutputoctets), 0) AS download_bytes
              FROM radacct
             WHERE tenant_id = ?
               AND username = ?
               AND COALESCE(acctstarttime, acctupdatetime, acctstoptime, '') != ''
             GROUP BY day
             ORDER BY day DESC
             LIMIT 14
            """,
            (tid, username),
        ).fetchall()
        daily_usage = [dict(r) for r in reversed(daily_rows)]
    except Exception:
        daily_usage = []
    daily_max_bytes = max(
        [((r.get("upload_bytes") or 0) + (r.get("download_bytes") or 0)) for r in daily_usage] or [0]
    )

    bandwidth_samples = []
    for s in session_views[:12]:
        duration = max(int(s.get("duration_seconds") or 0), 1)
        download_bytes = int(s.get("download_bytes") or 0)
        upload_bytes = int(s.get("upload_bytes") or 0)
        down_bps = int((download_bytes * 8) / duration)
        up_bps = int((upload_bytes * 8) / duration)
        bandwidth_samples.append({
            "label": s.get("started_at") or s.get("session_id") or "",
            "online": bool(s.get("online")),
            "mac": s.get("mac_address") or "",
            "ip": s.get("ip_address") or "",
            "device": (
                ((s.get("dhcp_device") or {}).get("label"))
                or ((s.get("device") or {}).get("label"))
                or ""
            ),
            "download_bps": down_bps,
            "upload_bps": up_bps,
            "total_bps": down_bps + up_bps,
        })
    bandwidth_max_bps = max([r.get("total_bps") or 0 for r in bandwidth_samples] or [0])
    bandwidth_current = next((r for r in bandwidth_samples if r.get("online")), bandwidth_samples[0] if bandwidth_samples else {})

    def _audit_payload(e: dict) -> dict:
        payload = e.get("payload") or e.get("_payload") or {}
        if isinstance(payload, dict):
            return payload
        try:
            return json.loads(e.get("payload_json") or "{}")
        except (TypeError, ValueError):
            return {}

    # ── 2. Audit events targeting this subscriber.
    #    audit_repo doesn't have a per-target filter yet — pull recent and
    #    filter in-memory (cheap for the typical 200-row window).
    try:
        all_events = audit_repo.recent(tid, limit=500)
        events = []
        for e in all_events:
            payload = _audit_payload(e)
            e["_payload"] = payload
            e["payload_display"] = " · ".join(
                f"{key}={value}" for key, value in payload.items()
                if key != "demo_profile_events"
            )
            if (
                (e.get("target_type") == "subscriber" and e.get("target_id") == username)
                or (e.get("target_type") == "card" and payload.get("username") == username)
            ):
                events.append(e)
            if len(events) >= 100:
                break
    except Exception:
        events = []

    # Split: actions BY this user vs actions ON this user
    manager_events = [e for e in events if e.get("actor", "").lower() != username.lower()][:50]
    own_events     = [e for e in events if e.get("actor", "").lower() == username.lower()][:50]

    def _audit_event_title(action: str) -> str:
        labels = {
            "create": "تم إنشاء الحساب",
            "update": "تم تعديل الحساب",
            "archive": "تم أرشفة الحساب",
            "enable": "تم تفعيل الحساب",
            "disable": "تم تعطيل الحساب",
            "reset_password": "تم تغيير كلمة المرور",
            "subscriber.daily_quota_reset": "استعادة الكوتة اليومية",
            "subscriber.quota_topup": "إضافة كوتة",
            "subscriber.cash_balance_add": "إضافة رصيد نقدي",
            "subscriber.plan_change": "تغيير العرض",
        }
        return labels.get(action or "", action or "حدث إداري")

    activity_events: list[dict] = []
    open_session = next((r for r in session_rows if not r.get("acctstoptime")), None)
    if open_session:
        activity_events.append({
            "kind": "active",
            "pill": "نشط",
            "pill_class": "cc-pill-green",
            "dot_class": "green",
            "title": "جلسة نشطة الآن",
            "desc": (
                f"الاتصال عبر {open_session.get('nasporttype') or open_session.get('servicetype') or '—'} "
                f"من {open_session.get('callingstationid') or '—'}"
            ),
            "at": open_session.get("acctupdatetime") or open_session.get("acctstarttime") or sub_obj.last_seen_at,
        })

    if sub_obj.first_login_at:
        activity_events.append({
            "kind": "first_login",
            "pill": "اتصال",
            "pill_class": "cc-pill-blue",
            "dot_class": "blue",
            "title": "بداية الجلسة الأولى",
            "desc": "تم الاتصال لأول مرة باستخدام هذا الحساب.",
            "at": sub_obj.first_login_at,
        })

    for e in events[:8]:
        payload = _audit_payload(e)
        action = e.get("action") or e.get("event") or ""
        details = payload.get("note") or payload.get("notes") or payload.get("reason") or ""
        if not details and payload:
            preview = []
            for key in ("plan_id", "quota_mb", "quota_target", "amount", "currency", "policy"):
                if key in payload:
                    preview.append(f"{key}={payload.get(key)}")
            details = " · ".join(preview)
        activity_events.append({
            "kind": "audit",
            "pill": "إدارة",
            "pill_class": "cc-pill-purple",
            "dot_class": "amber" if (e.get("severity") == "warning") else "",
            "title": _audit_event_title(action),
            "desc": details or f"نفّذها {e.get('actor') or 'system'}",
            "at": e.get("created_at") or e.get("ts"),
            "actor": e.get("actor") or "",
        })

    activity_events.append({
        "kind": "created",
        "pill": "إنشاء",
        "pill_class": "cc-pill-purple",
        "dot_class": "",
        "title": "تم إنشاء حساب المشترك",
        "desc": f"تم إصدار الحساب باسم المستخدم {sub_obj.username}.",
        "at": sub_obj.created_at,
    })

    activity_events = sorted(
        activity_events,
        key=lambda item: str(item.get("at") or ""),
        reverse=True,
    )[:12]

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
        session_views=session_views,
        session_summary=session_summary,
        daily_usage=daily_usage,
        daily_max_bytes=daily_max_bytes,
        bandwidth_samples=bandwidth_samples,
        bandwidth_max_bps=bandwidth_max_bps,
        bandwidth_current=bandwidth_current,
        events=events,
        activity_events=activity_events,
        manager_events=manager_events,
        own_events=own_events,
        invoices=invoices,
        used_cards=used_cards,
        payments=payments,
        loans=loans,
    )


def _subscriber_360_payload(*, subscriber_id: int | None = None, username: str = ""):
    from ..services.subscriber_360 import Subscriber360Service

    service = Subscriber360Service(tenant_id=_tid())
    try:
        if subscriber_id is not None:
            return service.get_by_id(subscriber_id)
        return service.get_by_username(username)
    except KeyError:
        abort(404)


def subscriber_360(subscriber_id: int):
    return render_template(
        "radius/subscriber_360.html",
        s360=_subscriber_360_payload(subscriber_id=subscriber_id),
        source_route="subscribers",
    )


def users_360_by_username(username: str):
    return render_template(
        "radius/subscriber_360.html",
        s360=_subscriber_360_payload(username=username),
        source_route="users",
    )


def subscriber_renewal_preview(subscriber_id: int):
    from ..core.errors import RadiusValidationError
    from ..services.subscriber_360 import Subscriber360Service

    try:
        preview = Subscriber360Service(tenant_id=_tid()).preview_renewal(
            subscriber_id=subscriber_id,
            amount_paid=float(request.form.get("amount_paid") or 0),
            discount_amount=float(request.form.get("discount_amount") or 0),
            debt_amount=float(request.form.get("debt_amount") or 0),
            loan_days_to_settle=int(request.form.get("loan_days_to_settle") or 0),
            actor=_actor(),
            record_event=True,
        )
    except (KeyError, ValueError, RadiusValidationError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("radius.subscriber_360", subscriber_id=subscriber_id))
    flash(
        f"معاينة التجديد: {preview['earned_days']} يوم، بدون تطبيق مباشر على RADIUS.",
        "success",
    )
    return redirect(url_for("radius.subscriber_360", subscriber_id=subscriber_id))


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
        login_macs=_subscriber_login_macs(username),
        default_country=_default_country(),
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


def _sync_subscriber_rules(tenant_id: int, actor, form, username: str) -> None:
    """Persist every existing bandwidth_schedule rule for this subscriber
    from the form data on the main «حفظ» click. No-op when nothing
    changed.

    JS-only buttons inside _speed_rules_panel.html («تم» / «فعّل الكل»
    / «عطّل الكل»; the sub-section master toggle; rule-level enabled
    checkboxes) update DOM state without a roundtrip. This helper —
    called from users_update right after the subscriber save —
    persists those staged changes by iterating every `sr_edit_name_<id>`
    key in the form (always sent for existing rules), gathering the
    full sr_edit_*_<id> payload, comparing against the DB row, and
    issuing a single update_bandwidth_schedule per actually-modified
    rule. Reload happens once at the end of users_update — never per
    inline action.
    """
    from ..services.operations import get_operations_service
    from .speed_rules_ui import _days_from_form
    svc = get_operations_service()

    rule_ids = set()
    for key in form.keys():
        if not key.startswith("sr_edit_name_"):
            continue
        try:
            rule_ids.add(int(key[len("sr_edit_name_"):]))
        except ValueError:
            continue
    if not rule_ids:
        return

    def _as_int(v, default=0):
        try:
            return int(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    for rid in rule_ids:
        try:
            existing = svc.get_bandwidth_schedule(tenant_id=tenant_id, schedule_id=rid)
        except Exception:
            continue
        if not existing or existing.get("subscriber_username") != username:
            continue
        sfx = str(rid)
        new_data = {
            "name": (form.get(f"sr_edit_name_{sfx}") or "").strip() or existing.get("name"),
            "starts_at_time": form.get(f"sr_edit_starts_at_time_{sfx}") or existing.get("starts_at_time"),
            "ends_at_time":   form.get(f"sr_edit_ends_at_time_{sfx}")   or existing.get("ends_at_time"),
            "days_csv":  _days_from_form(form, f"sr_edit_days_{sfx}"),
            "speed_down_kbps": _as_int(form.get(f"sr_edit_speed_down_kbps_{sfx}"), existing.get("speed_down_kbps") or 0),
            "speed_up_kbps":   _as_int(form.get(f"sr_edit_speed_up_kbps_{sfx}"),   existing.get("speed_up_kbps") or 0),
            "cir_down_kbps":   _as_int(form.get(f"sr_edit_cir_down_kbps_{sfx}"),   existing.get("cir_down_kbps") or 0),
            "cir_up_kbps":     _as_int(form.get(f"sr_edit_cir_up_kbps_{sfx}"),     existing.get("cir_up_kbps") or 0),
            "restore_mode": form.get(f"sr_edit_restore_mode_{sfx}") or existing.get("restore_mode") or "profile_default",
            "priority": _as_int(form.get(f"sr_edit_priority_{sfx}"), existing.get("priority") or 5),
            "enabled": (form.get(f"sr_edit_enabled_{sfx}") or "").lower() in {"1", "true", "on", "yes"},
            "notes": form.get(f"sr_edit_notes_{sfx}") or existing.get("notes") or "",
        }
        # Skip the DB write when nothing actually changed.
        if all(str(existing.get(k) or "") == str(new_data.get(k) or "") for k in new_data):
            continue
        try:
            svc.update_bandwidth_schedule(
                tenant_id=tenant_id, actor=actor, schedule_id=rid,
                data=new_data,
            )
        except RadiusError:
            continue

    # ── Newly-staged rules from JS «اعتماد القاعدة» ──────────────
    # The frontend emits hidden inputs sr_new_<n>_* for each rule the
    # operator confirmed locally. Create them now (one create per index).
    new_indices = set()
    for key in form.keys():
        if not key.startswith("sr_new_"):
            continue
        rest = key[len("sr_new_"):]
        idx_part = rest.split("_", 1)[0]
        try:
            new_indices.add(int(idx_part))
        except ValueError:
            continue
    for nidx in sorted(new_indices):
        sfx = str(nidx)
        starts = form.get(f"sr_new_{sfx}_starts_at_time") or ""
        ends   = form.get(f"sr_new_{sfx}_ends_at_time") or ""
        if not starts.strip() or not ends.strip():
            continue
        payload = {
            "target_type": "subscriber",
            "subscriber_username": username,
            "name": (form.get(f"sr_new_{sfx}_name") or "").strip() or "قاعدة سرعة",
            "starts_at_time": starts,
            "ends_at_time":   ends,
            "days_csv": form.get(f"sr_new_{sfx}_days_csv") or "",
            "speed_down_kbps": _as_int(form.get(f"sr_new_{sfx}_speed_down_kbps"), 0),
            "speed_up_kbps":   _as_int(form.get(f"sr_new_{sfx}_speed_up_kbps"),   0),
            "restore_mode": form.get(f"sr_new_{sfx}_restore_mode") or "profile_default",
            "priority": _as_int(form.get(f"sr_new_{sfx}_priority"), 5),
            "enabled": (form.get(f"sr_new_{sfx}_enabled") or "1").lower() in {"1","true","on","yes"},
            "notes": "",
            "metadata": {"embedded_target": "subscriber", "added_via": "users_form_defer"},
        }
        try:
            svc.create_bandwidth_schedule(tenant_id=tenant_id, actor=actor, data=payload)
        except RadiusError:
            continue


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
            user_types=USER_TYPES, is_new=False, login_macs=_subscriber_login_macs(username),
            default_country=_default_country(),
            speed_rules_panel=None), 400
    # Persist any JS-staged rule edits (bulk فعّل/عطّل, «تم», master
    # toggle, per-row enabled flips) — all in one redirect at the end.
    _sync_subscriber_rules(_tid(), _actor(), request.form, username)
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.users_list"))


def users_delete(username: str):
    try:
        get_users_service().delete(actor=_actor(), username=username)
        flash("تمت الأرشفة. يمكنك الاستعادة من سلة المحذوفات.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_bulk_delete():
    """Soft-delete (archive) every selected subscriber in one POST.

    Reuses the EXACT single-row delete path — get_users_service().delete()
    — which archives via the adapter (subscribers_repo.archive_subscriber,
    tenant-scoped) and writes an audit record per subscriber. Unknown /
    already-archived / failing usernames are skipped and reported, never
    aborting the batch. Returns to the list with a flash summary.

    Accepts the selected usernames from `usernames` (repeated form field,
    what the sticky bulk bar posts); also tolerates a single comma-joined
    `usernames` value for resilience.
    """
    raw = request.form.getlist("usernames")
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    # De-dupe while preserving order; drop blanks.
    seen: set[str] = set()
    usernames: list[str] = []
    for name in raw:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            usernames.append(name)

    if not usernames:
        flash("لم يتم تحديد أي مشترك للحذف.", "warning")
        return redirect(url_for("radius.users_list"))

    svc = get_users_service()
    actor = _actor()
    deleted = 0
    failed: list[str] = []
    for name in usernames:
        try:
            svc.delete(actor=actor, username=name)
            deleted += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — never abort the batch on one bad row
            failed.append(name)

    if deleted:
        flash(f"تم حذف {deleted} مشترك. يمكن الاستعادة من سلة المحذوفات.", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّر حذف {len(failed)} مشترك: {preview}", "warning")
    if not deleted and not failed:
        flash("لم يتم حذف أي مشترك.", "warning")
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


def users_change_plan(username: str):
    try:
        plan_id = int(request.form.get("plan_id") or 0)
        policy = (request.form.get("policy") or "").strip()
        result = get_users_service().change_plan(
            actor=_actor(),
            username=username,
            plan_id=plan_id,
            policy=policy,
        )
        debt = float(result.get("debt_amount") or 0)
        delta = int(result.get("minute_delta") or 0)
        if debt > 0:
            flash(f"تم تغيير العرض وتسجيل دين فرق السعر بقيمة {debt:.2f}.", "success")
        elif delta > 0:
            flash(f"تم تغيير العرض وتعويض {delta // 1440} يوم إضافي.", "success")
        elif delta < 0:
            flash(f"تم تغيير العرض وإنقاص {abs(delta) // 1440} يوم.", "warning")
        else:
            flash("تم تغيير العرض للمشترك.", "success")
    except (TypeError, ValueError):
        flash("اختيار العرض غير صحيح.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_send_sms(username: str):
    try:
        result = get_users_service().send_sms(
            actor=_actor(),
            username=username,
            message=request.form.get("message") or "",
        )
        flash(f"تمت إضافة رسالة SMS إلى قائمة الإرسال ({result.get('queued_count', 0)}).", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_quota_reset_daily(username: str):
    try:
        get_users_service().reset_daily_quota(actor=_actor(), username=username)
        flash("تمت استعادة الكوتة اليومية للمشترك.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_quota_topup(username: str):
    try:
        quota_mb = int(request.form.get("quota_mb") or 0)
        charge_mode = (request.form.get("charge_mode") or "free").strip()
        amount = _form_float("amount", 0.0)
        saved = get_users_service().add_quota(
            actor=_actor(),
            username=username,
            quota_mb=quota_mb,
            quota_target=(request.form.get("quota_target") or "combined").strip(),
            charge_mode=charge_mode,
            amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip(),
        )
        mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
        flash(f"تمت إضافة {quota_mb} MB كوتة {mode_label}. الرصيد الحالي {float(saved.balance or 0):.2f}.", "success")
    except (TypeError, ValueError):
        flash("قيمة الكوتة أو المبلغ غير صحيحة.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_balance_add(username: str):
    try:
        amount = _form_float("amount")
        saved = get_users_service().add_cash_balance(
            actor=_actor(),
            username=username,
            amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip(),
        )
        flash(f"تمت إضافة رصيد نقدي بقيمة {amount:.2f}. الرصيد الحالي {float(saved.balance or 0):.2f}.", "success")
    except (TypeError, ValueError):
        flash("قيمة الرصيد النقدي غير صحيحة.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))
