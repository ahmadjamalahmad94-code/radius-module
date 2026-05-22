"""snapshot_refresh — S7.2 fleet-safe router snapshot refresh.

Reads each enabled router, asks the existing K4/K5 admin-client
helpers for counters + resource info, and writes the result
through `router_snapshots_repo`. Per-router timeouts; failures
on one router don't poison the loop.

This module is wire-aware (calls mac.*); the repo + UI layers
stay pure.
"""
from __future__ import annotations

from typing import Any

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import router_snapshots_repo
from . import mikrotik_admin_client as mac
from . import mt_counters


def _load_routers(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT id, name, address, api_port, api_user, "
        "       api_password, api_use_tls, enabled, "
        "       connection_mode, vpn_peer_address "
        "FROM nas_devices "
        "WHERE tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(tenant_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def _nas_dict(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"],
        "host": row["address"],
        "port": int(row.get("api_port") or 8728),
        "username": row.get("api_user") or "admin",
        "password": row.get("api_password") or "",
        "use_tls": bool(row.get("api_use_tls")),
        "verify_tls": True, "timeout_sec": 8,
    }


def refresh_one(
    tenant_id: int, router: dict,
) -> dict[str, Any]:
    """Refresh one router. Always returns a result dict so the
    caller can aggregate; never raises."""
    rid = int(router["id"])
    if not router.get("enabled"):
        # Disabled routers don't get touched, but we still ensure
        # the snapshot row exists so the UI can render it as
        # 'disabled' rather than 'never seen'.
        router_snapshots_repo.save_failure(
            tenant_id=tenant_id, router_id=rid,
            error="الراوتر معطّل — لم نحاول الاتصال.",
            source="cached",
        )
        return {"router_id": rid, "ok": False,
                "reason": "disabled"}

    nas = _nas_dict(router)
    try:
        counters_res = mt_counters.counters_for_nas(nas)
        # counters_for_nas returns an MtResult-shape; either it
        # carries a dataclass with to_dict() or a plain dict.
        raw = (counters_res.data
               if hasattr(counters_res, "data") else counters_res)
        if hasattr(raw, "to_dict"):
            counters = raw.to_dict()
        else:
            counters = dict(raw or {})
        res = mac.system_resource(nas)
        resource = (res.data[0]
                    if getattr(res, "ok", False) and res.data
                    else {})
        router_snapshots_repo.save_success(
            tenant_id=tenant_id, router_id=rid,
            counters=counters, resource=resource,
        )
        return {"router_id": rid, "ok": True}
    except Exception as e:  # noqa: BLE001
        router_snapshots_repo.save_failure(
            tenant_id=tenant_id, router_id=rid,
            error=str(e),
        )
        return {"router_id": rid, "ok": False,
                "reason": str(e)[:200]}


def refresh_fleet(
    tenant_id: int = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Refresh every router in the tenant. Sequential — keeps
    blast radius small on a flaky link. A future commit can add
    a small ThreadPool when fleet sizes grow past ~20."""
    routers = _load_routers(int(tenant_id))
    results = [refresh_one(int(tenant_id), r) for r in routers]
    return {
        "tenant_id": int(tenant_id),
        "total": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


__all__ = ["refresh_one", "refresh_fleet"]
