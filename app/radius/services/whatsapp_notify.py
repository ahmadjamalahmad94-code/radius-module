"""Fail-safe WhatsApp event-notification helper (thin client).

A single tiny function — :func:`notify_whatsapp` — that the RADIUS auth /
accounting / dunning / maintenance flows call when a notable business event
happens (account created, password changed, subscription about to expire, a
maintenance window…). It is deliberately the *thinnest possible* wrapper around
the signed admin-panel bridge:

  * It NEVER calls the upstream messaging provider's API and stores no provider
    credential. It only ASKS the license panel to enqueue a templated message
    via :meth:`AdminPanelClient.enqueue_whatsapp_message` — exactly like the
    rest of the WhatsApp thin client.
  * It is GATED twice: first the operator must have flipped on the per-event
    local toggle ``whatsapp.send.<gate>`` in ``tenant_settings`` (the same keys
    the ``/admin/radius/whatsapp`` page writes); only then does it touch the
    bridge at all.
  * It is FAIL-SAFE: any error — bridge disabled, HTTPS missing, timeout, a
    missing phone, a bad payload, anything — is swallowed and logged at
    ``exception`` level, and the function returns ``False``. It can NEVER raise
    into the calling business flow. The underlying operation (account create,
    password reset, dunning tick, maintenance run) always fully succeeds even
    when the enqueue throws. This mirrors the workers' ``noqa: BLE001``
    discipline.

Privacy: this module logs nothing about the recipient phone number or the
message body at INFO. Only the (non-sensitive) event type is logged, and only
on failure.
"""
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)


def notify_whatsapp(
    tenant_id: int,
    event_type: str,
    *,
    gate: str,
    recipient_phone: str,
    template_key: str,
    idempotency_key: str,
    subscriber_id: int | None = None,
    variables: Any = None,
    language: str = "ar",
) -> bool:
    """Enqueue one templated WhatsApp message through the signed bridge.

    Returns ``True`` only when the enqueue was actually handed to the bridge
    (the local gate was ON and the bridge call did not raise). Returns
    ``False`` — never raises — in every other case: no phone, no idempotency
    key, the operator's local gate is OFF, or the enqueue threw.

    Args:
        tenant_id: tenant the subscriber belongs to.
        event_type: source business event (e.g. ``"otp"``, ``"expiry_notice"``)
            — recorded on the bridge payload as ``source_event_type`` and used
            in the (failure-only) log line. NOT a Meta secret.
        gate: the per-event local toggle suffix; the setting checked is
            ``whatsapp.send.<gate>`` (default ``"0"`` → OFF).
        recipient_phone: subscriber phone. Empty → no-op ``False`` (and never
            logged).
        template_key: panel-side template id to render.
        idempotency_key: stable key so a retry / double-fire does not double
            send. Empty → no-op ``False``.
        subscriber_id: optional subscriber id (for the panel's audit trail).
        variables: optional template variables (list/dict) — passed verbatim.
        language: template language (default Arabic).
    """
    # Cheap guards first — never touch the DB or the bridge without the basics.
    if not recipient_phone or not idempotency_key:
        return False

    # LOCAL GATE — the operator must have enabled WhatsApp for this event on the
    # client. The gate read itself must never raise into the business flow.
    try:
        from ..db.repos import tenants_repo

        gate_value = tenants_repo.get_setting(
            int(tenant_id or 1), f"whatsapp.send.{gate}", "0"
        )
    except Exception:  # noqa: BLE001 — a settings read failure must not break the flow
        _LOG.exception("whatsapp notify gate read failed (non-fatal) event=%s", event_type)
        return False

    if str(gate_value or "0").strip() != "1":
        return False

    # The bridge enqueue is the only step that may reach the network. Wrap it so
    # NOTHING it does can propagate into the caller's RADIUS / dunning / etc flow.
    # ── تطبيع الرقم بمفتاح الدولة (إعداد comms.country_dial_code) ──
    # المحلي 0599... يصبح +970599... قبل تسليمه لجسر واتساب. أي فشل في
    # التطبيع غير قاتل: نُبقي الرقم كما هو ونكمل الإرسال.
    try:
        from .comms_providers import normalize_msisdn, tenant_dial_code

        recipient_phone = normalize_msisdn(recipient_phone, tenant_dial_code(tenant_id)) or recipient_phone
    except Exception:  # noqa: BLE001 — التطبيع يجب ألا يكسر خط الإرسال
        pass

    try:
        from .admin_panel_client import AdminPanelClient

        AdminPanelClient().enqueue_whatsapp_message({
            "source_event_type": event_type,
            "subscriber_id": subscriber_id,
            "recipient_phone": recipient_phone,
            "template_key": template_key,
            "language": language,
            "variables": variables,
            "idempotency_key": idempotency_key,
        })
        return True
    except Exception:  # noqa: BLE001 — enqueue failure is ALWAYS non-fatal
        _LOG.exception("whatsapp notify failed (non-fatal) event=%s", event_type)
        return False
