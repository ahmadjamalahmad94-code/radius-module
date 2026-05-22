"""
routes للتكامل: MikroTik configs (legacy, Phase N2 deprecated) +
Webhooks settings + deliveries.

Phase N2 turns every `/mt/...` handler into HTTP 410 Gone. The
endpoint registrations stay so any `url_for("radius.mt_list")`
in existing templates still resolves — it just renders the
deprecation notice now. The wizard at `/admin/radius/mt/setup`
+ the Operations Center at `/admin/radius/mt/operations` are
the supported replacements.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request, url_for,
)

from ..db.repos import webhooks_repo
from ..core.tenant import DEFAULT_TENANT_ID


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_integration_routes(bp: Blueprint) -> None:
    # MikroTik configs — DEPRECATED in Phase N2. Endpoint names
    # kept so url_for() callers keep resolving, but every handler
    # returns 410 Gone with a pointer to the wizard.
    bp.add_url_rule("/mt", "mt_list", mt_list, methods=["GET"])
    bp.add_url_rule("/mt/new", "mt_new", mt_new, methods=["GET"])
    bp.add_url_rule("/mt", "mt_create", mt_create, methods=["POST"])
    bp.add_url_rule("/mt/<int:cid>/edit", "mt_edit", mt_edit, methods=["GET"])
    bp.add_url_rule("/mt/<int:cid>", "mt_update", mt_update, methods=["POST"])
    bp.add_url_rule("/mt/<int:cid>/delete", "mt_delete", mt_delete, methods=["POST"])
    bp.add_url_rule("/mt/<int:cid>/test", "mt_test", mt_test_run, methods=["POST"])
    # Webhooks
    bp.add_url_rule("/webhooks", "wh_settings", wh_settings, methods=["GET", "POST"])
    bp.add_url_rule("/webhooks/deliveries", "wh_deliveries", wh_deliveries, methods=["GET"])


# ─────────────── MikroTik legacy CRUD — DEPRECATED ───────────────


def _legacy_gone():
    """Render a 410 Gone page that points operators at the
    supported wizard / Operations Center surfaces.

    Returning a real 410 (not 302) keeps automated tools honest:
    bookmarks / scrapers / external links break loudly instead of
    silently redirecting to a different concept.
    """
    return render_template(
        "radius/mt_legacy_gone.html",
        wizard_url=url_for("radius.mt_setup_form"),
        operations_url=url_for("radius.mt_operations"),
    ), 410


def mt_list():
    return _legacy_gone()


def mt_new():
    return _legacy_gone()


def mt_create():
    return _legacy_gone()


def mt_edit(cid: int):
    return _legacy_gone()


def mt_update(cid: int):
    return _legacy_gone()


def mt_delete(cid: int):
    return _legacy_gone()


def mt_test_run(cid: int):
    return _legacy_gone()


# ─────────────── Webhooks ───────────────

def wh_settings():
    if request.method == "POST":
        from ..core.types_saas import WebhookSubscription
        from dataclasses import replace
        subs = webhooks_repo.list_subs(_tid())
        target = (request.form.get("target_url") or "").strip()
        secret = (request.form.get("secret") or "").strip()
        enabled = bool(request.form.get("enabled"))
        if not subs:
            webhooks_repo.upsert_sub(WebhookSubscription(
                id=None, tenant_id=_tid(), target_url=target, secret=secret,
                enabled=enabled,
            ))
        else:
            s = subs[0]
            webhooks_repo.upsert_sub(replace(s, target_url=target, secret=secret, enabled=enabled))
        flash("تم الحفظ.", "success")
        return redirect(url_for("radius.wh_settings"))
    subs = webhooks_repo.list_subs(_tid())
    current = subs[0] if subs else None
    return render_template("radius/wh_settings.html", current=current)


def wh_deliveries():
    status = request.args.get("status") or None
    items = webhooks_repo.list_deliveries(_tid(), status=status, limit=200)
    return render_template("radius/wh_deliveries.html", items=items, status=status)
