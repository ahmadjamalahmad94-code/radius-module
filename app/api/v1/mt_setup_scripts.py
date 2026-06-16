"""mikrotik setup-scripts — v1 JSON API (feat/api-first-parity, group 7g).

Mirrors the two MikroTik "paste this scheduler script" pages:
  * push-setup   (`routes/status.py:mt_push_setup`, `/admin/radius/mt-push-setup`)
    — router pushes DHCP leases to `/api/v1/devices/ingest`.
  * metrics-setup(`routes/mt_alerts.py:mt_metrics_setup`,
    `/admin/radius/alerts/agent-setup`) — router pushes interface metrics to
    `/api/v1/routers/<id>/metrics/ingest`.

Both pages build the final RouterOS scheduler script **client-side** (JS, from
the base URL + the operator-supplied token + router id). The server's own
contribution is the *inputs*: base URL, the ingest endpoint, the scheduler
name/interval, the available API-token names, and (for metrics) the routers.
This endpoint exposes exactly those inputs so the Flutter client assembles the
identical script. (The token VALUE is never returned — `api_tokens` stores only
hashes; the operator pastes the real token, same as the web page.)
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import api_tokens_repo
from ..auth import require_api_token
from ..responses import ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/mikrotik/push-setup", "mt_push_setup",
                    require_api_token(push_setup), methods=["GET"])
    bp.add_url_rule("/mikrotik/metrics-setup", "mt_metrics_setup",
                    require_api_token(metrics_setup), methods=["GET"])


def _base_url() -> str:
    proto = request.headers.get("X-Forwarded-Proto") or (
        "https" if request.is_secure else "http")
    host = request.headers.get("X-Forwarded-Host") or request.host
    # السماح بتجاوز صريح (مفيد للعميل خلف بروكسي مختلف).
    override = (request.args.get("base_url") or "").strip().rstrip("/")
    return override or f"{proto}://{host}"


def _token_names() -> tuple[list[str], str]:
    tokens = [t for t in api_tokens_repo.list_tokens(_tid()) if not t.get("revoked")]
    names = [str(t.get("name") or "") for t in tokens]
    return names, (names[0] if names else "")


def push_setup():
    """GET /mikrotik/push-setup — مدخلات سكربت دفع DHCP (يطابق mt_push_setup)."""
    names, suggested = _token_names()
    base = _base_url()
    return ok({
        "base_url": base,
        "ingest_endpoint": "/api/v1/devices/ingest",
        "ingest_url": base + "/api/v1/devices/ingest",
        "scheduler": {"name": "hoberadius-push-dhcp", "interval": "2m"},
        "method": "POST",
        "auth_header": "Authorization: Bearer <API_TOKEN>",
        "tokens": names,
        "suggested_token_name": suggested,
        "note": "السكربت النهائي يُجمَّع من هذه المدخلات (نفس ما تفعله الصفحة): "
                "/system scheduler add … on-event=/tool fetch إلى ingest_url "
                "بترويسة التوكن. التوكن يُدخله المشغّل (لا يُعاد من الخادم).",
    })


def metrics_setup():
    """GET /mikrotik/metrics-setup — مدخلات سكربت دفع القياسات (يطابق
    mt_metrics_setup). ?router_id لبناء رابط الـingest الكامل."""
    names, suggested = _token_names()
    base = _base_url()
    rid = (request.args.get("router_id") or "").strip()
    endpoint_tmpl = "/api/v1/routers/{router_id}/metrics/ingest"
    routers = []
    try:
        from ...radius.routes.mt_alerts import _routers_with_thresholds
        routers = _routers_with_thresholds(_tid())
    except Exception:  # noqa: BLE001
        routers = []
    return ok({
        "base_url": base,
        "ingest_endpoint_template": endpoint_tmpl,
        "ingest_url": (base + endpoint_tmpl.format(router_id=rid)) if rid.isdigit() else "",
        "scheduler": {"name": "hoberadius-push-metrics", "interval": "2m"},
        "method": "POST",
        "auth_header": "Authorization: Bearer <API_TOKEN>",
        "tokens": names,
        "suggested_token_name": suggested,
        "routers": routers,
        "note": "السكربت النهائي يُجمَّع من هذه المدخلات (نفس ما تفعله الصفحة): "
                "/system scheduler add … on-event=/tool fetch إلى ingest_url "
                "(جسم interfaces rx/tx) بترويسة التوكن.",
    })
