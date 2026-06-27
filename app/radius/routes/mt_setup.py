"""L3 — MikroTik setup-wizard routes.

Three endpoints back the Phase-L wizard flow:

    GET  /admin/radius/mt/setup
        Form: nas_name + address + RouterOS major version + (auto)
        server IP. Submits to POST below.

    POST /admin/radius/mt/setup
        - Generates fresh credentials (mt_provisioner).
        - Writes nas_devices row through the existing service so
          the audit trail captures the creation.
        - Backfills the L2 columns (ros_version, provisioned_at).
        - Redirects to the script page with the new nas_id +
          server_ip carried as a query arg.

    GET  /admin/radius/mt/<nas_id>/script?server_ip=...
        Renders the RouterOS script for that row. The page itself
        has a 'Test now' button that just deep-links into the
        existing K9 dashboard — there's no separate test endpoint
        to write because /system/overview already does the job.
"""
from __future__ import annotations

import os
from ..core import env_settings
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
    session, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import NasDevice
from ..db.connection import db, transaction
from ..services.devices import get_nas_devices_service
from ..services.mt_provisioner import (
    SUPPORTED_ROS_VERSIONS, generate_credentials, render_routeros_script,
    render_wg_block, render_sstp_mgmt_block, render_pptp_mgmt_block,
    script_section_lines, block_command_line, block_line_count,
)
from ..services import wg_peer_manager as wpm
from ..services import router_mgmt_tunnel as rmt
# أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
# توليد أنفاق SSTP/PPTP/IPsec ومزوّد الـCHR (chr_provisioner / v6_tunnels /
# routeros_caps tunnel-validation / router_tunnels_repo) كان يُولّد بيانات
# اعتماد على CHR من لوحة العميل المباعة — مركزي للمالك حصراً.


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "wizard"


def _default_server_ip() -> str:
    """Best-effort guess of the address the RADIUS server is reachable
    at FROM the router's perspective.

    Priority:
      1. `HOBERADIUS_PUBLIC_IP` env (operator-set, authoritative).
      2. The request's host header without port (works when the
         admin uses the same address the router would).
      3. Empty string — the form shows a placeholder asking the
         operator to type it.
    """
    env_ip = (env_settings.env("HOBERADIUS_PUBLIC_IP") or "").strip()
    if env_ip:
        return env_ip
    try:
        return (request.host or "").split(":", 1)[0]
    except RuntimeError:
        return ""


# أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
# كان هنا ‎_configure_v6_tunnels‎ + ‎_gen_tunnel_secret‎ + مفاتيح جلسة كشف
# أسرار النفق. توليد أنفاق SSTP/PPTP/IPsec وإنشاء حسابات على CHR من لوحة
# العميل المباعة ممنوع؛ هذه حوكمة مركزية للمالك. أعمدة الأنفاق على
# nas_devices (migrations 092/105) تبقى موجودة لكن غير مستخدمة — محجوزة
# لإعادة البناء المركزي لاحقاً.


def register_mt_setup_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/operations", "mt_operations", mt_operations, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/operations/live", "mt_operations_live",
        mt_operations_live, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/setup", "mt_setup_form", mt_setup_form, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/setup", "mt_setup_create", mt_setup_create, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/script",
        "mt_setup_script", mt_setup_script, methods=["GET"],
    )
    # ── v6 SSTP/PPTP management-tunnel credentials (dedicated UI) ──
    bp.add_url_rule(
        "/mt/<int:nas_id>/sstp", "mt_sstp_credentials",
        mt_sstp_credentials, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/onboarding-script", "mt_onboarding_script",
        mt_onboarding_script, methods=["GET"],
    )
    # ── Remote access (Open WinBox) over the tunnel ──
    bp.add_url_rule(
        "/mt/<int:nas_id>/remote/winbox/open", "mt_remote_winbox_open",
        mt_remote_winbox_open, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/remote/<int:session_id>/close", "mt_remote_close",
        mt_remote_close, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/remote-sessions", "mt_remote_sessions",
        mt_remote_sessions, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/sstp/test", "mt_sstp_test",
        mt_sstp_test, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/sstp/sync", "mt_sstp_sync",
        mt_sstp_sync, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/sstp/reset", "mt_sstp_reset",
        mt_sstp_reset, methods=["POST"],
    )
    # ── SSTP/PPTP credential management surface (list/view/edit/delete) ──
    bp.add_url_rule(
        "/mt/sstp-users", "mt_sstp_users", mt_sstp_users, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/sstp-users/toggle", "mt_sstp_user_toggle",
        mt_sstp_user_toggle, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/sstp-users/expiry", "mt_sstp_user_expiry",
        mt_sstp_user_expiry, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/sstp-users/reset", "mt_sstp_user_reset",
        mt_sstp_user_reset, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/sstp-users/delete", "mt_sstp_user_delete",
        mt_sstp_user_delete, methods=["POST"],
    )
    # ── WireGuard management-tunnel surface (v7 parallel to SSTP page) ──
    bp.add_url_rule(
        "/mt/wg-peers", "mt_wg_peers", mt_wg_peers, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/wg", "mt_wg_details", mt_wg_details, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/wg-peers/regenerate", "mt_wg_peer_regenerate",
        mt_wg_peer_regenerate, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/wg-peers/remove", "mt_wg_peer_remove",
        mt_wg_peer_remove, methods=["POST"],
    )


# ─── Management-tunnel status (محسوبة، صادقة) ────────────────────
#
# عمود nas_devices.management_tunnel_status (migration 092) عمود ساكن
# لا يكتبه أي شيء في كامل قاعدة الكود — يبقى على قيمته الافتراضية
# 'not_configured' أبداً، فكانت الشارة القديمة تُظهر دائماً دِرعاً
# رمادياً بتلميح مبهم («لا نفق إدارة مُعدّ»). لا نقرأ هنا handshake
# الـWireGuard الحيّ عمداً: لوحة العميل المباعة تُبقي مُشغّل أوامر
# الـshell معطّلاً افتراضياً (DisabledCommandRunner) ولا نُخزّن أي
# أسرار CHR/نفق فيها. بدلاً من ذلك نشتقّ حالة صادقة وقابلة للتنفيذ من
# الإشارات التي تملكها اللوحة أصلاً:
#
#   1) last_check_status — فحص وصول TCP فعلي يُرسَل إلى عنوان الراوتر
#      داخل النفق على منفذ الـAPI (devices_test). إثبات شامل أن نفق
#      الإدارة يحمل حركة فعلاً.
#   2) ملف الـpeer تحت wg-peers.d — يُثبت أن peer أُنشئ لهذا الراوتر
#      (AllowedIPs = ‎/32 لعنوان الراوتر في النفق). مفاتيح عامة فقط؛
#      لا يغادر أي سرّ المضيف.
#   3) دورة حياة التجهيز (router_provisioning_registry).


def _provisioned_peer_ips() -> "set[str] | None":
    """عناوين النفق ‎/32 التي لها ملف peer لـWireGuard.

    تُعيد None حين يتعذّر قراءة مجلد الـpeers (مثلاً جهاز تطوير بلا
    wg-peers.d) كي لا يدّعي المُستدعي زوراً «لا يوجد peer».
    """
    try:
        peers_dir = Path(env_settings.env(wpm.PEERS_DIR_ENV) or wpm.PEERS_DIR_DEFAULT)
        if not peers_dir.is_dir():
            return None
        ips: "set[str]" = set()
        for child in sorted(peers_dir.iterdir()):
            if not (child.is_file() and child.suffix == ".conf"
                    and not child.name.startswith(".")):
                continue
            try:
                parsed = wpm.parse_peer_file(child)
            except OSError:
                continue
            for piece in str(parsed.get("AllowedIPs") or "").split(","):
                piece = piece.strip()
                if piece:
                    ips.add(piece.split("/")[0])
        return ips
    except Exception:
        return None


_CHECK_STATUS_AR = {
    "reachable":   "ناجح",
    "timeout":     "انتهت المهلة",
    "unreachable": "تعذّر الاتصال",
    "unknown":     "غير معروف",
}


