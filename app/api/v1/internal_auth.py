"""
/api/v1/internal/auth — endpoint يستدعيه FreeRADIUS عبر rlm_rest module.

الـ rlm_rest يرسل POST/JSON بحقول من packet الـ Access-Request.
نحن نقرّر ونرجع JSON بصيغة rlm_rest المتوقَّعة:
    {
      "control:Auth-Type": "Accept" | "Reject",
      "reply:<Attribute>": "<value>",
      ...
    }

ملاحظات:
- لا يستخدم Bearer token عام (هو daخلي بين FR و HR على localhost).
- بدل ذلك: تحقّق من X-Internal-Secret ضد env HOBERADIUS_INTERNAL_SECRET.
- لا يرفع أبدًا 500 — يرجع Reject عند أي خطأ كي لا يعطّل FreeRADIUS.
"""
from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

_LOG = logging.getLogger(__name__)


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/internal/auth", "internal_auth", internal_auth, methods=["POST"])
    bp.add_url_rule("/internal/postauth", "internal_postauth", internal_postauth, methods=["POST"])


def _check_internal_secret() -> bool:
    expected = (os.environ.get("HOBERADIUS_INTERNAL_SECRET") or "").strip()
    if not expected:
        return True   # dev mode: لا secret مضبوط
    return request.headers.get("X-Internal-Secret", "") == expected


def _resolve_tenant_id(body: dict) -> int:
    """يحدّد tenant_id من NAS-IP-Address (الـ NAS مرتبط بـ tenant)."""
    nas_ip = body.get("NAS-IP-Address") or body.get("nas_ip_address") or ""
    if not nas_ip:
        return 1
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT tenant_id FROM nas_devices WHERE address = ? AND enabled = 1 LIMIT 1",
        (nas_ip,)).fetchone()
    return int(row["tenant_id"]) if row else 1


def internal_auth():
    if not _check_internal_secret():
        return jsonify({"control:Auth-Type": "Reject",
                         "reply:Reply-Message": "Internal auth secret mismatch"}), 401

    body = request.get_json(silent=True) or {}
    # FreeRADIUS rlm_rest يستعمل أسماء الـ attributes الكاملة (case-sensitive).
    # نقبل أيضًا lowercased للتوافق.
    def g(k: str, default: str = "") -> str:
        return str(body.get(k) or body.get(k.lower()) or
                    body.get(k.replace("-", "_")) or default).strip()

    from app.radius.services.policy_engine import AuthRequest, authorize
    try:
        req = AuthRequest(
            username=g("User-Name"),
            password=g("User-Password"),
            tenant_id=_resolve_tenant_id(body),
            calling_station_id=g("Calling-Station-Id"),
            called_station_id=g("Called-Station-Id"),
            nas_ip=g("NAS-IP-Address"),
            nas_port_type=g("NAS-Port-Type"),
        )
        decision = authorize(req)
    except Exception:  # noqa: BLE001
        _LOG.exception("policy engine error — defaulting to Reject")
        return jsonify({
            "control:Auth-Type": "Reject",
            "reply:Reply-Message": "Internal error — try again",
        }), 200

    out: dict = {}
    if decision.ok:
        out["control:Auth-Type"] = "Accept"
    else:
        out["control:Auth-Type"] = "Reject"
    for k, v in decision.reply_attrs.items():
        out[f"reply:{k}"] = v
    return jsonify(out), 200


def internal_postauth():
    """FreeRADIUS يستدعي هذا بعد قراره النهائي — للـ logging والـ webhooks."""
    if not _check_internal_secret():
        return jsonify({"ok": False, "reason": "secret_mismatch"}), 401
    body = request.get_json(silent=True) or {}
    username = (body.get("User-Name") or body.get("user_name") or "").strip()
    reply_code = (body.get("reply_code") or "").strip()
    nas_ip = (body.get("NAS-IP-Address") or body.get("nas_ip_address") or "").strip()
    if not username:
        return jsonify({"ok": True, "noop": True}), 200
    try:
        tenant_id = _resolve_tenant_id(body)
        from app.webhooks.dispatcher import dispatch_event
        event = "session.authorized" if "Accept" in reply_code else "session.rejected"
        dispatch_event(event, {
            "username": username, "nas_ip": nas_ip, "reply_code": reply_code,
        }, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        _LOG.exception("post-auth dispatch failed")
    return jsonify({"ok": True}), 200
