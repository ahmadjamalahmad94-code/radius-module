"""Admin Bridge activation service.

Provides two public functions:
- ``activate_bridge(base_url, activation_code, tenant_id, admin_id)``
  → calls the panel API, stores credentials in tenant_settings, returns result dict.
- ``test_bridge_connection(tenant_id)``
  → checks whether the bridge is configured and reachable, returns status dict.

Security rules:
- The raw activation_code is sent ONCE to the panel and then discarded.
- The shared_secret received in the response is written to tenant_settings but
  NEVER returned to callers, never logged, and never echoed back to the UI.
- Credentials are stored under the ``license_admin_bridge.*`` namespace so that
  ``AdminBridgeConfig.from_env()`` picks them up transparently.
"""
from __future__ import annotations

import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
import json
from typing import Any

LOG = logging.getLogger(__name__)

ACTIVATE_PATH = "/api/integration/hoberadius/instance/activate"
LICENSE_CHECK_PATH = "/api/license/check"

# tenant_settings keys — must match AdminBridgeConfig.from_env() lookups.
_KEY_BASE_URL = "license_admin_bridge.base_url"
_KEY_LICENSE_KEY = "license_admin_bridge.license_key"
_KEY_SHARED_SECRET = "license_admin_bridge.shared_secret"
_KEY_ENABLED = "license_admin_bridge.enabled"

_DEFAULT_TIMEOUT = 10  # seconds

# Module-level imports — kept at top level so unit tests can patch them cleanly.
try:
    from app.radius.db.repos import tenants_repo, audit_repo
    from app.radius.core.tenant import DEFAULT_TENANT_ID as _DEFAULT_TENANT_ID
    from app.radius.services.admin_panel_client import (
        AdminBridgeConfig,
        _server_fingerprint,
    )
except Exception:  # noqa: BLE001 — allow import in test environments without full app stack
    tenants_repo = None  # type: ignore[assignment]
    audit_repo = None  # type: ignore[assignment]
    _DEFAULT_TENANT_ID = 1
    AdminBridgeConfig = None  # type: ignore[assignment, misc]
    _server_fingerprint = lambda: ""  # type: ignore[misc]


class BridgeActivationError(Exception):
    """Raised when activation cannot be completed."""

    def __init__(self, message_ar: str, status: str = "error", *, http_status: int = 400):
        super().__init__(message_ar)
        self.message_ar = message_ar
        self.status = status
        self.http_status = http_status