def _derive_mgmt_status(
    item: dict,
    provisioned_ips: "set[str] | None",
    *,
    live: "str | None" = None,
    live_pollable: bool = False,
) -> dict:
    """نموذج عرض صادق لشارة نفق الإدارة لصفّ راوتر واحد.

    لا يُعيد أبداً «غير محدّد» المبهمة؛ كل حالة تحمل سبباً عربياً
    واضحاً يستطيع المشغّل التصرّف بناءً عليه.

    **مصدر واحد متّسق مع عمود «الحالة»:** عمود «متصل/غير متصل» يأتي من
    استطلاع حيّ لـ/counters (JS). إذا كانت اللوحة تتواصل مع الراوتر الآن
    فالنفق يحمل الحركة فعلاً = فعّال حتماً، مهما قال فحص TCP اليدوي القديم
    (last_check). لذا:
      * ``live='connected'`` (أو ``'down'``) — الإشارة الحيّة لها الأولوية
        المطلقة على كل شيء (تُمرَّر من نفس الاستطلاع، عبر JS وقت التشغيل).
      * ``live_pollable=True`` — الصف سيُستطلَع حيًّا (مفعّل وغير قيد
        التجهيز): لا نؤكّد أي حالة من last_check القديم لأنه قد يتناقض مع
        العمود الحيّ؛ نعرض «جارٍ الفحص…» ونترك الاستطلاع يحسمها. هذا يمنع
        تناقض «متصل + النفق متوقف» نهائيًّا.
    """
    ros = str(item.get("ros_version") or "")
    # نوع النفق المتوقّع حسب إصدار RouterOS (مرآة recommended_management_tunnel).
    tunnel_label = ("WireGuard" if ros.startswith("7")
                    else ("SSTP" if ros.startswith("6") else "الإدارة"))

    check = str(item.get("last_check_status") or "").strip().lower()
    at = str(item.get("last_check_at") or "").strip()
    lifecycle = str(item.get("lifecycle_state") or "")
    addr = str(item.get("address") or "").strip()
    has_peer = ((addr in provisioned_ips)
                if (provisioned_ips is not None and addr) else None)

    # 0) الإشارة الحيّة لها الأولوية المطلقة (نفس مصدر عمود «متصل»).
    if live == "connected":
        return {
            "state": "active", "color": "green", "label": "نفق فعّال",
            "reason": ("نفق إدارة %s فعّال — اللوحة تتواصل مع الراوتر الآن "
                       "(حركة حيّة عبر النفق)." % tunnel_label),
        }
    if live == "down":
        return {
            "state": "down", "color": "red", "label": "النفق متوقف",
            "reason": ("لا استجابة حيّة من الراوتر عبر نفق %s الآن. تأكّد أن "
                       "الراوتر يعمل وأن النفق متصل." % tunnel_label),
        }

    # 1) صفوف يُستطلَع اتصالها حيًّا: لا نؤكّد حالة من فحص TCP اليدوي القديم
    #    (قد يكون منتهي المهلة منذ ساعات بينما النفق يحمل الحركة الآن) —
    #    نؤجّل القرار للاستطلاع الحيّ كي لا نتناقض مع عمود «الحالة».
    if live_pollable:
        return {
            "state": "checking", "color": "grey", "label": "جارٍ الفحص…",
            "reason": ("يُقاس اتصال نفق %s حيًّا الآن عبر استطلاع الراوتر…"
                       % tunnel_label),
        }

    # 2) أقوى إشارة ساكنة (للصفوف غير المُستطلَعة): فحص الوصول عبر النفق.
    if check == "reachable":
        return {
            "state": "active", "color": "green", "label": "نفق فعّال",
            "reason": ("نفق إدارة %s فعّال — الراوتر يُجاب عبر النفق%s."
                       % (tunnel_label, (" (آخر فحص ناجح — " + at + ")") if at else " (آخر فحص ناجح)")),
        }
    if check in ("timeout", "unreachable"):
        return {
            "state": "down", "color": "red", "label": "النفق متوقف",
            "reason": ("تعذّر الوصول إلى الراوتر عبر نفق %s (%s%s). تأكّد أن الراوتر يعمل وأن النفق متصل."
                       % (tunnel_label, _CHECK_STATUS_AR.get(check, check), (" في " + at) if at else "")),
        }

    # 3) فشل التجهيز.
    if lifecycle == "failed":
        return {
            "state": "down", "color": "red", "label": "فشل التجهيز",
            "reason": (item.get("failure_reason")
                       or "فشل تجهيز نفق الإدارة. أعد تشغيل معالج التجهيز."),
        }

    # 4) التجهيز ما زال جارياً.
    if lifecycle in ("reserved", "waiting_router_key", "peer_ready",
                     "vpn_verified", "radius_pending", "api_pending"):
        return {
            "state": "pending", "color": "amber", "label": "قيد الإعداد",
            "reason": ("نفق %s قيد الإعداد (%s)."
                       % (tunnel_label, item.get("lifecycle_label_ar") or lifecycle)),
        }

    # 5) أُنشئ peer لكن لم يُختبر الوصول بعد.
    if has_peer:
        return {
            "state": "pending", "color": "amber", "label": "بانتظار أول فحص",
            "reason": ("أُنشئ peer نفق %s لهذا الراوتر، لكن لم يُختبر الوصول بعد — "
                       "اضغط «اختبار الاتصال» في قائمة الأجهزة." % tunnel_label),
        }

    # 6) لم يُنشأ peer إطلاقاً (والقراءة متاحة).
    if has_peer is False:
        return {
            "state": "not_setup", "color": "grey", "label": "لم يُنشأ نفق",
            "reason": ("لم يُنشأ peer نفق %s لهذا الراوتر بعد. شغّل معالج التجهيز لإنشاء النفق."
                       % tunnel_label),
        }

    # 7) تعذّر تحديد وجود peer (لا يمكن قراءة wg-peers.d) ولا يوجد فحص.
    return {
        "state": "unknown", "color": "grey", "label": "لم يُختبر بعد",
        "reason": ("لم يُختبر نفق %s بعد — اضغط «اختبار الاتصال» في قائمة الأجهزة "
                   "لقياس الوصول عبر النفق." % tunnel_label),
    }


