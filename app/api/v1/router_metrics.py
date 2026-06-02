"""Router metrics endpoints — PUSH-mode ingest for the smart-alerts engine.

Routers across the public internet usually have their MT API firewalled even
when outbound HTTPS works, so — exactly like /devices/ingest («دفع DHCP») — the
router PUSHES its metrics here every ~2 minutes via a /system scheduler script.

  POST /api/v1/routers/<router_id>/metrics/ingest

Body (JSON, or text/plain JSON — MT's /tool fetch sends text/plain by default):
  {
    "reported_at": "2026-06-02T10:00:00Z",   # router clock (optional)
    "uptime_seconds": 604800,                 # optional
    "interfaces": [ {"name": "ether1", "rx_bytes": 123, "tx_bytes": 456}, ... ]
  }

Scoped to the caller's tenant via the API token; the router_id must belong to
that tenant. A fresh push also clears the router's offline alert immediately.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/routers/<int:router_id>/metrics/ingest", "router_metrics_ingest",
        require_api_token(router_metrics_ingest), methods=["POST"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def router_metrics_ingest(router_id: int):
    import json

    from ...radius.db.repos import nas_repo, router_metrics_repo
    from ...radius.services import smart_alerts

    tenant_id = _tid()
    router = nas_repo.get_nas(tenant_id, int(router_id))
    if not router:
        return fail("not_found", "الراوتر غير موجود.", status=404)

    raw = (request.get_data(as_text=True) or "").strip()
    if not raw:
        return fail("empty_body", "بيانات المقاييس مطلوبة.", status=400)
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return fail("invalid_json", "بيانات الطلب ليست بصيغة صحيحة.", status=400)
    if not isinstance(body, dict):
        return fail("invalid_shape", "أرسل كائنًا يحتوي قائمة الواجهات.", status=400)

    raw_ifaces = body.get("interfaces")
    if not isinstance(raw_ifaces, list):
        raw_ifaces = []

    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    interfaces = []
    for row in raw_ifaces:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        interfaces.append({
            "name": name[:64],
            "rx_bytes": _to_int(row.get("rx_bytes") if row.get("rx_bytes") is not None
                                else row.get("rx-byte")),
            "tx_bytes": _to_int(row.get("tx_bytes") if row.get("tx_bytes") is not None
                                else row.get("tx-byte")),
        })

    sample_id = router_metrics_repo.record_sample(
        tenant_id=tenant_id,
        router_id=int(router_id),
        reported_at=str(body.get("reported_at") or ""),
        uptime_seconds=_to_int(body.get("uptime_seconds")),
        interfaces=interfaces,
    )
    # A fresh push proves the router is alive — clear any offline alert now.
    smart_alerts.on_push(tenant_id, int(router_id))

    return ok({
        "router_id": int(router_id),
        "sample_id": sample_id,
        "interfaces_recorded": len(interfaces),
    })
