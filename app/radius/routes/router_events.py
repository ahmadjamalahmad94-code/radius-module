"""Router event webhook + netwatch install/remove (Sprint 6).

Public webhook receiver (no Authorization header — RouterOS's
/tool fetch can't add custom headers; we authenticate via the
HMAC token in the query string). Plus three control routes
the dashboard uses to install/remove the per-device netwatch
on the customer router.

Endpoints:
  GET  /api/router-events/netwatch              ← webhook
  POST /admin/radius/network/devices/<id>/netwatch         ← install
  POST /admin/radius/network/devices/<id>/netwatch/remove  ← remove

The webhook is mounted on the same blueprint as everything
else under /admin/radius/, since that's the URL prefix the
public app is reachable on. RouterOS scripts ship with the
full URL (HOBERADIUS_PUBLIC_URL prefix), so the API path is
deliberately verbose to disambiguate from /api/v1.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template,
    request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import (
    nas_repo,
    network_device_checks_repo,
    network_devices_repo,
    tenant_telegram_settings_repo,
)
from ..services import (
    router_event_token,
    router_netwatch_planner,
    telegram_notifier,
)

_LOG = logging.getLogger(__name__)


def register_router_events_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/api/router-events/netwatch",
        "router_events_netwatch_webhook",
        router_events_netwatch_webhook, methods=["GET", "POST"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/netwatch",
        "router_events_netwatch_install",
        router_events_netwatch_install, methods=["POST"],
    )
    bp.add_url_rule(
        "/network/devices/<int:device_id>/netwatch/remove",
        "router_events_netwatch_remove",
        router_events_netwatch_remove, methods=["POST"],
    )


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _nas_dict(nas_dc) -> dict:
    return {
        "id":              nas_dc.id,
        "tenant_id":       nas_dc.tenant_id,
        "name":            nas_dc.name,
        "address":         nas_dc.address,
        "api_port":        nas_dc.api_port,
        "api_user":        nas_dc.api_user,
        "api_password":    nas_dc.api_password,
        "api_use_tls":     nas_dc.api_use_tls,
        "api_timeout_sec": getattr(nas_dc, "api_timeout_sec", 3) or 3,
    }


# ── Webhook ────────────────────────────────────────────────────


def router_events_netwatch_webhook():
    """Receives `?device_id=…&token=…&state=up|down` from
    a customer's MikroTik netwatch script. No header auth —
    HMAC token in the URL is the only credential.

    Returns 200 even on auth failure (so the router doesn't
    retry indefinitely) but logs the failure server-side.
    """
    # Bootstrap: try the device's tenant for token verification.
    # We need device_id BEFORE we can look up tenant_id, but the
    # token covers both — solution is to brute-check across the
    # device's actual tenant_id from DB. Since device_id is
    # global, look it up first then verify.
    try:
        device_id = int(request.args.get("device_id") or 0)
    except ValueError:
        device_id = 0
    if not device_id:
        return jsonify({"ok": False, "error": "device_id missing"}), 200

    # Cross-tenant device lookup — we use the DB directly because
    # the webhook is system-scoped, not Flask-session-scoped.
    from ..db.connection import db
    cur = db().execute(
        "SELECT id, tenant_id, router_id, name FROM network_devices "
        "WHERE id = ?", (device_id,),
    )
    r = cur.fetchone()
    if not r:
        _LOG.warning("[netwatch-webhook] unknown device_id=%s", device_id)
        return jsonify({"ok": False, "error": "unknown device"}), 200

    parsed = router_event_token.split_event(
        int(r["tenant_id"]), request.args,
    )
    if parsed is None:
        _LOG.warning(
            "[netwatch-webhook] auth failed device=%s remote=%s",
            device_id, (request.remote_addr or "?"),
        )
        return jsonify({"ok": False, "error": "auth failed"}), 200

    _device_id, state, _tok = parsed
    status = "up" if state == "up" else "down"

    # Persist the event in the same history table the VPS-side
    # monitor writes to, marked source='router_netwatch' so the
    # operator can filter by origin.
    network_device_checks_repo.record_check(
        device_id=device_id,
        status=status,
        latency_ms=None,
        error_message="",
        source="router_netwatch",
    )
    network_devices_repo.set_last_check(
        tenant_id=int(r["tenant_id"]),
        device_id=device_id,
        status=status,
        latency_ms=None,
    )

    # Fan out via Telegram if the tenant has it configured + the
    # device has alert_enabled. Re-use the same notifier the
    # Sprint-2 monitor uses so wording stays consistent.
    device = network_devices_repo.get_by_id(int(r["tenant_id"]), device_id)
    if device and device.get("alert_enabled") and \
            tenant_telegram_settings_repo.is_configured(int(r["tenant_id"])):
        name = device.get("name") or f"#{device_id}"
        ip = device.get("ip_address") or ""
        if status == "down":
            msg = (
                f"⚠️ <b>الراوتر يقول: انقطع</b> «{name}»\n"
                f"IP: <code>{ip}</code>\n"
                f"المصدر: <code>router netwatch</code>"
            )
        else:
            msg = (
                f"✅ <b>الراوتر يقول: عاد</b> «{name}»\n"
                f"IP: <code>{ip}</code>\n"
                f"المصدر: <code>router netwatch</code>"
            )
        ok, err = telegram_notifier.send_to_tenant(int(r["tenant_id"]), msg)
        delivery = "sent" if ok else ("failed" if err else "skipped")
        network_device_checks_repo.record_alert(
            tenant_id=int(r["tenant_id"]),
            device_id=device_id,
            event_type=f"router_netwatch_{status}",
            delivery=delivery,
            message=msg if ok else f"{msg}\n[error] {err}",
        )

    return jsonify({"ok": True, "device_id": device_id, "state": state}), 200


# ── Control: install / remove ──────────────────────────────────


def _load_pair(device_id: int):
    tenant_id = _tid()
    device = network_devices_repo.get_by_id(tenant_id, device_id)
    if not device:
        abort(404)
    nas_dc = nas_repo.get_nas(tenant_id, device["router_id"])
    if not nas_dc:
        abort(404)
    return device, _nas_dict(nas_dc), tenant_id


def router_events_netwatch_install(device_id: int):
    device, nas, tenant_id = _load_pair(device_id)
    ok, err, info = router_netwatch_planner.install_netwatch(
        nas=nas, tenant_id=tenant_id, device=device,
    )
    if not ok:
        flash(f"فشل التفعيل: {err}", "danger")
    else:
        flash(
            f"فُعّل netwatch على «{device['name']}». "
            f"الراوتر سيُنبّه HobeRadius عند انقطاع/عودة الجهاز.",
            "success",
        )
    return redirect(url_for(
        "radius.network_devices_edit", device_id=device_id,
    ))


def router_events_netwatch_remove(device_id: int):
    device, nas, _ = _load_pair(device_id)
    ok, err = router_netwatch_planner.remove_netwatch(
        nas=nas, device_id=device_id,
    )
    if not ok:
        flash(f"فشل الإلغاء: {err}", "warning")
    else:
        flash(
            f"أُلغي netwatch عن «{device['name']}» على الراوتر.",
            "success",
        )
    return redirect(url_for(
        "radius.network_devices_edit", device_id=device_id,
    ))