def mt_operations():
    """L5 — Operations Center — unified router management.

    Single source of truth for every router in the tenant. Replaces
    the older split between '/setup-wizard/fleet' (provisioning view)
    and this page (production view) — both now flow through here.
    Each row carries:

      * Inventory state (nas_devices)        — name, address, enabled
      * Live API state  (counters poll)      — connected users, traffic
      * Provisioning lifecycle (registry)    — waiting_router_key /
                                               peer_ready / vpn_verified
                                               / fully_onboarded / failed
      * Wizard run id (for resume button)

    Operator actions per row:
      🛠️ خدمات       → Router Services Dashboard (Hotspot, Broadband,
                       حجب/فتح مواقع, تغيير IP, اتصال عن بُعد)
      📊 لوحة         → live K9 dashboard
      ↪ متابعة المعالج → only when lifecycle is mid-progress
      ⚙ برمجة / تصميم / نسخ / سكربت / تفعيل/تعطيل / تعديل
    """
    rows = db().execute(
        """
        SELECT
          nd.id, nd.name, nd.address, nd.enabled, nd.ros_version,
          nd.provisioned_at, nd.last_check_status, nd.last_check_at,
          nd.connection_mode, nd.vpn_peer_address, nd.api_user, nd.api_port,
          nd.management_tunnel_type, nd.management_tunnel_status,
          nd.traffic_tunnel_type, nd.traffic_mode, nd.traffic_enabled,
          rpr.id              AS registry_id,
          rpr.wizard_run_id   AS wizard_run_id,
          rpr.lifecycle_state AS lifecycle_state,
          rpr.failure_reason  AS failure_reason
        FROM nas_devices nd
        -- Pick the latest registry row for this router (matched on the
        -- VPN IP, since the wizard registers `address = router_vpn_ip`).
        -- Routers added outside the wizard simply won't match — they
        -- get a blank lifecycle, treated as already-onboarded by the UI.
        LEFT JOIN router_provisioning_registry rpr
          ON rpr.id = (
            SELECT id FROM router_provisioning_registry
            WHERE tenant_id = nd.tenant_id
              AND router_vpn_ip = nd.address
            ORDER BY id DESC LIMIT 1
          )
        WHERE nd.tenant_id = ?
          AND (nd.deleted_at IS NULL OR nd.deleted_at = '')
        ORDER BY nd.id ASC
        """,
        (_tid(),),
    ).fetchall()
    # Lifecycle states the operator should still SEE as 'in progress'.
    # 'fully_onboarded' is the steady state — no badge needed there.
    PROVISIONING_STATES = {
        "reserved", "waiting_router_key", "peer_ready", "vpn_verified",
        "radius_pending", "api_pending", "failed",
    }
    LIFECYCLE_LABELS_AR = {
        "reserved":            ("محجوز",            "grey"),
        "waiting_router_key":  ("بانتظار مفتاح",     "amber"),
        "peer_ready":          ("VPN جاهز",         "amber"),
        "vpn_verified":        ("اختبار التجهيز",    "amber"),
        "radius_pending":      ("RADIUS قيد الإعداد", "amber"),
        "api_pending":         ("API قيد الإعداد",   "amber"),
        "failed":              ("فشل التجهيز",       "red"),
    }
    items = []
    for n, row in enumerate(rows, start=1):
        lifecycle = str(row["lifecycle_state"] or "")
        is_provisioning = lifecycle in PROVISIONING_STATES
        label, color = LIFECYCLE_LABELS_AR.get(lifecycle, ("", "grey"))
        items.append({
            "display_num": n,
            "id": row["id"],
            "name": row["name"],
            "address": row["address"],
            "enabled": bool(row["enabled"]),
            "ros_version": row["ros_version"] or "",
            "provisioned_at": row["provisioned_at"] or "",
            "last_check_status": row["last_check_status"] or "",
            "last_check_at": row["last_check_at"] or "",
            "connection_mode": row["connection_mode"] or "direct",
            "vpn_peer_address": row["vpn_peer_address"] or "",
            "api_user": row["api_user"] or "",
            "api_port": row["api_port"] or 8728,
            # Provisioning lifecycle (from router_provisioning_registry)
            "registry_id": row["registry_id"],
            "wizard_run_id": row["wizard_run_id"],
            "lifecycle_state": lifecycle,
            "lifecycle_label_ar": label,
            "lifecycle_color": color,
            "is_provisioning": is_provisioning,
            "failure_reason": row["failure_reason"] or "",
            # ── VPN tunnel profile (migration 092) ──
            "management_tunnel_type": row["management_tunnel_type"] or "none",
            "management_tunnel_status": row["management_tunnel_status"] or "not_configured",
            "traffic_tunnel_type": row["traffic_tunnel_type"] or "none",
            "traffic_mode": row["traffic_mode"] or "disabled",
            "traffic_enabled": bool(row["traffic_enabled"]),
        })
    # شارة نفق الإدارة المحسوبة — تُقرأ ملفات الـpeers مرّة واحدة لكل
    # صفحة (مسح مجلد واحد، بلا shell)، ثم تُشتقّ الحالة لكل صفّ.
    # الصفوف المفعّلة غير قيد التجهيز تُستطلَع حيًّا (نفس عمود «الحالة»)،
    # فنترك الاستطلاع الحيّ (JS) يحسم شارتها كي لا تتناقض مع العمود.
    provisioned_ips = _provisioned_peer_ips()
    # الأداء: لا نَفحص الاتصال أثناء رسم الصفحة. كان حساب «متصل» من radacct
    # (live_map) يجري متزامنًا هنا فيُؤخّر ظهور الصفحة. الآن الصدفة تُرسَم فورًا
    # (DB فقط) و«حالة الاتصال» تبدأ «جارٍ فحص الاتصال…» ثم تُملأ كسولًا عبر
    # AJAX (radius.mt_operations_live + استطلاع /counters) بعد اكتمال التحميل.
    for it in items:
        it["mgmt"] = _derive_mgmt_status(
            it, provisioned_ips,
            live_pollable=(it["enabled"] and not it["is_provisioning"]),
        )
        it["live"] = None   # يُملأ بالجافاسكربت بعد الرسم (لا فحص متزامن)
    provisioning_count = sum(1 for it in items if it["is_provisioning"])
    # عدّاد «متصل» يبدأ مجهولًا (None → «—») ويُحدّثه AJAX بعد التحميل.
    radacct_connected_count = None
    # O2 — pass an api_token so the per-row counter poll JS can
    # authenticate against /api/v1/mikrotik/<id>/counters without
    # needing a separate session-bridging step.
    from .mt_dashboard import _ui_api_token   # internal helper reuse
    return render_template(
        "radius/mt_operations.html",
        items=items,
        api_token=_ui_api_token(),
        provisioning_count=provisioning_count,
        radacct_connected_count=radacct_connected_count,
    )


def mt_operations_live():
    """Lazy connectivity seed for the operations page (AJAX, JSON).

    Returns the radacct-derived live status per router so the page shell can
    render instantly and fill «متصل» AFTER load — instead of computing it
    synchronously during render. DB-only (radacct), fast, never contacts a
    router. Mirrors the diagnostics lazy-load pattern."""
    rows = db().execute(
        "SELECT id, address, vpn_peer_address FROM nas_devices "
        "WHERE tenant_id=? AND (deleted_at IS NULL OR deleted_at='')",
        (_tid(),),
    ).fetchall()
    try:
        from ..services import live_sessions
        lmap = live_sessions.live_map(_tid())
        out: dict[str, dict] = {}
        connected = 0
        for r in rows:
            live = live_sessions.router_live(dict(r), lmap)
            online = bool(live.get("online"))
            out[str(r["id"])] = {"online": online,
                                 "active": int(live.get("active") or 0)}
            if online:
                connected += 1
        return jsonify({"ok": True, "routers": out, "connected": connected})
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, never 500 the poll
        return jsonify({"ok": False, "error": str(exc),
                        "routers": {}, "connected": 0})


def mt_setup_form():
    return render_template(
        "radius/mt_setup_form.html",
        default_server_ip=_default_server_ip(),
        ros_versions=SUPPORTED_ROS_VERSIONS,
    )


