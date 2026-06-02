"""Loop-probe endpoints — PUSH-mode ingest for DHCP-client loop detection.

A router-side scheduler (generated on the loop-setup page) reads the status of
each passive loop-probe DHCP client and posts it here every ~minute:

  POST /api/v1/routers/<router_id>/loop/ingest
  { "probes": [ {"interface": "ether2", "status": "bound",
                 "address": "10.0.0.7/24", "server": "10.0.0.1"}, ... ] }

A probe that is `bound` (got a lease on a port that should never see DHCP) is a
loop; the server upserts the reading + raises auto.router.loop with the IP.
Token-authed + tenant-scoped, exactly like the metrics ingest.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/routers/<int:router_id>/loop/ingest", "router_loop_ingest",
        require_api_token(router_loop_ingest), methods=["POST"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def router_loop_ingest(router_id: int):
    import json

    from ...radius.db.repos import nas_repo, router_loop_probes_repo
    from ...radius.services import smart_alerts

    tenant_id = _tid()
    if not nas_repo.get_nas(tenant_id, int(router_id)):
        return fail("not_found", "الراوتر غير موجود.", status=404)

    raw = (request.get_data(as_text=True) or "").strip()
    if not raw:
        return fail("empty_body", "بيانات المجسّات مطلوبة.", status=400)
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return fail("invalid_json", "بيانات الطلب ليست بصيغة صحيحة.", status=400)

    probes = body.get("probes") if isinstance(body, dict) else body
    if not isinstance(probes, list):
        return fail("invalid_shape", "أرسل قائمة المجسّات (probes).", status=400)

    recorded = 0
    for row in probes:
        if not isinstance(row, dict):
            continue
        iface = str(row.get("interface") or row.get("name") or "").strip()
        if not iface:
            continue
        router_loop_probes_repo.upsert_reading(
            tenant_id=tenant_id,
            router_id=int(router_id),
            interface=iface,
            status=str(row.get("status") or "").strip(),
            lease_ip=str(row.get("address") or row.get("lease_ip") or "").strip(),
            server_ip=str(row.get("server") or row.get("gateway")
                          or row.get("server_ip") or "").strip(),
        )
        recorded += 1

    # Evaluate this router's probes → open/resolve loop alerts immediately.
    try:
        smart_alerts.evaluate_loops(tenant_id, int(router_id))
    except Exception:  # noqa: BLE001 — never break ingest
        pass

    return ok({"router_id": int(router_id), "probes_recorded": recorded})
