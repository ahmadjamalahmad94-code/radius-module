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


def _is_production() -> bool:
    """Production when HOBERADIUS_ENV or FLASK_ENV resolves to prod/production.
    Mirrors app.api.auth._is_production — kept local to avoid importing a
    private symbol across modules."""
    env = (os.environ.get("HOBERADIUS_ENV")
           or os.environ.get("FLASK_ENV")
           or "").strip().lower()
    return env in {"prod", "production"}


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/internal/auth", "internal_auth", internal_auth, methods=["POST"])
    bp.add_url_rule("/internal/postauth", "internal_postauth", internal_postauth, methods=["POST"])
    bp.add_url_rule("/internal/_diag", "internal_diag", internal_diag, methods=["POST"])


def _redact_prefix(s: str, n: int = 4) -> str:
    """Return a short prefix for logging — never the full secret."""
    if not s:
        return "<empty>"
    if len(s) <= n:
        return "<short>"
    return s[:n] + "…"


def _check_internal_secret(body: dict | None = None) -> bool:
    """تحقّق من السرّ الداخلي. يقبل من مكانَين:

      1. HTTP header `X-Internal-Secret` — الطريقة المثالية، لكن FR 3.2.x
         لا يدعم custom headers في rlm_rest.
      2. JSON body field `_internal_secret` — fallback تستعمله FR 3.2.x
         (انظر mods-enabled/rest).

    مهم (SEC M5): لو HOBERADIUS_INTERNAL_SECRET غير مضبوط:
      • في الإنتاج → **نرفض** (fail closed): بلا سرّ يصير مسار المصادقة
        الداخليّ (قرار Accept/Reject لـRADIUS) مفتوحًا لأيّ من يبلغ المنفذ.
      • خارج الإنتاج → نقبل dev mode كي لا يكسر اختبارًا محلّيًا.

    عند الفشل نُسجّل WARNING يحوي: أطوال expected vs incoming + presence،
    + prefix قصير للتمييز (4 أحرف + …، لا نُسرّب السرّ).
    """
    expected = (os.environ.get("HOBERADIUS_INTERNAL_SECRET") or "").strip()
    if not expected:
        if _is_production():
            _LOG.error(
                "internal_auth: HOBERADIUS_INTERNAL_SECRET غير مضبوط في الإنتاج — "
                "رفض الطلب الداخليّ (fail closed). اضبطها في .env.")
            return False
        _LOG.warning("internal_auth: HOBERADIUS_INTERNAL_SECRET غير مضبوط — "
                      "تشغيل dev mode (يقبل بدون secret). اضبطها في .env للإنتاج.")
        return True

    header_secret = request.headers.get("X-Internal-Secret", "")
    if header_secret and header_secret == expected:
        # DEBUG, not WARNING: logged on EVERY login (twice with post-auth) —
        # thousands of lines/s during a login burst (stress L01).
        _LOG.debug("internal_auth: secret OK via header")
        return True

    if body is None:
        body = request.get_json(silent=True) or {}
    body_secret = str(body.get("_internal_secret") or "").strip()
    if body_secret and body_secret == expected:
        _LOG.debug("internal_auth: secret OK via body fallback "
                   "(FR 3.2.x rlm_rest cannot send custom headers)")
        return True

    _LOG.warning(
        "internal_auth: SECRET MISMATCH — expected_len=%d "
        "header_present=%s header_len=%d header_prefix=%s "
        "body_secret_present=%s body_secret_len=%d body_secret_prefix=%s "
        "expected_prefix=%s remote=%s",
        len(expected),
        "yes" if "X-Internal-Secret" in request.headers else "no",
        len(header_secret), _redact_prefix(header_secret),
        "yes" if body_secret else "no",
        len(body_secret), _redact_prefix(body_secret),
        _redact_prefix(expected), request.remote_addr or "?",
    )
    return False


def _packet_source(body: dict) -> str:
    """The address the packet came FROM (FreeRADIUS Packet-Src-IP-Address).

    B-14: this — not the in-packet NAS-IP-Address — decides the tenant.
    FreeRADIUS answers only registered clients whose shared secret matched,
    so the source address identifies the router; NAS-IP-Address is written by
    the router itself. NAS-IP-Address is used only when the source is absent
    (an rlm_rest config older than this fix)."""
    src = str(body.get("Packet-Src-IP-Address")
              or body.get("packet_src_ip_address") or "").strip()
    if src:
        return src
    return str(body.get("NAS-IP-Address") or body.get("nas_ip_address") or "").strip()


def _resolve_source_tenant(body: dict):
    """B-14: the tenant that owns the router that sent this packet, or an
    unattributed result (``tenant_id is None``) — never a tenant-1 default.
    See app/radius/services/nas_tenant.py for the rule."""
    from app.radius.services.nas_tenant import resolve_source_tenant
    return resolve_source_tenant(_packet_source(body))