def mt_setup_create():
    name = (request.form.get("name") or "").strip()
    ros_version = (request.form.get("ros_version") or "").strip()
    server_ip = (request.form.get("server_ip") or _default_server_ip()).strip()

    # The router's reachable address is NEVER typed by the operator — it is
    # the stable tunnel IP the system auto-assigns + persists (WireGuard for
    # v7, accel SSTP/PPTP for v6). It is filled in below by the tunnel flow.
    address = ""

    # Hard validation. Friendly form-level checks live in the
    # template; this is the last line of defence.
    if not name:
        flash("اكتب اسمًا للراوتر", "error")
        return redirect(url_for("radius.mt_setup_form"))
    if ros_version not in SUPPORTED_ROS_VERSIONS:
        flash("اختر نسخة RouterOS (6 أو 7)", "error")
        return redirect(url_for("radius.mt_setup_form"))
    # v6 always goes through a management tunnel — SSTP (default) or PPTP. The
    # tunnel auto-assigns the router's stable IP, so there is no manual
    # address path anymore. The "direct" option was dropped entirely (owner
    # decision): the ONLY paths are WireGuard (v7) and SSTP/PPTP (v6). Any
    # legacy/unknown v6_mode (incl. "direct") falls back to SSTP.
    v6_mode = (request.form.get("v6_mode") or "sstp_mgmt").strip()
    if v6_mode != "pptp_mgmt":
        v6_mode = "sstp_mgmt"
    v6_is_tunnel = ros_version == "6"
    if ros_version == "7" and not server_ip:
        flash(
            "لم نتمكّن من معرفة عنوان السيرفر — اكتبه يدويًّا أو اضبط "
            "HOBERADIUS_PUBLIC_IP",
            "error",
        )
        return redirect(url_for("radius.mt_setup_form"))

    creds = generate_credentials()

    # Phase M — for RouterOS 7 we auto-provision a WireGuard peer
    # so the router never needs a public IP. The NAS row records
    # the tunnel address as its `address`, and HobeRadius dials
    # the router via that IP (connection_mode='vpn').
    wg_provision = None
    if ros_version == "7":
        try:
            wg_provision = wpm.provision_peer(name)
        except ValueError as exc:
            flash(f"تعذّر تجهيز WireGuard: {exc}", "error")
            return redirect(url_for("radius.mt_setup_form"))
        except Exception as exc:  # noqa: BLE001
            flash(
                "WG غير مهيّأ على السيرفر بعد — تأكّد أن "
                "HOBERADIUS_WG_SERVER_PUBKEY و HOBERADIUS_WG_SERVER_ENDPOINT "
                f"مضبوطين في .env. ({exc})",
                "error",
            )
            return redirect(url_for("radius.mt_setup_form"))
        # Override the operator-typed address with the WG-allocated one.
        address = str(wg_provision.allowed_ip)

    # Phase v6 — provision an SSTP (default) / PPTP management tunnel over
    # accel-ppp. Mirrors the WG path: the tunnel account is a RADIUS-authable
    # user (radcheck/radreply) with a fixed Framed-IP; the NAS row records
    # that tunnel IP as its address so CoA reaches the router over the tunnel.
    tun_provision = None
    if v6_is_tunnel:
        transport = "sstp" if v6_mode == "sstp_mgmt" else "pptp"
        try:
            tun_provision = rmt.provision_tunnel(
                name, transport=transport, tenant_id=_tid())
        except rmt.RouterMgmtTunnelError as exc:
            flash(f"تعذّر تجهيز نفق الإدارة: {exc}", "error")
            return redirect(url_for("radius.mt_setup_form"))
        except Exception as exc:  # noqa: BLE001
            flash(
                "خادم accel غير مهيّأ بعد — تأكّد من ضبط HOBERADIUS_ACCEL_SERVER_HOST "
                f"و HOBERADIUS_MGMT_TUNNEL_POOL في الإعدادات. ({exc})",
                "error",
            )
            return redirect(url_for("radius.mt_setup_form"))
        # The router's fixed tunnel IP becomes its address (CoA target).
        address = str(tun_provision.tunnel_ip)

    dev = NasDevice(
        id=None,
        name=name,
        address=address,
        secret=creds["radius_secret"],
        vendor="mikrotik",
        nas_type="hotspot",
        api_port=8728,
        api_user=creds["api_user"],
        api_password=creds["api_password"],
        api_use_tls=False,
        enabled=True,
        monitoring_enabled=True,
    )
    try:
        saved = get_nas_devices_service().create(actor=_actor(), device=dev)
    except Exception as exc:  # noqa: BLE001
        # If we already created a WG peer, roll it back so the IP
        # is reclaimed.
        if wg_provision is not None:
            try:
                wpm.deprovision_peer(wg_provision.slug)
            except Exception:
                pass
        # Same for a v6 tunnel account — remove the radcheck/radreply user.
        if tun_provision is not None:
            try:
                rmt.deprovision_tunnel(tun_provision.tunnel_username, tenant_id=_tid())
            except Exception:
                pass
        flash(f"فشل إنشاء صف الراوتر: {exc}", "error")
        return redirect(url_for("radius.mt_setup_form"))

    # Backfill L2 columns + (M2) the K1 VPN columns +
    # (M4) sync into the FreeRADIUS `nas` table so the router can
    # actually RADIUS-authenticate. FreeRADIUS reads this table on
    # boot with `read_clients = yes`; new rows are picked up by
    # restarting freeradius (or sending HUP).
    now = datetime.now(timezone.utc).isoformat() + "Z"
    short_name = (saved.name or "rt")[:32]
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET ros_version=?, provisioned_at=? "
            "WHERE id=? AND tenant_id=?",
            (ros_version, now, saved.id, _tid()),
        )
        if wg_provision is not None:
            c.execute(
                "UPDATE nas_devices SET connection_mode=?, "
                "       vpn_peer_address=?, vpn_public_key=? "
                "WHERE id=? AND tenant_id=?",
                (
                    "vpn",
                    str(wg_provision.allowed_ip),
                    wg_provision.router_public_key,
                    saved.id, _tid(),
                ),
            )
        if tun_provision is not None:
            # Persist the v6 tunnel state via migration 092's prepared columns.
            # connection_mode='vpn' + vpn_peer_address=tunnel IP routes CoA to
            # the router over the tunnel (resolve_connection_address). The
            # credential lives in radcheck; management_secret_ref records the
            # RADIUS identity (the rtr- username) that holds it.
            c.execute(
                "UPDATE nas_devices SET connection_mode=?, vpn_peer_address=?, "
                "       management_tunnel_type=?, management_tunnel_status=?, "
                "       management_tunnel_interface_name=?, "
                "       management_remote_address=?, management_vpn_subnet=?, "
                "       management_secret_ref=?, sstp_verify_certificate=? "
                "WHERE id=? AND tenant_id=?",
                (
                    "vpn", str(tun_provision.tunnel_ip),
                    tun_provision.mgmt_tunnel_type, "pending",
                    tun_provision.interface,
                    str(tun_provision.tunnel_ip), str(tun_provision.pool),
                    tun_provision.tunnel_username, 0,
                    saved.id, _tid(),
                ),
            )
        # M4 — make the router visible to FreeRADIUS. The `nas`
        # table has no unique constraint on (tenant_id, nasname),
        # so we delete-then-insert to stay idempotent on re-runs
        # (e.g. operator deletes a NAS and re-creates with the
        # same address — fresh secret, fresh row).
        c.execute(
            "DELETE FROM nas WHERE tenant_id=? AND nasname=?",
            (_tid(), saved.address),
        )
        c.execute(
            "INSERT INTO nas (tenant_id, nasname, shortname, type, secret) "
            "VALUES (?,?,?,?,?)",
            (_tid(), saved.address, short_name, "mikrotik", saved.secret),
        )

    # The router's PRIVATE key only exists in this request. It
    # belongs on the router, never in our DB. Stash it on the
    # session for the next-page render and delete it after one use.
    if wg_provision is not None:
        session[f"_wg_router_priv_{saved.id}"] = wg_provision.router_private_key

    # The v6 tunnel password only exists in this request — it belongs on the
    # router, and its canonical copy is in radcheck. Stash it for the
    # next-page render and delete it after one use (mirrors the WG key stash).
    if tun_provision is not None:
        session[f"_mgmt_tun_pw_{saved.id}"] = tun_provision.tunnel_password

    return redirect(url_for(
        "radius.mt_setup_script",
        nas_id=saved.id,
        server_ip=server_ip,
    ))


