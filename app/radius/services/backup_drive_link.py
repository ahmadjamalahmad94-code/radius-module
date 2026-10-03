"""Google Drive backups — the ONE status source and link for web and API.

Drive linking moved to the customer portal on the license panel: the web
backups page reads the connection from the panel (bridge
``fetch_google_drive_status``) and its «ربط جوجل درايف» button opens the
customer portal through a one-click SSO link (``request_portal_sso``).

The app used to read a *local* device-flow status and start a device flow that
needs a Google OAuth client id/secret no screen can set (always 409). Both the
web page and ``/api/v1/backups/*`` now read through this module, so the two
surfaces cannot disagree again.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

from typing import Any

FOLDER_DEFAULT = "HobeRadius Backups"


def panel_google_drive_status() -> dict[str, Any]:
    """Drive connection as the license panel reports it (what the web shows).

    ``status``: ``connected`` / ``not_connected`` (the panel answered) /
    ``not_configured`` (the panel could not be reached or is not set up).
    Never raises.
    """
    answered = False
    resp: dict = {}
    try:
        from .admin_panel_client import AdminPanelClient
        r = AdminPanelClient().fetch_google_drive_status()
        if r.get("ok"):
            answered = True
            resp = r.get("response") or {}
    except Exception:  # noqa: BLE001 — a status read never breaks a page
        answered = False
    connected = bool(resp.get("connected"))
    if connected:
        status = "connected"
        message = (_tr("جوجل درايف مربوط من بوابة العميل — تُحوَّل النسخ المرفوعة "
                   "إلى لوحة التراخيص إلى درايفك تلقائيًّا."))
    elif answered:
        status = "not_connected"
        message = _tr("جوجل درايف غير مربوط — اربطه من بوابة العميل.")
    else:
        status = "not_configured"
        message = (_tr("جوجل درايف غير مفعل حاليًا — تعذّرت قراءة حالته من لوحة "
                   "التراخيص. يُربط من بوابة العميل."))
    return {
        "configured": answered,
        "connected": connected,
        "pending": False,
        "status": status,
        "email": str(resp.get("email") or ""),
        "folder_name": str(resp.get("folder_name") or FOLDER_DEFAULT),
        "last_upload_at": str(resp.get("last_upload_at") or ""),
        "last_error": "",
        "message_ar": message,
        # how the operator links/manages Drive (the web button target).
        "link_via": "customer_portal",
    }


def portal_sso_link() -> dict[str, Any]:
    """A short-lived SSO link into the customer portal (where Drive is linked).

    Same reading as the web ``license_file_portal_sso``: the transport's
    ``ok`` is not the panel's answer — the link and the panel's own Arabic
    reason live under ``response``. Returns ``{ok, url}`` or ``{ok: False,
    message}``. Never raises.
    """
    try:
        from .admin_panel_client import AdminPanelClient
        result = AdminPanelClient().request_portal_sso()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "error",
                "message": _tr('تعذّر فتح بوابة العميل: %(exc)s', exc=exc)}
    if not result.get("ok"):
        err = result.get("error") or {}
        msg = str(err.get("message") or "").strip() if isinstance(err, dict) else ""
        return {"ok": False, "status": str(result.get("status") or "unavailable"),
                "message": msg or N_("تعذّر فتح بوابة العميل: لوحة التراخيص غير متاحة.")}
    inner = result.get("response") or {}
    url = str(inner.get("sso_url") or "")
    if url:
        return {"ok": True, "url": url}
    reason = str(inner.get("message") or "").strip()
    return {"ok": False, "status": str(inner.get("status") or result.get("status") or ""),
            "message": reason or _tr("تعذّر فتح بوابة العميل: لم يصل رابط الدخول.")}