def _resolve_tenant_id(body: dict) -> int | None:
    """Back-compat wrapper: the owning tenant id, or None (do not attribute)."""
    return _resolve_source_tenant(body).tenant_id


def internal_auth():
    body = request.get_json(silent=True) or {}
    if not _check_internal_secret(body):
        return jsonify({"control:Auth-Type": "Reject",
                         "reply:Reply-Message": "Internal auth secret mismatch"}), 401

    # ـ نُسقط حقل السرّ من الـ body قبل التحليل كي لا يُسجَّل أو يُسرَّب في
    # error traces لاحقاً ـ
    body.pop("_internal_secret", None)

    # FreeRADIUS rlm_rest يستعمل أسماء الـ attributes الكاملة (case-sensitive).
    # نقبل أيضًا lowercased للتوافق.
    def g(k: str, default: str = "") -> str:
        return str(body.get(k) or body.get(k.lower()) or
                    body.get(k.replace("-", "_")) or default).strip()

    # B-14: a router no tenant can be given (multi-network server, source
    # address unknown or claimed by two tenants) is REJECTED and recorded in
    # the operator-only quarantine — it is never evaluated against, nor logged
    # into, tenant 1. (Single-network servers always resolve to their only
    # tenant, exactly as before.)
    try:
        owner = _resolve_source_tenant(body)
    except Exception as exc:  # noqa: BLE001
        from app.radius.db.connection import is_lock_error
        if is_lock_error(exc):
            _LOG.warning("internal_auth: database busy resolving the NAS "
                         "tenant — no decision (503): %s", exc)
            return jsonify({"error": "busy"}), 503
        _LOG.exception("internal_auth: NAS tenant resolution failed — Reject")
        return jsonify({"control:Auth-Type": "Reject",
                        "reply:Reply-Message": "Internal error — try again"}), 200
    if not owner.attributed and owner.reason == "probe":
        # B-14 local registry: a registered health probe gets an answer
        # (proof of life) but leaves no trace — no login log, no quarantine,
        # no fail2ban count, no webhook.
        return jsonify({"control:Auth-Type": "Reject",
                        "reply:Reply-Message": "Health probe"}), 200
    if not owner.attributed:
        from app.radius.services.nas_tenant import quarantine_auth
        quarantine_auth(source_ip=owner.source_ip,
                        nas_ip_attr=g("NAS-IP-Address"),
                        username=g("User-Name"),
                        calling_station_id=g("Calling-Station-Id"),
                        reason=owner.reason)
        _LOG.warning("internal_auth: REJECT user=%r — router %s belongs to no "
                     "single tenant (%s); recorded in radius_unattributed",
                     g("User-Name"), owner.source_ip or "?", owner.reason)
        return jsonify({"control:Auth-Type": "Reject",
                        "reply:Reply-Message": "Unknown NAS"}), 200

    from app.radius.services.policy_engine import AuthRequest, authorize
    try:
        req = AuthRequest(
            username=g("User-Name"),
            password=g("User-Password"),
            chap_password=g("CHAP-Password"),
            chap_challenge=g("CHAP-Challenge"),
            tenant_id=int(owner.tenant_id),
            calling_station_id=g("Calling-Station-Id"),
            called_station_id=g("Called-Station-Id"),
            nas_ip=g("NAS-IP-Address"),
            nas_port=g("NAS-Port"),
            nas_port_type=g("NAS-Port-Type"),
            # بوابة الكابتيف الخارجية قد تُمرّر User-Agent عبر هذا الحقل المخصّص
            # (anti-mac-clone). FreeRADIUS لا يحمل UA عادةً.
            user_agent=g("X-User-Agent") or g("User-Agent") or "",
        )
        # DEBUG: policy_engine.authorize logs the same fields («auth_attempt»)
        # and the decision; one line per login is enough under load.
        _LOG.debug(
            "internal_auth: REQ tenant=%d user=%r nas=%s mac=%s pap=%s chap=%s",
            req.tenant_id, req.username, req.nas_ip, req.calling_station_id,
            "yes" if req.password else "no",
            "yes" if req.chap_password else "no",
        )
        decision = authorize(req)
    except Exception as exc:  # noqa: BLE001
        # Stress L01: a TRANSIENT database lock is "no decision", not "wrong
        # password". HTTP 503 → rlm_rest `fail` → sites-enabled/default stays
        # silent → the NAS retransmits, instead of rejecting a valid login.
        from app.radius.db.connection import is_lock_error
        if is_lock_error(exc):
            _LOG.warning("internal_auth: database busy — no decision (503), "
                         "the NAS will retransmit: %s", exc)
            return jsonify({"error": "busy"}), 503
        _LOG.exception("internal_auth: policy engine error — defaulting to Reject")
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
    # ـ WARNING مؤقت: يطبع كل أركان القرار للوحدة الواحدة كي ينظر العامل
    # ـ في log واحد ويعرف بالضبط لماذا تمّ القبول/الرفض ـ
    _LOG.warning("internal_auth: RESP user=%r http=200 auth=%s reason=%s "
                  "reply_keys=%s",
                  req.username,
                  "Accept" if decision.ok else "Reject",
                  decision.reason or "-",
                  sorted(decision.reply_attrs.keys()))
    return jsonify(out), 200