def mt_setup_script(nas_id: int):
    row = db().execute(
        "SELECT * FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        abort(404)
    nas = dict(row)
    ros_version = (nas.get("ros_version") or "7").strip()

    mgmt_type = (nas.get("management_tunnel_type") or "").strip().lower()
    is_v6_tunnel = mgmt_type in ("sstp_mgmt", "pptp_mgmt")

    # ── SINGLE SOURCE OF TRUTH ──────────────────────────────────────────────
    # A v6 SSTP/PPTP router has exactly ONE script: the AUTHORITATIVE onboarding
    # generator (router_onboarding_script.build_onboarding_script — firewall
    # ordering, idempotent + authoritative cleanup: RADIUS-disable-before-add,
    # tunnel cleanup, NAT redirect, self-heal). This legacy setup-script page
    # produced a DIFFERENT, simpler script (RADIUS + API + tunnel block only) for
    # the same router, which confused operators ("why are the two scripts
    # different?"). So for a v6 tunnel row we redirect here — there is never a
    # second, divergent script. This page now serves ONLY v7 WireGuard + legacy/
    # direct rows (which the onboarding generator doesn't cover). The v6 tunnel
    # password is revealed by the onboarding page from radcheck, so no secret is
    # lost by redirecting (it doesn't depend on the one-time session stash).
    if is_v6_tunnel:
        return redirect(url_for("radius.mt_onboarding_script", nas_id=nas_id))

    # Phase M — for v7+VPN rows we render the WG block in front of
    # the RADIUS commands. The router's private key only lives in
    # the session for one render (see mt_setup_create), so a
    # refresh after issuing intentionally gives a "key already
    # shown — rotate to re-issue" notice rather than re-printing
    # the secret.
    wg_block = None
    tunnel_block = None
    wg_priv_revealed = False
    mgmt_pw_revealed = False
    wg_priv = session.pop(f"_wg_router_priv_{nas_id}", None)
    mgmt_pw = session.pop(f"_mgmt_tun_pw_{nas_id}", None)
    is_vpn_row = (nas.get("connection_mode") or "").strip().lower() == "vpn"

    # Default RADIUS dial target. The wizard now creates ONLY tunnel rows
    # (WireGuard v7 / SSTP|PPTP v6) — there is no "direct" wizard path. This
    # fallback only applies to legacy/externally-created non-tunnel rows.
    radius_server_ip = (
        request.args.get("server_ip") or _default_server_ip() or "<SERVER_IP>"
    )
    api_allowed_address = None

    if ros_version == "7" and is_vpn_row and not is_v6_tunnel:
        # ── v7 WireGuard path (unchanged) ──
        try:
            cfg = wpm.load_config()
        except ValueError as exc:
            flash(f"تعذّر قراءة إعدادات WireGuard من البيئة: {exc}", "error")
            return redirect(url_for("radius.mt_setup_form"))
        if wg_priv:
            wg_priv_revealed = True
            wg_block = render_wg_block(
                nas_name=nas["name"],
                router_private_key=wg_priv,
                server_pubkey=cfg.server_pubkey,
                server_endpoint=cfg.server_endpoint,
                allowed_subnet=str(cfg.subnet),
                router_tunnel_ip=f"{nas['address']}/{cfg.subnet.prefixlen}",
                keepalive_sec=wpm.DEFAULT_KEEPALIVE_SEC,
                ros_version="7",
                mgmt_server_ip=str(cfg.server_ip),
            )
        # else: no private key in session → "key already issued" notice.
        radius_server_ip = str(cfg.server_ip)   # router dials RADIUS over WG
        # The v7 script's `set api address=` line is emitted AFTER the WG block,
        # so it must carry the SAME combined allow-list (WG subnet + SSTP
        # gateway) — otherwise it would clobber the WG block's combined `api`
        # line back to WG-only within the same paste.
        from ..services import mgmt_acl as _mgmt_acl
        api_allowed_address = _mgmt_acl.combined_acl(
            wg_subnet=str(cfg.subnet), wg_first=True)
    elif is_v6_tunnel:
        # ── v6 SSTP/PPTP management tunnel path (accel-ppp) ──
        try:
            tcfg = rmt.load_config()
        except rmt.RouterMgmtTunnelError as exc:
            flash(f"تعذّر قراءة إعدادات نفق الإدارة: {exc}", "error")
            return redirect(url_for("radius.mt_setup_form"))
        username = (nas.get("management_secret_ref")
                    or rmt.tunnel_username(nas["name"]))
        iface = (nas.get("management_tunnel_interface_name")
                 or (rmt.SSTP_IFACE_NAME if mgmt_type == "sstp_mgmt"
                     else rmt.PPTP_IFACE_NAME))
        if mgmt_pw:
            mgmt_pw_revealed = True
            if mgmt_type == "sstp_mgmt":
                tunnel_block = render_sstp_mgmt_block(
                    nas_name=nas["name"], accel_host=tcfg.accel_host,
                    username=username, password=mgmt_pw,
                    port=tcfg.sstp_port, iface=iface,
                )
            else:
                tunnel_block = render_pptp_mgmt_block(
                    nas_name=nas["name"], accel_host=tcfg.accel_host,
                    username=username, password=mgmt_pw, iface=iface,
                )
        # else: password already shown once; template shows a rotate notice.
        radius_server_ip = str(tcfg.server_ip)  # router dials RADIUS over tunnel
        api_allowed_address = str(tcfg.pool)

    try:
        script = render_routeros_script(
            nas_name=nas["name"],
            api_user=nas["api_user"],
            api_password=nas["api_password"],
            radius_secret=nas["secret"],
            server_ip=radius_server_ip,
            ros_version=ros_version,
            api_port=int(nas.get("api_port") or 8728),
            coa_port=int(nas.get("coa_port") or 3799),
            wg_block=wg_block,
            tunnel_block=tunnel_block,
            api_allowed_address=api_allowed_address,
        )
    except ValueError as exc:
        flash(f"تعذّر توليد السكربت: {exc}", "error")
        return redirect(url_for("radius.mt_setup_form"))

    # Section line-ranges so each «شرح الكود» row jumps + highlights the right
    # lines in the code card (the praised onboarding treatment). Computed from
    # the rendered script (never hard-coded) so it stays correct if a template
    # changes. Order MUST match the template's explain list: the WG path shows 6
    # rows (واجهة/peer/عنوان النفق/مستخدم API/تفعيل API+RADIUS+CoA/ربط هوتسبوت
    # وPPP); the legacy path shows the last 3 (no WG block).
    _ranges = script_section_lines(script)

    def _span(*keys):
        present = [_ranges[k] for k in keys if k in _ranges]
        return (present[0][0], present[-1][1]) if present else None

    if is_vpn_row:
        explain_ranges = [
            _span("0a"), _span("0b"), _span("0c"),
            _span("1"), _span("2", "3", "4"), _span("5"),
        ]
    else:
        explain_ranges = [_span("1"), _span("2", "3", "4"), _span("5")]

    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كانت صفحة السكربت تمرّر حالة كشف الأنفاق وتحذيراتها. حُذفت.
    return render_template(
        "radius/mt_setup_script.html",
        nas=nas,
        script=script,
        explain_ranges=explain_ranges,
        server_ip=radius_server_ip,
        ros_version=ros_version,
        is_vpn_row=is_vpn_row,
        wg_priv_revealed=wg_priv_revealed,
        is_v6_tunnel=is_v6_tunnel,
        mgmt_pw_revealed=mgmt_pw_revealed,
        mgmt_transport=("SSTP" if mgmt_type == "sstp_mgmt" else "PPTP") if is_v6_tunnel else "",
        dashboard_url=url_for("radius.mt_dashboard", nas_id=nas["id"]),
    )


# ─── v6 SSTP/PPTP management-tunnel credentials (dedicated UI) ─────────────
#
# These back the "إعدادات نفق SSTP" page: they expose the rtr- account's RADIUS
# state ("Synced to RADIUS"), a real "Test SSTP/RADIUS Login" diagnostic that
# distinguishes every failure mode, a re-sync action (idempotent), a password
# reset (reveal-once), the generated MikroTik client block, and a preview of
# the generated accel-ppp config + startup health checks — all from the UI.

def _v6_nas_row_or_404(nas_id: int) -> dict:
    row = db().execute(
        "SELECT * FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        abort(404)
    nas = dict(row)
    if str(nas.get("management_tunnel_type") or "") not in ("sstp_mgmt", "pptp_mgmt"):
        # Only v6 SSTP/PPTP tunnels have an rtr- credential surface.
        abort(404)
    return nas


def _tunnel_user_for(nas: dict) -> str:
    """The rtr- username for this row — prefer the persisted secret_ref, else
    derive from the router name (matches provisioning)."""
    ref = str(nas.get("management_secret_ref") or "").strip()
    if ref.startswith(rmt.TUNNEL_USER_PREFIX):
        return ref
    return rmt.tunnel_username(nas.get("name") or "")


def mt_sstp_credentials(nas_id: int):
    nas = _v6_nas_row_or_404(nas_id)
    username = _tunnel_user_for(nas)
    mgmt_type = str(nas.get("management_tunnel_type") or "")
    transport = "SSTP" if mgmt_type == "sstp_mgmt" else "PPTP"

    status = rmt.tunnel_radius_status(username, tenant_id=_tid())

    # Reveal-once artefacts stashed by the action endpoints.
    diag = session.pop(f"_sstp_diag_{nas_id}", None)
    revealed_pw = session.pop(f"_sstp_pw_{nas_id}", None)

    # accel server connection facts + generated MikroTik client block.
    accel_host = ""
    sstp_port = rmt.ACCEL_SSTP_PORT_DEFAULT
    mikrotik_block = ""
    try:
        cfg = rmt.load_config()
        accel_host = cfg.accel_host
        sstp_port = cfg.sstp_port
    except rmt.RouterMgmtTunnelError:
        cfg = None
    if revealed_pw:
        try:
            if mgmt_type == "sstp_mgmt":
                mikrotik_block = render_sstp_mgmt_block(
                    nas_name=nas.get("name") or "", accel_host=accel_host,
                    username=username, password=revealed_pw, port=sstp_port,
                    iface=str(nas.get("management_tunnel_interface_name")
                              or rmt.SSTP_IFACE_NAME))
            else:
                mikrotik_block = render_pptp_mgmt_block(
                    nas_name=nas.get("name") or "", accel_host=accel_host,
                    username=username, password=revealed_pw,
                    iface=str(nas.get("management_tunnel_interface_name")
                              or rmt.PPTP_IFACE_NAME))
        except ValueError:
            mikrotik_block = ""

    # accel-ppp generated config preview + health checks (best-effort).
    accel_conf = ""
    health = []
    try:
        from ..services import accel_config as ac
        params = ac.params_from_settings(cfg=cfg) if cfg else ac.params_from_settings()
        accel_conf = ac.generate_accel_conf(params)
        health = ac.run_health_checks(params)
    except Exception:  # noqa: BLE001 — preview must never 500 the page
        accel_conf = ""
        health = []

    # Remote access (Open WinBox) — active session for this router (if any).
    remote = None
    try:
        from ..services import router_remote_access as ra
        from ..db.repos import router_remote_sessions_repo as sr
        if ra.enabled():
            s = sr.active_for_router(_tid(), nas_id)
            if s:
                rhost = ra.public_host()
                s["endpoint"] = (f"{rhost}:{s['public_port']}" if rhost
                                 else f"<PANEL_PUBLIC_IP>:{s['public_port']}")
                s["seconds_left"] = ra.seconds_remaining(s)
                s["unrestricted"] = ra.is_unrestricted(s)
                s["source_label"] = ra.source_label(s)
            remote = {"enabled": True, "session": s, "ttl_min": ra.ttl_minutes()}
        else:
            remote = {"enabled": False, "session": None, "ttl_min": 0}
    except Exception:  # noqa: BLE001 — remote panel must never 500 the page
        remote = {"enabled": False, "session": None, "ttl_min": 0}

    # Section line-ranges for the «شرح الكود» rows (jump + highlight in the code
    # card). The MikroTik mgmt block is a comment header + a single
    # ``/interface ...-client add`` command, so the create/credentials/encryption
    # rows all point at that one command line (credentials and crypto settings
    # share the same physical command). The accel-ppp config is one logical file.
    mt_explain_ranges = None
    if mikrotik_block:
        _cmd = block_command_line(mikrotik_block)
        mt_explain_ranges = [(1, _cmd), (_cmd, _cmd), (_cmd, _cmd)]
    accel_explain_ranges = None
    if accel_conf:
        accel_explain_ranges = [(1, block_line_count(accel_conf))]

    return render_template(
        "radius/sstp_credentials.html",
        nas=nas, username=username, transport=transport,
        mgmt_type=mgmt_type, status=status, diag=diag,
        revealed_pw=revealed_pw, mikrotik_block=mikrotik_block,
        mt_explain_ranges=mt_explain_ranges,
        accel_explain_ranges=accel_explain_ranges,
        accel_host=accel_host, sstp_port=sstp_port,
        accel_conf=accel_conf, health=health,
        diag_labels=_SSTP_DIAG_LABELS,
        remote=remote,
        script_url=url_for("radius.mt_setup_script", nas_id=nas_id),
        onboarding_url=url_for("radius.mt_onboarding_script", nas_id=nas_id),
    )


def mt_sstp_test(nas_id: int):
    nas = _v6_nas_row_or_404(nas_id)
    username = _tunnel_user_for(nas)
    password = (request.form.get("password") or "").strip() or None
    diag = rmt.diagnose_tunnel_login(username, tenant_id=_tid(), password=password)
    session[f"_sstp_diag_{nas_id}"] = diag
    return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))


