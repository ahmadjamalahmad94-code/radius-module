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
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request,
    session, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import NasDevice
from ..db.connection import db, transaction
from ..services.devices import get_nas_devices_service
from ..services.mt_provisioner import (
    SUPPORTED_ROS_VERSIONS, generate_credentials, render_routeros_script,
    render_wg_block,
)
from ..services import wg_peer_manager as wpm
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
    env_ip = (os.environ.get("HOBERADIUS_PUBLIC_IP") or "").strip()
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
        "/mt/setup", "mt_setup_form", mt_setup_form, methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/setup", "mt_setup_create", mt_setup_create, methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/script",
        "mt_setup_script", mt_setup_script, methods=["GET"],
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
        peers_dir = Path(os.environ.get(wpm.PEERS_DIR_ENV) or wpm.PEERS_DIR_DEFAULT)
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
          nd.connection_mode, nd.api_user, nd.api_port,
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
    for it in items:
        it["mgmt"] = _derive_mgmt_status(
            it, provisioned_ips,
            live_pollable=(it["enabled"] and not it["is_provisioning"]),
        )
    provisioning_count = sum(1 for it in items if it["is_provisioning"])
    # O2 — pass an api_token so the per-row counter poll JS can
    # authenticate against /api/v1/mikrotik/<id>/counters without
    # needing a separate session-bridging step.
    from .mt_dashboard import _ui_api_token   # internal helper reuse
    return render_template(
        "radius/mt_operations.html",
        items=items,
        api_token=_ui_api_token(),
        provisioning_count=provisioning_count,
    )


def mt_setup_form():
    return render_template(
        "radius/mt_setup_form.html",
        default_server_ip=_default_server_ip(),
        ros_versions=SUPPORTED_ROS_VERSIONS,
    )


def mt_setup_create():
    name = (request.form.get("name") or "").strip()
    address = (request.form.get("address") or "").strip()
    ros_version = (request.form.get("ros_version") or "").strip()
    server_ip = (request.form.get("server_ip") or _default_server_ip()).strip()

    # Hard validation. Friendly form-level checks live in the
    # template; this is the last line of defence.
    if not name:
        flash("اكتب اسمًا للراوتر", "error")
        return redirect(url_for("radius.mt_setup_form"))
    if ros_version not in SUPPORTED_ROS_VERSIONS:
        flash("اختر نسخة RouterOS (6 أو 7)", "error")
        return redirect(url_for("radius.mt_setup_form"))
    # For v6 the operator MUST supply an address — RouterOS 6 has
    # no native WireGuard, so we can't auto-provision a tunnel.
    if ros_version == "6" and not address:
        flash(
            "RouterOS 6 لا يدعم WireGuard — اكتب عنوان الراوتر (IP) "
            "للاتصال المباشر",
            "error",
        )
        return redirect(url_for("radius.mt_setup_form"))
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

    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كان راوتر v6 هنا يُجهَّز بنفق إدارة SSTP + نفق ترافيك اختياري وحساب على
    # CHR. حُذف؛ راوتر v6 يُنشأ الآن باتصال مباشر فقط (العنوان اليدوي).
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

    # Phase M — for v7+VPN rows we render the WG block in front of
    # the RADIUS commands. The router's private key only lives in
    # the session for one render (see mt_setup_create), so a
    # refresh after issuing intentionally gives a "key already
    # shown — rotate to re-issue" notice rather than re-printing
    # the secret.
    wg_block = None
    wg_priv_revealed = False
    wg_priv = session.pop(f"_wg_router_priv_{nas_id}", None)
    is_vpn_row = (nas.get("connection_mode") or "").strip().lower() == "vpn"

    if ros_version == "7" and is_vpn_row:
        try:
            cfg = wpm.load_config()
        except ValueError as exc:
            flash(
                f"تعذّر قراءة إعدادات WireGuard من البيئة: {exc}",
                "error",
            )
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
            )
        # else: no private key in session → leave wg_block None.
        # The template surfaces a "private key already issued" notice.

    # For the RADIUS half of the script, `server_ip` is what the
    # router will dial. For VPN rows that's the server's tunnel IP
    # (10.10.0.1), for direct rows it's the operator-supplied IP
    # from ?server_ip=… .
    if is_vpn_row:
        try:
            cfg = wpm.load_config()
            radius_server_ip = str(cfg.server_ip)
        except ValueError:
            radius_server_ip = (
                request.args.get("server_ip") or _default_server_ip() or "<SERVER_IP>"
            )
    else:
        radius_server_ip = (
            request.args.get("server_ip") or _default_server_ip() or "<SERVER_IP>"
        )

    # For VPN rows, lock the router's API service to the WG subnet
    # so the API can't be reached from the LAN/public interfaces.
    # Direct-mode rows leave it open (operator firewalls as needed).
    api_allowed_address = None
    if is_vpn_row:
        try:
            api_allowed_address = str(wpm.load_config().subnet)
        except ValueError:
            api_allowed_address = None
    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كان راوتر v6 هنا يكشف سكربتات أنفاق SSTP/الترافيك وأسرارها لمرة واحدة.
    # حُذف؛ سكربت v6 صار اتصال RADIUS مباشر فقط دون أي كتلة نفق.

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
            api_allowed_address=api_allowed_address,
        )
    except ValueError as exc:
        flash(f"تعذّر توليد السكربت: {exc}", "error")
        return redirect(url_for("radius.mt_setup_form"))

    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كانت صفحة السكربت تمرّر حالة كشف الأنفاق وتحذيراتها. حُذفت.
    return render_template(
        "radius/mt_setup_script.html",
        nas=nas,
        script=script,
        server_ip=radius_server_ip,
        ros_version=ros_version,
        is_vpn_row=is_vpn_row,
        wg_priv_revealed=wg_priv_revealed,
        dashboard_url=url_for("radius.mt_dashboard", nas_id=nas["id"]),
    )