def internal_diag():
    """Endpoint تشخيصي يكشف عن مسار البحث + قرار policy_engine بلا
    اعتماد على X-Internal-Secret. يُمكَّن فقط لو HOBERADIUS_DIAG_ENABLED=1.
    (B-08: صار يتطلّب X-Internal-Secret أيضًا — انظر الفحص أدناه.)

    الاستخدام من VPS:
      docker exec hoberadius curl -s -X POST \\
        http://localhost:8000/api/v1/internal/_diag \\
        -H 'Content-Type: application/json' \\
        -H "X-Internal-Secret: $HOBERADIUS_INTERNAL_SECRET" \\
        -d '{"username":"user1000","password":"123456","tenant_id":1}'

    يعيد JSON يحوي:
      db_path, tenant_id, found_in (subscribers/cards/none),
      decision (ok, reason, reply_keys).
    """
    if os.environ.get("HOBERADIUS_DIAG_ENABLED", "").strip() != "1":
        return jsonify({"error": "diag disabled",
                         "hint": "set HOBERADIUS_DIAG_ENABLED=1 in .env and "
                                  "restart hoberadius"}), 403

    body = request.get_json(silent=True) or {}
    # Security B-08: this endpoint answers "does user X exist in tenant T and
    # is password P accepted" — a password/existence oracle across tenants.
    # It now needs the internal secret (header ``X-Internal-Secret`` or body
    # ``_internal_secret``, same as FreeRADIUS), and a secret MUST be
    # configured: no dev "accept without secret" fallback here.
    if not (os.environ.get("HOBERADIUS_INTERNAL_SECRET") or "").strip():
        return jsonify({"error": "diag requires HOBERADIUS_INTERNAL_SECRET"}), 403
    if not isinstance(body, dict) or not _check_internal_secret(body):
        return jsonify({"error": "secret_mismatch"}), 401
    body.pop("_internal_secret", None)
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "")
    tenant_id = int(body.get("tenant_id") or 1)

    # ـ نلتقط مسار الـ DB الذي تستخدمه Flask فعليًا ـ
    from app.radius.db.connection import _resolve_db_path  # type: ignore
    from app.radius.db.repos import cards_repo, subscribers_repo

    db_path = _resolve_db_path()
    sub = subscribers_repo.get_subscriber(tenant_id, username)
    card = cards_repo.get_card_by_username(tenant_id, username) if not sub else None
    found_in = "subscribers" if sub else ("cards" if card else "none")

    from app.radius.services.policy_engine import AuthRequest, authorize
    req = AuthRequest(username=username, password=password, tenant_id=tenant_id)
    _LOG.warning("internal_diag: REQ user=%r tenant=%d db_path=%s found_in=%s",
                  username, tenant_id, db_path, found_in)
    decision = authorize(req)
    _LOG.warning("internal_diag: RESP user=%r auth=%s reason=%s",
                  username, "Accept" if decision.ok else "Reject",
                  decision.reason or "-")
    return jsonify({
        "db_path": db_path,
        "tenant_id": tenant_id,
        "username": username,
        "found_in": found_in,
        "subscriber_status": sub.status if sub else None,
        "card_revoked": bool(card.revoked) if card else None,
        "decision": {
            "ok": decision.ok,
            "reason": decision.reason,
            "message": decision.message,
            "reply_keys": sorted(decision.reply_attrs.keys()),
        },
    }), 200


def internal_postauth():
    """FreeRADIUS يستدعي هذا بعد قراره النهائي — للـ logging والـ webhooks."""
    body = request.get_json(silent=True) or {}
    if not _check_internal_secret(body):
        return jsonify({"ok": False, "reason": "secret_mismatch"}), 401
    body.pop("_internal_secret", None)
    username = (body.get("User-Name") or body.get("user_name") or "").strip()
    reply_code = (body.get("reply_code") or "").strip()
    nas_ip = (body.get("NAS-IP-Address") or body.get("nas_ip_address") or "").strip()
    if not username:
        return jsonify({"ok": True, "noop": True}), 200
    try:
        tenant_id = _resolve_tenant_id(body)
        if tenant_id is None:
            # B-14: no single owning tenant → no tenant's webhooks receive it.
            return jsonify({"ok": True, "noop": True,
                            "reason": "unattributed_nas"}), 200
        from app.webhooks.dispatcher import dispatch_event
        event = "session.authorized" if "Accept" in reply_code else "session.rejected"
        dispatch_event(event, {
            "username": username, "nas_ip": nas_ip, "reply_code": reply_code,
        }, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        _LOG.exception("post-auth dispatch failed")
    return jsonify({"ok": True}), 200