def mt_sstp_sync(nas_id: int):
    nas = _v6_nas_row_or_404(nas_id)
    username = _tunnel_user_for(nas)
    try:
        res = rmt.ensure_tunnel_radius_user(username, tenant_id=_tid())
    except rmt.RouterMgmtTunnelError as exc:
        flash(f"تعذّرت المزامنة مع RADIUS: {exc}", "error")
        return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))
    # Keep nas_devices' tunnel IP consistent with what RADIUS now holds.
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET management_remote_address=?, vpn_peer_address=? "
            "WHERE id=? AND tenant_id=?",
            (str(res.tunnel_ip), str(res.tunnel_ip), nas_id, _tid()),
        )
    flash("تمّت المزامنة مع RADIUS — الحساب جاهز لمصادقة MSCHAP-v2.", "success")
    return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))


def mt_sstp_reset(nas_id: int):
    nas = _v6_nas_row_or_404(nas_id)
    username = _tunnel_user_for(nas)
    # Operator may supply a specific password (to match what the router already
    # sends); otherwise we generate a fresh strong one.
    supplied = (request.form.get("password") or "").strip() or None
    try:
        res = rmt.ensure_tunnel_radius_user(
            username, tenant_id=_tid(), password=supplied)
    except rmt.RouterMgmtTunnelError as exc:
        flash(f"تعذّر تعيين كلمة المرور: {exc}", "error")
        return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))
    # Reveal once on the next render so the operator can copy the new MikroTik
    # block. The canonical copy stays in radcheck.
    session[f"_sstp_pw_{nas_id}"] = res.password
    flash("كلمة مرور النفق جاهزة — تُعرض مرة واحدة. انسخ إعدادات MikroTik أدناه.",
          "success")
    return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))


#: UI labels + remediation for each diagnostic code (Arabic).
_SSTP_DIAG_LABELS = {
    rmt.DIAG_OK: ("جاهز", "الحساب مهيّأ لمصادقة MSCHAP-v2 عبر SSTP/PPTP.", "green"),
    rmt.DIAG_INVALID_USER: (
        "مستخدم غير موجود",
        "لا حساب rtr- في RADIUS — اضغط «مزامنة مع RADIUS» لإنشائه.", "red"),
    rmt.DIAG_MISSING_SECRET: (
        "لا كلمة مرور",
        "الحساب موجود بلا سرّ — أعد تعيين كلمة المرور.", "red"),
    rmt.DIAG_MSCHAP_INCOMPATIBLE: (
        "سرّ غير متوافق مع MSCHAP",
        "السرّ المخزّن غير قابل للعكس (لا يصلح لـMSCHAP-v2) — أعد تعيين كلمة "
        "المرور لكتابة Cleartext/NT-Password.", "red"),
    rmt.DIAG_DISABLED: (
        "الحساب معطّل",
        "الحساب مضبوط على الرفض (Auth-Type := Reject) — أعد المزامنة لتفعيله.",
        "amber"),
    rmt.DIAG_EXPIRED: (
        "منتهي الصلاحية",
        "تاريخ انتهاء الحساب مضى — حدّث الصلاحية أو أعد المزامنة.", "amber"),
    rmt.DIAG_NO_FRAMED_IP: (
        "لا عنوان نفق ثابت",
        "لا Framed-IP — أعد المزامنة لتثبيت عنوان النفق.", "amber"),
    rmt.DIAG_WRONG_PASSWORD: (
        "كلمة المرور خاطئة",
        "كلمة المرور المُدخلة لا تطابق المخزّنة — صحّحها على الراوتر أو أعد "
        "تعيينها.", "red"),
}


# ─── SSTP/PPTP credential MANAGEMENT surface (list / view / edit / delete) ──
#
# A full from-UI management table for ALL SSTP/PPTP management-tunnel accounts
# (rtr-*). The owner can list every account, reveal its plaintext password
# (the secret is MSCHAP-reversible by design), enable/disable, set expiry, reset
# the password, and delete — no terminal. rtr-* usernames are DERIVED from the
# router (provider/central architecture) so the username itself is read-only;
# everything else is editable here.

def _require_known_tunnel_user(username: str) -> str:
    """Validate that ``username`` is a real rtr- account in this tenant before
    any mutation — prevents writing arbitrary radcheck rows from the form."""
    username = (username or "").strip()
    if not username.startswith(rmt.TUNNEL_USER_PREFIX):
        abort(400)
    from ..db.repos import freeradius_repo as _fr
    if not _fr.list_user_check(_tid(), username):
        abort(404)
    return username


def mt_sstp_users():
    accounts = rmt.list_tunnel_accounts(_tid())
    return render_template(
        "radius/sstp_users.html",
        accounts=accounts,
        diag_labels=_SSTP_DIAG_LABELS,
        _sstp=True,
    )


def mt_sstp_user_toggle():
    username = _require_known_tunnel_user(request.form.get("username") or "")
    enabled = (request.form.get("enabled") or "").strip().lower() in ("1", "true", "on", "yes")
    rmt.set_tunnel_enabled(username, tenant_id=_tid(), enabled=enabled)
    flash(("تم تفعيل" if enabled else "تم تعطيل") + f" الحساب {username}.", "success")
    return redirect(url_for("radius.mt_sstp_users"))


def mt_sstp_user_expiry():
    username = _require_known_tunnel_user(request.form.get("username") or "")
    raw = (request.form.get("expire_at") or "").strip()
    expire_at = None
    if raw:
        # Accept an HTML <input type=date/datetime-local> value.
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                expire_at = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if expire_at is None:
            flash("صيغة تاريخ الانتهاء غير مفهومة.", "error")
            return redirect(url_for("radius.mt_sstp_users"))
    rmt.set_tunnel_expiry(username, tenant_id=_tid(), expire_at=expire_at)
    flash(("حُدّثت صلاحية " if expire_at else "أُزيلت صلاحية ") + username + ".",
          "success")
    return redirect(url_for("radius.mt_sstp_users"))


def mt_sstp_user_reset():
    username = _require_known_tunnel_user(request.form.get("username") or "")
    supplied = (request.form.get("password") or "").strip() or None
    try:
        rmt.ensure_tunnel_radius_user(username, tenant_id=_tid(), password=supplied)
    except rmt.RouterMgmtTunnelError as exc:
        flash(f"تعذّر تعيين كلمة المرور: {exc}", "error")
        return redirect(url_for("radius.mt_sstp_users"))
    flash(f"حُدّثت كلمة مرور {username} — مرئية في الجدول.", "success")
    return redirect(url_for("radius.mt_sstp_users"))


def mt_sstp_user_delete():
    username = _require_known_tunnel_user(request.form.get("username") or "")
    rmt.deprovision_tunnel(username, tenant_id=_tid())
    flash(f"حُذف حساب النفق {username} من RADIUS.", "success")
    return redirect(url_for("radius.mt_sstp_users"))


# ─── WireGuard management-tunnel surface (v7 parallel to the SSTP page) ──────
#
# v7 routers reach the panel over a WireGuard management tunnel (no RADIUS
# account — auth IS the WG key exchange). This surface lists every WG-managed
# router with its REAL connection facts (tunnel IP, public key, interface,
# peer-on-server presence, reachability) and offers the WG-appropriate actions:
# regenerate keys (rotate on the server + one-time reveal via the setup script),
# remove peer, show config. No password reveal / MSCHAP diagnostics (N/A for WG).

def _wg_nas_id_from_form() -> int:
    from ..services import wireguard_mgmt as wg
    try:
        nas_id = int(request.form.get("nas_id") or 0)
    except (TypeError, ValueError):
        abort(400)
    if not nas_id or not wg.get_wg_nas(_tid(), nas_id):
        abort(404)
    return nas_id


def mt_wg_peers():
    from ..services import wireguard_mgmt as wg
    peers = wg.list_mgmt_peers(_tid())
    server = wg.server_info()
    return render_template(
        "radius/wg_peers.html",
        peers=peers, server=server, _wg=True,
    )


