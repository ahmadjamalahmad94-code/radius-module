"""CHR tunnels — customer-side view + request (consumer of the signed bridge).

This page only VIEWS tunnels and REQUESTS new ones from the license panel. It
never generates CHR credentials locally and never persists a raw tunnel secret.
A requested tunnel's SSTP user/password is shown ONCE in the response for local
injection and is not stored.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, session, url_for


def register_tunnels_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/tunnels", "tunnels_list", tunnels_list, methods=["GET"])
    bp.add_url_rule("/tunnels/request", "tunnels_request", tunnels_request, methods=["POST"])
    bp.add_url_rule("/tunnels/sync", "tunnels_sync", tunnels_sync, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _render(*, revealed: dict[str, Any] | None = None):
    from ..db.repos import bridge_tunnels_repo
    from ..services.admin_panel_client import AdminBridgeConfig

    config = AdminBridgeConfig.from_env()
    tunnels = bridge_tunnels_repo.list_tunnels(_tid())
    # SIMPLE_LINK contract (يونيو 2026 purge): the bridge is "ready" when it is
    # enabled, the panel URL is HTTPS, and the license_key is set. The legacy
    # ``shared_secret`` field was REMOVED from AdminBridgeConfig — referencing it
    # here raised AttributeError → 500 whenever the bridge was actually
    # configured (the unconfigured state short-circuited on ``enabled`` and
    # rendered fine, which is why this only broke for linked customers).
    bridge_ready = bool(
        config.enabled
        and str(config.base_url or "").lower().startswith("https://")
        and config.license_key
    )
    return render_template(
        "radius/tunnels.html",
        tunnels=tunnels,
        revealed=revealed,
        bridge_ready=bridge_ready,
        base_url=config.base_url,
    )


def tunnels_list():
    return _render()


def tunnels_request():
    from ..services.license_tunnel_bridge import LicenseTunnelBridgeService

    tunnel_type = (request.form.get("tunnel_type") or "sstp").strip().lower()
    label = (request.form.get("label") or "").strip()[:160]
    router_id = (request.form.get("router_id") or "").strip()[:40]
    if tunnel_type not in {"sstp", "pptp", "l2tp", "ipsec"}:
        tunnel_type = "sstp"

    result = LicenseTunnelBridgeService().request_tunnel(
        tenant_id=_tid(), tunnel_type=tunnel_type, router_id=router_id, label=label,
    )
    if not result.get("ok"):
        flash(f"تعذّر طلب النفق: {_status_label(result.get('status'))}.", "error")
        return redirect(url_for("radius.tunnels_list"))

    tunnel = result.get("tunnel") or {}
    creds = result.get("credentials") or {}
    flash(
        f"تم إنشاء النفق «{tunnel.get('remote_name')}». انسخ بيانات الدخول الآن — لن تُعرض مرة أخرى ولا تُخزَّن.",
        "success",
    )
    # Render directly (no redirect) so the one-time credentials live only in this
    # response body — never the session cookie, never the database.
    return _render(revealed={
        "remote_name": tunnel.get("remote_name"),
        "tunnel_type": tunnel.get("tunnel_type"),
        "remote_address": tunnel.get("remote_address"),
        "username": creds.get("username"),
        "password": creds.get("password"),
    })


def tunnels_sync():
    from ..services.license_tunnel_bridge import LicenseTunnelBridgeService

    result = LicenseTunnelBridgeService().sync_tunnels(tenant_id=_tid())
    if result.get("ok"):
        flash(
            "تمت مزامنة الأنفاق: "
            f"{result.get('active_count', 0)} فعّال، "
            f"{result.get('suspended_count', 0)} موقوف، "
            f"{result.get('revoked_count', 0)} ملغى.",
            "success",
        )
    else:
        flash(f"تعذّرت مزامنة الأنفاق: {_status_label(result.get('status'))}.", "error")
    return redirect(url_for("radius.tunnels_list"))


def _status_label(status: Any) -> str:
    labels = {
        "https_required": "الاتصال الآمن (HTTPS) مطلوب",
        "config_missing": "إعدادات الربط غير مكتملة",
        "disabled": "الجسر غير مفعّل",
        "denied": "فشل التحقق من سر الربط أو التوقيع",
        "timeout": "انتهت مهلة الاتصال بلوحة التراخيص",
        "unavailable": "لوحة التراخيص غير متاحة الآن",
        "invalid_payload": "رد لوحة التراخيص غير متوقّع",
    }
    return labels.get(str(status or "").strip().lower(), "حالة غير معروفة من لوحة التراخيص")