def activate_bridge(
    base_url: str,
    activation_code: str,
    *,
    tenant_id: int,
    admin_id: int = 0,
) -> dict[str, Any]:
    """Call the panel activation endpoint and persist credentials.

    Returns ``{"ok": True, "customer_name": ..., "license_key": ..., "base_url": ...}``
    on success. The shared_secret is stored in tenant_settings but NOT returned.

    Raises :class:`BridgeActivationError` on any failure.
    """
    base_url = (base_url or "").strip().rstrip("/")
    activation_code = (activation_code or "").strip()

    if not base_url or not base_url.startswith(("https://", "http://")):
        raise BridgeActivationError("رابط لوحة التراخيص غير صحيح. يجب أن يبدأ بـ https://")

    if not activation_code:
        raise BridgeActivationError("كود التفعيل مطلوب.")

    # Build the activation payload.
    try:
        fingerprint = _server_fingerprint()
    except Exception:
        fingerprint = ""

    payload = json.dumps({
        "activation_code": activation_code,
        "server_fingerprint": fingerprint,
        "base_url": base_url,
    }).encode("utf-8")

    url = f"{base_url}{ACTIVATE_PATH}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
            msg = body.get("message") or body.get("status") or "خطأ من لوحة التراخيص"
        except Exception:
            msg = f"HTTP {exc.code}"
        LOG.warning("bridge_activation: panel returned HTTP %s — %s", exc.code, msg)
        if exc.code == 409:
            raise BridgeActivationError("كود التفعيل استُخدم مسبقاً. اطلب كوداً جديداً من الأدمن.", "already_used", http_status=409)
        if exc.code == 410:
            raise BridgeActivationError("انتهت صلاحية كود التفعيل. اطلب كوداً جديداً من الأدمن.", "expired", http_status=410)
        if exc.code == 401:
            raise BridgeActivationError("كود التفعيل غير صحيح.", "invalid_token", http_status=401)
        raise BridgeActivationError(f"فشل الاتصال بلوحة التراخيص: {msg}", http_status=exc.code)
    except urllib.error.URLError as exc:
        LOG.warning("bridge_activation: connection error to %s — %s", url, exc.reason)
        raise BridgeActivationError(
            f"تعذّر الوصول إلى لوحة التراخيص على {base_url}. تحقق من الرابط وإعدادات الشبكة.",
            "connection_error",
            http_status=503,
        )
    except Exception as exc:
        LOG.exception("bridge_activation: unexpected error")
        raise BridgeActivationError("خطأ غير متوقع أثناء الاتصال بلوحة التراخيص.", http_status=500)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise BridgeActivationError("ردّ غير صالح من لوحة التراخيص (ليس JSON).", http_status=502)

    if not data.get("ok"):
        msg = data.get("message") or data.get("status") or "خطأ غير معروف"
        raise BridgeActivationError(f"رفضت اللوحة التفعيل: {msg}", data.get("status", "error"))

    license_key = str(data.get("license_key") or "").strip().upper()
    shared_secret = str(data.get("shared_secret") or "").strip()
    panel_base_url = str(data.get("base_url") or base_url).strip().rstrip("/")
    customer_name = str(data.get("customer_name") or "").strip()

    if not license_key or not shared_secret:
        raise BridgeActivationError("ردّ التفعيل ناقص (license_key أو shared_secret مفقود).", http_status=502)

    # Persist credentials — shared_secret goes directly to DB, never logged.
    tid = tenant_id or _DEFAULT_TENANT_ID
    tenants_repo.set_setting(tid, _KEY_BASE_URL, panel_base_url, by=admin_id)
    tenants_repo.set_setting(tid, _KEY_LICENSE_KEY, license_key, by=admin_id)
    tenants_repo.set_setting(tid, _KEY_SHARED_SECRET, shared_secret, by=admin_id)
    tenants_repo.set_setting(tid, _KEY_ENABLED, "1", by=admin_id)

    try:
        audit_repo.record(
            tenant_id=tid,
            actor=f"admin:{admin_id}" if admin_id else "system",
            action="bridge_activated",
            target_type="tenant_settings",
            target_id=str(tid),
            payload={
                "license_key": license_key,
                "base_url": panel_base_url,
                "customer_name": customer_name,
            },
        )
    except Exception:  # noqa: BLE001 — audit must never block activation.
        LOG.exception("bridge_activation: audit_repo.record failed (non-fatal)")

    LOG.info(
        "bridge_activated: tenant_id=%s license_key=%s panel=%s",
        tid, license_key, panel_base_url,
    )

    return {
        "ok": True,
        "license_key": license_key,
        "base_url": panel_base_url,
        "customer_name": customer_name,
    }


def test_bridge_connection(*, tenant_id: int = 0) -> dict[str, Any]:
    """Check whether the bridge credentials are stored and the panel is reachable.

    Returns a dict with:
      ``configured`` — True if all three credentials are set in tenant_settings.
      ``reachable``  — True if the panel /api/health endpoint responded 200.
      ``status``     — human-readable status string.
      ``message_ar`` — Arabic status message for the UI.
    """
    cfg = AdminBridgeConfig.from_env()

    if not cfg.base_url or not cfg.license_key or not cfg.shared_secret:
        return {
            "ok": False,
            "configured": False,
            "reachable": False,
            "status": "not_configured",
            "message_ar": "الجسر غير مُفعَّل بعد. أدخل كود التفعيل أو اضبط الإعدادات يدوياً.",
        }

    # Ping /api/health — does NOT send credentials, just checks connectivity.
    health_url = f"{cfg.base_url}/api/health"
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(health_url, timeout=5, context=ctx) as resp:
            reachable = resp.status == 200
    except Exception:
        reachable = False

    if reachable:
        return {
            "ok": True,
            "configured": True,
            "reachable": True,
            "status": "connected",
            "message_ar": f"الجسر مُفعَّل والاتصال بلوحة التراخيص يعمل. ({cfg.base_url})",
        }
    else:
        return {
            "ok": False,
            "configured": True,
            "reachable": False,
            "status": "unreachable",
            "message_ar": f"الإعدادات موجودة لكن لوحة التراخيص غير قابلة للوصول حالياً ({cfg.base_url}).",
        }
