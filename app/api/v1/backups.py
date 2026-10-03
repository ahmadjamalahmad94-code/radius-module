"""Backup readiness endpoints."""
from __future__ import annotations
from app.i18n_text import _tr

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.operations import get_operations_service
    return get_operations_service()


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/backups/status", "backups_status",
                    require_api_token(backups_status), methods=["GET"])
    bp.add_url_rule("/backups/run", "backups_run",
                    require_api_token(backups_run), methods=["POST"])
    # zero-w3: the web «نسخة كاملة» (run-all): local → panel (if paid) → Drive,
    # with the full-archive mode. The app ran /backups/run (local only).
    bp.add_url_rule("/backups/run-all", "backups_run_all",
                    require_api_token(backups_run_all), methods=["POST"])
    # zero-w3: Drive is linked in the customer portal (web button → SSO link).
    bp.add_url_rule("/backups/google-drive/portal-link",
                    "backups_google_drive_portal_link",
                    require_api_token(backups_google_drive_portal_link),
                    methods=["POST"])
    bp.add_url_rule("/backups/google-drive/connect",
                    "backups_google_drive_connect",
                    require_api_token(backups_google_drive_connect), methods=["POST"])
    bp.add_url_rule("/backups/google-drive/poll",
                    "backups_google_drive_poll",
                    require_api_token(backups_google_drive_poll), methods=["POST"])
    bp.add_url_rule("/backups/google-drive/status",
                    "backups_google_drive_status",
                    require_api_token(backups_google_drive_status), methods=["GET"])


def backups_status():
    payload = _svc().backup_status(tenant_id=_tid())
    # zero-w3: the SAME Drive status source as the web backups page (the
    # license panel / customer portal) — not the local device-flow state the
    # app could never complete.
    from ...radius.services.backup_drive_link import panel_google_drive_status
    payload["google_drive"] = panel_google_drive_status()
    return ok(payload)


def _wants_full_archive(body: dict) -> bool:
    raw = str(body.get("mode") or body.get("scope") or request.args.get("mode")
              or request.args.get("scope") or "").strip().lower()
    return raw in {"full", "archive", "full_archive"}


def backups_run_all():
    """Mirror of the web ``backups_run_all``: local backup, then the license
    panel upload (paid service), then Drive — one step list. ``mode=full``
    takes a full archive (with the log/telemetry tables) instead of the lean
    core copy. 200 with ``ok`` + ``steps`` like the web JSON."""
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return fail("validation_error", _tr("جسم الطلب يجب أن يكون كائن JSON."), status=422)
    full = _wants_full_archive(body)
    result = _svc().run_full_backup(tenant_id=_tid(), actor=_actor(), lean=not full)
    return ok({"ok": bool(result.get("ok")), "mode": "full" if full else "lean",
               "steps": list(result.get("steps") or [])})


def backups_google_drive_portal_link():
    """The web «ربط جوجل درايف» target: a short-lived SSO link into the
    customer portal, where Drive is linked and managed."""
    from ...radius.services.backup_drive_link import portal_sso_link
    res = portal_sso_link()
    if res.get("ok"):
        return ok({"url": res["url"]})
    return fail("portal_unavailable", res.get("message") or _tr("تعذّر فتح بوابة العميل."),
                status=502, details={"status": res.get("status") or ""})


def backups_run():
    result = _svc().run_local_backup(tenant_id=_tid(), actor=_actor())
    return ok(result, status=201 if result.get("verified") else 500)


def backups_google_drive_connect():
    """Start the real Google Drive OAuth device flow.

    The radius server has no public HTTPS domain, so a redirect OAuth flow is
    impossible — we use Google's Limited-Input Device flow. This endpoint kicks
    it off and returns the `user_code` + `verification_url` the operator opens
    on any phone/PC to authorise, then `/google-drive/poll` collects the token.

    When the OAuth client_id/secret haven't been saved yet we return a clear
    `needs_configuration` envelope (بانتظار تفعيلك) — never a silent 501.
    """
    from ...radius.services import google_drive as gd

    tid = _tid()
    if not gd.is_configured(tid):
        return fail(
            "needs_configuration",
            _tr("بانتظار تفعيلك: احفظ Google OAuth client_id و client_secret من "
            "صفحة النسخ الاحتياطي أولًا، ثم ابدأ الربط."),
            status=409,
            details={"domain": "backups", "operation": "google_drive_connect",
                     "required": ["google_drive.client_id", "google_drive.client_secret"]},
        )
    result = gd.start_device_flow(tid)
    if not result.get("ok"):
        return fail("device_start_failed",
                    result.get("detail") or _tr("تعذّر بدء ربط جوجل درايف."),
                    status=502, details={"error": result.get("error")})
    return ok({
        "user_code": result.get("user_code"),
        "verification_url": result.get("verification_url"),
        "expires_in": result.get("expires_in"),
        "interval": result.get("interval"),
    })


def backups_google_drive_poll():
    """Poll the pending device flow for the issued refresh token."""
    from ...radius.services import google_drive as gd

    result = gd.poll_device_flow(_tid())
    if result.get("ok"):
        return ok({"connected": True, "email": result.get("email")})
    # pending is a normal interim state while the operator authorises.
    return ok({"connected": False, "pending": bool(result.get("pending")),
               "error": result.get("error"), "detail": result.get("detail")})


def backups_google_drive_status():
    from ...radius.services import google_drive as gd

    return ok(gd.status(_tid()))
