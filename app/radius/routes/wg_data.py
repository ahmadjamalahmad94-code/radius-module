"""إدارة واجهة WireGuard البيانات (wg-data) — عرض، تهيئة، إضافة/حذف/تعطيل peers.

مُسجَّل على blueprint الرئيسي (radius) ضمن _register_all.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for


def register_wg_data_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/wg-data", "wg_data_dashboard", wg_data_dashboard, methods=["GET"])
    bp.add_url_rule("/wg-data/init", "wg_data_init", wg_data_init, methods=["POST"])
    bp.add_url_rule("/wg-data/peers/add", "wg_data_add_peer", wg_data_add_peer, methods=["POST"])
    bp.add_url_rule(
        "/wg-data/peers/<int:peer_id>/remove",
        "wg_data_remove_peer", wg_data_remove_peer, methods=["POST"],
    )
    bp.add_url_rule(
        "/wg-data/peers/<int:peer_id>/suspend",
        "wg_data_suspend_peer", wg_data_suspend_peer, methods=["POST"],
    )
    bp.add_url_rule(
        "/wg-data/peers/<int:peer_id>/activate",
        "wg_data_activate_peer", wg_data_activate_peer, methods=["POST"],
    )
    bp.add_url_rule(
        "/wg-data/sync-quota",
        "wg_data_sync_quota", wg_data_sync_quota, methods=["POST"],
    )


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _render(
    svc: dict | None,
    peers: list[dict],
    *,
    new_peer: dict | None = None,
    client_config: str | None = None,
    allocations: list[dict] | None = None,
):
    from ..services.license_sync_service import get_active_allocations

    allocs = allocations or get_active_allocations(_tid(), service_type="wireguard_data")
    return render_template(
        "radius/wg_data.html",
        svc=svc,
        peers=peers,
        new_peer=new_peer,
        client_config=client_config,
        allocations=allocs,
    )


def wg_data_dashboard():
    from ..services.wg_data_manager import get_service, list_peers

    tid = _tid()
    svc = get_service(tid)
    peers = list_peers(tid, svc["id"]) if svc else []
    return _render(svc, peers)


def wg_data_init():
    from ..services.wg_data_manager import init_service

    tid = _tid()
    alloc_id = request.form.get("allocation_mirror_id", type=int)
    interface_name = (request.form.get("interface_name") or "").strip() or None
    listen_port = request.form.get("listen_port", type=int)
    address_range = (request.form.get("address_range") or "").strip() or None

    try:
        svc = init_service(
            tid, alloc_id,
            interface_name=interface_name,
            listen_port=listen_port,
            address_range=address_range,
        )
        key_path = svc.pop("private_key_written_to", "?")
        flash(
            f"تمت التهيئة. Private key محفوظ في: {key_path} — "
            "احتفظ بنسخة احتياطية منه الآن!",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    except OSError as exc:
        flash(f"خطأ في الكتابة إلى الملف: {exc}", "error")

    return redirect(url_for("radius.wg_data_dashboard"))


def wg_data_add_peer():
    from ..services.wg_data_manager import get_service, list_peers, add_peer

    tid = _tid()
    svc = get_service(tid)
    if not svc:
        flash("هيّئ خدمة WireGuard أولًا.", "error")
        return redirect(url_for("radius.wg_data_dashboard"))

    display_name    = (request.form.get("display_name") or "").strip()
    notes           = (request.form.get("notes") or "").strip()
    speed_mbps      = request.form.get("speed_limit_mbps", type=int)
    transfer_limit  = request.form.get("transfer_limit_bytes", type=int)

    try:
        peer, _client_priv, config = add_peer(
            tenant_id=tid,
            service_id=svc["id"],
            display_name=display_name,
            notes=notes,
            speed_limit_mbps=speed_mbps,
            transfer_limit_bytes=transfer_limit,
        )
    except (ValueError, RuntimeError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("radius.wg_data_dashboard"))

    peers = list_peers(tid, svc["id"])
    return _render(svc, peers, new_peer=peer, client_config=config)


def wg_data_remove_peer(peer_id: int):
    from ..services.wg_data_manager import remove_peer

    ok = remove_peer(_tid(), peer_id)
    flash("تم حذف الـ peer." if ok else "لم يُعثر على الـ peer.", "success" if ok else "error")
    return redirect(url_for("radius.wg_data_dashboard"))


def wg_data_suspend_peer(peer_id: int):
    from ..services.wg_data_manager import suspend_peer

    ok = suspend_peer(_tid(), peer_id)
    flash("تم تعطيل الـ peer." if ok else "الـ peer غير نشط أو غير موجود.", "success" if ok else "error")
    return redirect(url_for("radius.wg_data_dashboard"))


def wg_data_activate_peer(peer_id: int):
    from ..services.wg_data_manager import activate_peer

    ok = activate_peer(_tid(), peer_id)
    flash("تم إعادة تفعيل الـ peer." if ok else "الـ peer غير موقوف أو غير موجود.", "success" if ok else "error")
    return redirect(url_for("radius.wg_data_dashboard"))


def wg_data_sync_quota():
    from ..services.wg_data_manager import sync_quota

    result = sync_quota(_tid())
    if result.get("ok"):
        total_mb = result["total_bytes"] / 1_048_576
        flash(
            f"تم تحديث الكوتا: {result['peers_updated']} peer — "
            f"إجمالي {total_mb:.1f} MB.",
            "success",
        )
    else:
        flash(f"فشل تحديث الكوتا: {result.get('error', '?')}", "error")
    return redirect(url_for("radius.wg_data_dashboard"))