def mt_wg_peer_regenerate():
    from ..services import wireguard_mgmt as wg
    nas_id = _wg_nas_id_from_form()
    try:
        priv = wg.regenerate_peer(_tid(), nas_id)
    except wg.WireguardMgmtError as exc:
        flash(f"تعذّر توليد مفاتيح WireGuard: {exc}", "error")
        return redirect(url_for("radius.mt_wg_peers"))
    # One-time reveal through the SAME mechanism the wizard uses: stash the
    # private key for one render of the setup-script page, then it self-deletes.
    session[f"_wg_router_priv_{nas_id}"] = priv
    flash("وُلِّدت مفاتيح WireGuard جديدة — انسخ سكربت الراوتر الآن "
          "(يُعرض المفتاح الخاصّ مرّة واحدة).", "success")
    return redirect(url_for("radius.mt_setup_script", nas_id=nas_id))


def mt_wg_peer_remove():
    from ..services import wireguard_mgmt as wg
    nas_id = _wg_nas_id_from_form()
    try:
        removed = wg.remove_peer(_tid(), nas_id)
    except wg.WireguardMgmtError as exc:
        flash(f"تعذّر إزالة peer: {exc}", "error")
        return redirect(url_for("radius.mt_wg_peers"))
    if removed:
        flash("أُزيل peer الراوتر من الخادم — يتوقّف النفق حتى إعادة التوليد.",
              "success")
    else:
        flash("لا peer مُسجَّل لهذا الراوتر على الخادم.", "info")
    return redirect(url_for("radius.mt_wg_peers"))


def mt_wg_details(nas_id: int):
    """Per-router WireGuard details — the v7 sibling of the SSTP credentials
    page (``/mt/<id>/sstp``). Shows this router's REAL connection facts (tunnel
    IP, AllowedIPs, public key with reveal, interface, server endpoint), an
    honest status, a router-side config PREVIEW (the private key is never stored,
    so it shows a placeholder + a regenerate action that reveals it once), plus
    the WG-appropriate actions (regenerate keys / remove peer)."""
    from ..services import wireguard_mgmt as wg
    peer = wg.get_mgmt_peer(_tid(), nas_id)
    if peer is None:
        abort(404)
    server = wg.server_info()

    # Router-side WireGuard config PREVIEW. The private key belongs on the router
    # and is never persisted here, so the preview uses a clear placeholder; the
    # real key is revealed exactly once via «إعادة توليد المفاتيح» (the setup
    # script). Best-effort — never 500 the page if the WG env isn't configured.
    wg_preview = ""
    try:
        if server.configured and peer.get("tunnel_ip"):
            cfg = wpm.load_config()
            wg_preview = render_wg_block(
                nas_name=peer["name"],
                router_private_key="<المفتاح-الخاص-يظهر-مرّة-عند-إعادة-التوليد>",
                server_pubkey=cfg.server_pubkey,
                server_endpoint=cfg.server_endpoint,
                allowed_subnet=str(cfg.subnet),
                router_tunnel_ip=f"{peer['tunnel_ip']}/{cfg.subnet.prefixlen}",
                keepalive_sec=wpm.DEFAULT_KEEPALIVE_SEC,
                ros_version="7",
                mgmt_server_ip=str(cfg.server_ip),
            )
    except Exception:  # noqa: BLE001 — preview must never 500 the page
        wg_preview = ""

    # Section line-ranges so each «شرح الكود» row jumps + highlights the right
    # lines in the WireGuard preview card. Only when a preview is actually
    # rendered (else the rows stay non-interactive instead of jumping to nothing).
    # Order matches the template's 4 rows: واجهة WG / ربط peer / عنوان النفق /
    # توجيه RADIUS والإدارة (the management-restriction tail, 0d+0e).
    wg_explain_ranges = None
    if wg_preview:
        _wr = script_section_lines(wg_preview)

        def _wspan(*keys):
            present = [_wr[k] for k in keys if k in _wr]
            return (present[0][0], present[-1][1]) if present else None

        wg_explain_ranges = [
            _wspan("0a"), _wspan("0b"), _wspan("0c"), _wspan("0d", "0e"),
        ]

    return render_template(
        "radius/wg_details.html",
        peer=peer, server=server, wg_preview=wg_preview,
        wg_explain_ranges=wg_explain_ranges,
        remote=_remote_context(nas_id),
        script_url=url_for("radius.mt_setup_script", nas_id=nas_id),
    )


# ─── Onboarding script (phase 1) — one-paste full RouterOS setup ─────────────
#
# Surfaces the comprehensive HobeRadius-native onboarding script for a v6 SSTP
# router: tunnel + RADIUS + API user + ORDERED firewall + self-heal + backup,
# all from the router's real data + panel settings. Secrets are unique per
# router (the rtr- tunnel password + the per-NAS RADIUS secret) and embedded for
# the operator to paste once.

def _onboarding_walled_garden() -> list:
    raw = str(env_settings.env("HOBERADIUS_WALLED_GARDEN", "") or "")
    return [e.strip() for e in raw.replace(";", ",").replace("\n", ",").split(",")
            if e.strip()]


def mt_onboarding_script(nas_id: int):
    from ..services.router_onboarding_script import (
        OnboardingParams, OnboardingScriptError, build_onboarding_script,
        explain_sections, split_sections)

    nas = _v6_nas_row_or_404(nas_id)
    username = _tunnel_user_for(nas)
    mgmt_type = str(nas.get("management_tunnel_type") or "")
    transport = "SSTP" if mgmt_type == "sstp_mgmt" else "PPTP"

    try:
        cfg = rmt.load_config()
    except rmt.RouterMgmtTunnelError as exc:
        flash(f"خادم accel غير مهيّأ: {exc}", "error")
        return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))

    # The rtr- account holds the UNIQUE tunnel password. Ensure it exists, then
    # reveal it (the script embeds it). ensure also pins a stable Framed-IP.
    st = rmt.tunnel_radius_status(username, tenant_id=_tid(), reveal_secret=True)
    if not st.exists or not st.cleartext or not st.framed_ip:
        try:
            res = rmt.ensure_tunnel_radius_user(username, tenant_id=_tid(), cfg=cfg)
            tunnel_pw, tunnel_ip = res.password, str(res.tunnel_ip)
        except rmt.RouterMgmtTunnelError as exc:
            flash(f"تعذّر تجهيز حساب النفق: {exc}", "error")
            return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))
    else:
        tunnel_pw, tunnel_ip = st.cleartext, st.framed_ip

    params = OnboardingParams(
        router_name=str(nas.get("name") or ""),
        router_id=int(nas_id),
        accel_host=cfg.accel_host,
        sstp_port=int(cfg.sstp_port),
        tunnel_user=username,
        tunnel_password=tunnel_pw,
        tunnel_ip=tunnel_ip,
        radius_ip=str(cfg.server_ip),
        radius_secret=str(nas.get("secret") or ""),
        api_user=str(nas.get("api_user") or "hobe-api"),
        api_password=str(nas.get("api_password") or ""),
        walled_garden=_onboarding_walled_garden(),
        block_page_url=str(env_settings.env("HOBERADIUS_BLOCK_PAGE_URL", "") or ""),
        hotspot_pool=str(env_settings.env("HOBERADIUS_HOTSPOT_POOL", "10.5.50.0/24")),
        pppoe_pool=str(env_settings.env("HOBERADIUS_PPPOE_POOL", "10.5.60.0/24")),
    )
    try:
        script = build_onboarding_script(params)
    except OnboardingScriptError as exc:
        flash(
            f"تعذّر توليد السكربت — بيانات الراوتر ناقصة/ضعيفة: {exc}. "
            "تحقّق من سرّ RADIUS وكلمة مرور النفق.", "error")
        return redirect(url_for("radius.mt_sstp_credentials", nas_id=nas_id))

    _sections = split_sections(script)
    return render_template(
        "radius/onboarding_script.html",
        nas=nas, username=username, transport=transport,
        script=script, sections=_sections,
        explanations=explain_sections(_sections),
        script_lines=script.split("\n"), tunnel_ip=tunnel_ip,
        accel_host=cfg.accel_host, sstp_port=cfg.sstp_port,
        sstp_url=url_for("radius.mt_sstp_credentials", nas_id=nas_id),
    )


# ─── Remote access (Open WinBox) over the SSTP tunnel ────────────────────────
#
# Opens a managed, source-IP-locked, time-boxed nginx-stream forward
# PANEL_PUBLIC_IP:<port> → router_tunnel_ip:8291 so an admin can WinBox into the
# router over the tunnel. Secure-on-demand by default (auto-closes at expiry).

def _client_ip() -> str:
    """The admin's public IP as seen at the panel (for the forward source-lock).
    Honours X-Forwarded-For (nginx sets it) then remote_addr."""
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return xff or (request.remote_addr or "")


