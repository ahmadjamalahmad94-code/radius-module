"""«تحديث النظام» — per-customer OPT-IN self-update (owner/super only).

The panel SEES an available update and is FREE to take it. Confirm writes the
host-agent request marker; the agent (deploy/updater/) performs the actual
backup → update-to-latest → run-all-migrations → health-check → rollback. This
module never runs docker/git/build — it only signals and reads markers.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
    session, url_for,
)

from ..services import self_update


def register_system_update_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/system/update", "system_update", system_update_page, methods=["GET"])
    bp.add_url_rule("/system/update/check", "system_update_check", system_update_check, methods=["POST"])
    bp.add_url_rule("/system/update/request", "system_update_request", system_update_request, methods=["POST"])
    bp.add_url_rule("/system/update/status", "system_update_status", system_update_status, methods=["GET"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", None) or session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "owner"


def _require_owner_or_super() -> None:
    """The update flow is owner/super-only. 403 for everyone else.

    ``session['is_super_admin']`` is the owner-bypass flag (see
    session_helpers._resolve_is_super — set True for the primary owner AND an
    assigned super_admin). We also accept an explicit primary-owner check as a
    belt-and-braces fallback.
    """
    if bool(session.get("is_super_admin")):
        return
    try:
        from ..db.repos import admins_repo
        if admins_repo.is_primary_owner(session.get("admin_id")):
            return
    except Exception:  # noqa: BLE001
        pass
    abort(403)


def system_update_page():
    _require_owner_or_super()
    tid = _tid()
    state = self_update.get_cached_state(tid)
    progress = self_update.get_progress(tid)
    return render_template(
        "radius/system_update.html",
        state=state,
        progress=progress,
        changelog_html=self_update.changelog_html(state),
        events=self_update.recent_events(tid, limit=15),
    )


def system_update_check():
    """Force a fresh check now (owner clicked «التحقّق الآن»)."""
    _require_owner_or_super()
    state = self_update.check_for_update(_tid(), force=True)
    if _wants_json():
        return jsonify({"ok": True, "state": state})
    if state.get("available"):
        flash("يتوفّر تحديث جديد.", "success")
    elif state.get("ok"):
        flash("أنت على أحدث إصدار.", "info")
    else:
        flash("تعذّر التحقّق من التحديثات الآن.", "warning")
    return redirect(url_for("radius.system_update"))


def system_update_request():
    """OPT-IN confirm → write the host-agent update-request marker."""
    _require_owner_or_super()
    tid = _tid()
    state = self_update.get_cached_state(tid)

    if not state.get("available"):
        return _fail("لا يوجد تحديث متاح.", 409)

    # Requested target: the client may echo the version it saw. We authorise
    # against the cached state's target_version — when the instance is below the
    # hard min_version, target is the INTERMEDIATE step, not the latest, so a
    # blind direct jump is never written.
    target = str(state.get("target_version") or state.get("latest") or "").strip()
    requested = (request.form.get("requested_version") or "").strip() or target
    # Never allow requesting the latest when a direct jump is blocked.
    if state.get("blocked_direct_jump"):
        requested = str(state.get("min_version") or target)

    if not requested:
        return _fail("تعذّر تحديد الإصدار المطلوب.", 400)

    result = self_update.request_update(
        tid,
        requested_version=requested,
        requested_by=int(session.get("admin_id") or 0),
        actor=_actor(),
    )
    if not result.get("ok"):
        return _fail("تعذّر إرسال طلب التحديث. تحقّق من إعداد وكيل التحديث على الخادم.", 500)

    progress = self_update.get_progress(tid)
    if _wants_json():
        return jsonify({"ok": True, "request": result["request"], "progress": progress})
    flash("تم إرسال طلب التحديث. جارٍ التحديث…", "success")
    return redirect(url_for("radius.system_update"))


def system_update_status():
    """Polled by the «جاري التحديث…» screen."""
    _require_owner_or_super()
    return jsonify({"ok": True, "progress": self_update.get_progress(_tid())})


# ── helpers ───────────────────────────────────────────────────────────
def _wants_json() -> bool:
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
        or request.headers.get("X-CSRFToken") is not None
    )


def _fail(message: str, code: int):
    if _wants_json():
        return jsonify({"ok": False, "error": message}), code
    flash(message, "error")
    return redirect(url_for("radius.system_update"))
