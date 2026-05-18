"""
routes للتكامل: MikroTik configs + Webhooks settings + deliveries.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db.repos import mikrotik_repo, webhooks_repo
from ..core.tenant import DEFAULT_TENANT_ID


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_integration_routes(bp: Blueprint) -> None:
    # MikroTik configs
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


# ─────────────── MikroTik ───────────────

def mt_list():
    items = mikrotik_repo.list_configs(_tid())
    return render_template("radius/mt_list.html", items=items)


def mt_new():
    return render_template("radius/mt_form.html", item={}, is_new=True)


def _form_to_dict():
    f = request.form
    return dict(
        name=(f.get("name") or "").strip(),
        host=(f.get("host") or "").strip(),
        port=int(f.get("port") or 8728),
        username=(f.get("username") or "admin").strip(),
        password=f.get("password") or "",
        use_tls=bool(f.get("use_tls")),
        verify_tls=bool(f.get("verify_tls")),
        timeout_sec=int(f.get("timeout_sec") or 10),
        enabled=bool(f.get("enabled")),
    )


def mt_create():
    d = _form_to_dict()
    if not d["host"] or not d["username"]:
        flash("host + username مطلوبان", "error")
        return redirect(url_for("radius.mt_new"))
    mikrotik_repo.create(_tid(), **d)
    flash(f"تم إضافة {d['name']}.", "success")
    return redirect(url_for("radius.mt_list"))


def mt_edit(cid: int):
    it = mikrotik_repo.get(_tid(), cid)
    if not it: abort(404)
    return render_template("radius/mt_form.html", item=it, is_new=False)


def mt_update(cid: int):
    it = mikrotik_repo.get(_tid(), cid)
    if not it: abort(404)
    changes = _form_to_dict()
    # كلمة السر فارغة = لا نُعدّلها
    if not changes["password"]:
        changes.pop("password")
    mikrotik_repo.update(_tid(), cid, **changes)
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.mt_list"))


def mt_delete(cid: int):
    mikrotik_repo.delete(_tid(), cid)
    flash("تم الحذف.", "success")
    return redirect(url_for("radius.mt_list"))


def mt_test_run(cid: int):
    """يجرّب الاتصال ويحدّث last_status + last_seen_at."""
    cfg = mikrotik_repo.get(_tid(), cid)
    if not cfg: abort(404)
    from ..integration.mikrotik import MikrotikClient
    from ..integration.mikrotik.errors import AuthError, ConnectError, MikrotikError
    from ..db.helpers import now_iso
    try:
        with MikrotikClient(
            host=cfg["host"], port=cfg["port"],
            username=cfg["username"], password=cfg["password"],
            use_tls=bool(cfg["use_tls"]), verify_tls=bool(cfg["verify_tls"]),
            timeout=cfg["timeout_sec"],
        ) as c:
            ident = list(c.print_("/system/identity/print"))
            name = ident[0].get("name", "?") if ident else "?"
        mikrotik_repo.update(_tid(), cid, last_status=f"ok: {name}", last_seen_at=now_iso())
        flash(f"اتصال ناجح: {name}", "success")
    except AuthError as e:
        mikrotik_repo.update(_tid(), cid, last_status=f"auth_error: {e}")
        flash(f"خطأ مصادقة: {e}", "error")
    except ConnectError as e:
        mikrotik_repo.update(_tid(), cid, last_status=f"connect_error: {e}")
        flash(f"تعذّر الاتصال: {e}", "error")
    except MikrotikError as e:
        mikrotik_repo.update(_tid(), cid, last_status=f"mt_error: {e}")
        flash(f"خطأ: {e}", "error")
    return redirect(url_for("radius.mt_list"))


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