def _remote_nas_or_404(nas_id: int) -> dict:
    """Fetch a router that supports remote access — ANY managed tunnel router
    (v6 SSTP/PPTP OR v7 WireGuard), not just v6. WinBox-over-tunnel works for
    both (the forward targets the router's tunnel IP, resolved generically by
    router_remote_access.router_tunnel_ip from vpn_peer_address/address)."""
    row = db().execute(
        "SELECT * FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        abort(404)
    return dict(row)


def _remote_return_url(nas_id: int, nas: "dict | None" = None) -> str:
    """The details page to return to after a remote-access action — the one
    matching this router's tunnel type (SSTP page for v6, WG page for v7)."""
    if nas is None:
        nas = _remote_nas_or_404(nas_id)
    mtype = str(nas.get("management_tunnel_type") or "").strip().lower()
    if mtype in ("sstp_mgmt", "pptp_mgmt"):
        return url_for("radius.mt_sstp_credentials", nas_id=nas_id)
    from ..services import wireguard_mgmt as wg
    if wg.get_wg_nas(_tid(), nas_id):
        return url_for("radius.mt_wg_details", nas_id=nas_id)
    return url_for("radius.mt_sstp_credentials", nas_id=nas_id)


def _remote_context(nas_id: int) -> dict:
    """Build the WinBox remote-access view context (active session + ttl),
    shared verbatim by the SSTP and WireGuard details pages."""
    try:
        from ..services import router_remote_access as ra
        from ..db.repos import router_remote_sessions_repo as sr
        if not ra.enabled():
            return {"enabled": False, "session": None, "ttl_min": 0}
        s = sr.active_for_router(_tid(), nas_id)
        if s:
            rhost = ra.public_host()
            s["endpoint"] = (f"{rhost}:{s['public_port']}" if rhost
                             else f"<PANEL_PUBLIC_IP>:{s['public_port']}")
            s["seconds_left"] = ra.seconds_remaining(s)
            s["unrestricted"] = ra.is_unrestricted(s)
            s["source_label"] = ra.source_label(s)
        return {"enabled": True, "session": s, "ttl_min": ra.ttl_minutes()}
    except Exception:  # noqa: BLE001 — remote panel must never 500 the page
        return {"enabled": False, "session": None, "ttl_min": 0}


def _is_wg_managed_row(nas: dict) -> bool:
    """A router reached over a WireGuard tunnel (not SSTP/PPTP). Mirrors
    ``router_remote_access._is_wg_managed`` / ``wireguard_mgmt._is_wg_row``."""
    mtype = str(nas.get("management_tunnel_type") or "").strip().lower()
    if mtype in ("sstp_mgmt", "pptp_mgmt"):
        return False
    mode = str(nas.get("connection_mode") or "").strip().lower()
    has_pub = bool(str(nas.get("vpn_public_key") or "").strip())
    iface = str(nas.get("vpn_interface") or "").strip().lower()
    return mode == "vpn" or has_pub or iface.startswith("wg")


def _wg_winbox_hint(nas: dict) -> str:
    """Actionable troubleshooting line for «تفعيل WinBox» on a WireGuard router.
    Distinguishes the two failure modes so a closed WinBox isn't a dead-end:

      • tunnel DOWN — the last reachability probe failed → the forward to the WG
        IP would close too; fix the handshake first.
      • tunnel UP but WinBox closes — the router-side ACL/WG isn't set → re-paste
        the (now idempotent) WireGuard block ONCE to bind WinBox to the tunnel.

    Returns "" for non-WG routers (SSTP path is unchanged)."""
    if not _is_wg_managed_row(nas):
        return ""
    check = str(nas.get("last_check_status") or "").strip().lower()
    if check in ("timeout", "unreachable", "down"):
        return ("ملاحظة: آخر فحص يُظهر أن الراوتر لا يستجيب عبر نفق WireGuard — "
                "النفق على الأرجح غير متّصل، وأيّ اتصال WinBox سيُغلق أيضًا. "
                "تحقّق من مصافحة WireGuard (الـpeer على الخادم + endpoint) أولًا.")
    return ("إن أُغلق WinBox رغم فتح المنفذ: النفق متّصل لكن الراوتر يرفض المصدر. "
            "أعد لصق «بلوك WireGuard» المُحدَّث على الراوتر مرّة واحدة (صار idempotent "
            "— يَمسح أي تكرار من لصقات سابقة ثم يَفتح WinBox على شبكة النفق). "
            "صفحة «اتصالات WireGuard» ← تفاصيل الراوتر ← انسخ البلوك.")


def mt_remote_winbox_open(nas_id: int):
    from ..services import router_remote_access as ra
    nas = _remote_nas_or_404(nas_id)
    back = _remote_return_url(nas_id, nas)
    # always-on (persistent, no expiry) — opt-in per router.
    persistent = (request.form.get("persistent") or "").strip() in ("1", "on", "true")
    # Source restriction is OPTIONAL and defaults to «any». The radio is
    # authoritative: "any" clears any stray value left in the (hidden) field so we
    # never restrict by accident; "restrict" requires an IP/CIDR — an empty field
    # is surfaced rather than silently opening to anyone. When source_mode is
    # absent (programmatic callers), infer it from allowed_source.
    source_mode = (request.form.get("source_mode") or "").strip().lower()
    allowed_source = (request.form.get("allowed_source") or "").strip()
    if not source_mode:
        source_mode = "restrict" if allowed_source else "any"
    if source_mode == "restrict":
        if not allowed_source:
            flash("اخترتَ التقييد على IP/CIDR لكن لم تُدخل عنوانًا — اكتب IP أو "
                  "نطاق CIDR، أو اختر «من أي مكان».", "error")
            return redirect(back)
    else:
        allowed_source = ""   # «any» — ignore any stray value in the hidden field
    try:
        res = ra.open_session(
            tenant_id=_tid(), router_id=nas_id, source_ip=_client_ip(),
            opened_by=_actor(), service="winbox",
            persistent=persistent or None, allowed_source=allowed_source)
        scope = ("من أي مكان (بدون قيد IP)" if res.get("unrestricted")
                 else f"مقفول على {res['source_ip']}")
        if res.get("always_on"):
            flash(
                f"فُتح WinBox بوضع دائم — الصق في WinBox: {res['endpoint']} "
                f"({scope}). يبقى مفتوحًا حتى الإغلاق اليدويّ.",
                "success")
        else:
            flash(
                f"فُتح WinBox — الصق في WinBox: {res['endpoint']} "
                f"({scope}). يُغلق تلقائيًّا عند الانتهاء.",
                "success")
        # For a WireGuard-managed router, surface WHY a connection might still
        # fail (so a closed WinBox isn't a silent dead-end): the tunnel being
        # down vs the router-side ACL not yet set. Distinct, actionable.
        hint = _wg_winbox_hint(nas)
        if hint:
            flash(hint, "info")
    except ra.RemoteAccessError as exc:
        flash(f"تعذّر فتح WinBox: {exc}", "error")
    except RuntimeError as exc:
        flash(f"تعذّر فتح WinBox: {exc}", "error")
    return redirect(back)


def mt_remote_close(nas_id: int, session_id: int):
    from ..services import router_remote_access as ra
    nas = _remote_nas_or_404(nas_id)
    ra.close_session(tenant_id=_tid(), session_id=session_id,
                     closed_by=_actor(), source_ip=_client_ip())
    flash("أُغلقت جلسة الوصول البعيد.", "success")
    return redirect(_remote_return_url(nas_id, nas))


def mt_remote_sessions():
    """Active + recent remote-access sessions (open/close/audit)."""
    from ..services import router_remote_access as ra
    from ..db.repos import router_remote_sessions_repo as sr
    active = sr.list_active(tenant_id=_tid())
    recent = sr.list_recent(_tid(), limit=50)
    # enrich with router name + countdown + endpoint
    names = {r["id"]: r["name"] for r in db().execute(
        "SELECT id, name FROM nas_devices WHERE tenant_id=?", (_tid(),)).fetchall()}
    host = ra.public_host()
    for s in active:
        s["router_name"] = names.get(s["router_id"], f"#{s['router_id']}")
        s["seconds_left"] = ra.seconds_remaining(s)
        s["endpoint"] = f"{host}:{s['public_port']}" if host else f"<IP>:{s['public_port']}"
        s["unrestricted"] = ra.is_unrestricted(s)
        s["source_label"] = ra.source_label(s)
    for s in recent:
        s["router_name"] = names.get(s["router_id"], f"#{s['router_id']}")
        s["unrestricted"] = ra.is_unrestricted(s)
        s["source_label"] = ra.source_label(s)
    return render_template("radius/remote_sessions.html",
                           active=active, recent=recent, host=host)
