"""
RouterSync — يحوّل أحداث DB إلى عمليات MT API فعلية.

يُستخدم من:
1. SqliteAdapter (عند upsert/delete) → enqueue jobs.
2. sync_worker (خيط خلفي) → ينفّذ jobs.

تصميم:
- لا يفتح اتصالات في الـ enqueue (سريع، لا blocking).
- الـ worker هو الذي ينفذ فعليًا.
- إن لم يكن للـ tenant routers مفعّلة → الـ job يُسجَّل ويُغلق "done" فورًا
  (لا داعي لإبقائه retrying).
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from ..core.types import AccessPlan, Subscriber
from ..core.types_saas import IpPool
from ..db.repos import mikrotik_repo, sync_queue_repo
from .mikrotik import MikrotikClient
from .mikrotik.errors import AuthError, ConnectError, MikrotikError, MikrotikTrap
from .mikrotik.pool import acquire as acquire_mt

_LOG = logging.getLogger(__name__)


# ─────────────── enqueue API (يُستدعى من الـ adapter) ───────────────


def enqueue_subscriber_upsert(sub: Subscriber) -> None:
    sync_queue_repo.enqueue(
        tenant_id=sub.tenant_id, kind="subscriber_upsert",
        entity_id=sub.id, entity_key=sub.username,
        payload=_subscriber_payload(sub),
    )


def enqueue_subscriber_delete(tenant_id: int, username: str) -> None:
    sync_queue_repo.enqueue(
        tenant_id=tenant_id, kind="subscriber_delete",
        entity_key=username, payload={"username": username},
    )


def enqueue_plan_upsert(plan: AccessPlan) -> None:
    sync_queue_repo.enqueue(
        tenant_id=plan.tenant_id, kind="plan_upsert",
        entity_id=plan.id, entity_key=plan.name,
        payload=_plan_payload(plan),
    )


def enqueue_plan_delete(tenant_id: int, plan_name: str) -> None:
    sync_queue_repo.enqueue(
        tenant_id=tenant_id, kind="plan_delete",
        entity_key=plan_name, payload={"name": plan_name},
    )


def enqueue_pool_upsert(pool: IpPool) -> None:
    sync_queue_repo.enqueue(
        tenant_id=pool.tenant_id, kind="pool_upsert",
        entity_id=pool.id, entity_key=pool.pool_name,
        payload={"name": pool.pool_name, "ranges": pool.range_ip},
    )


def enqueue_disconnect(tenant_id: int, username: str) -> None:
    sync_queue_repo.enqueue(
        tenant_id=tenant_id, kind="disconnect",
        entity_key=username, payload={"username": username},
    )


def enqueue_reset_password(tenant_id: int, username: str, new_password: str) -> None:
    sync_queue_repo.enqueue(
        tenant_id=tenant_id, kind="reset_password",
        entity_key=username,
        payload={"username": username, "password": new_password},
    )


# ─────────────── execute (يُستدعى من sync_worker) ───────────────


def execute_job(job: dict) -> tuple[bool, str]:
    """
    ينفّذ الـ job على كل routers الـ tenant المفعّلة.
    يُرجع (ok, error_text).
    """
    tenant_id = job["tenant_id"]
    routers = mikrotik_repo.list_configs(tenant_id)
    enabled = [r for r in routers if r["enabled"]]
    if not enabled:
        # لا routers — نعتبر الـ job منجزًا (DB-only mode)
        _LOG.info("no enabled routers for tenant=%d, job=%s done as noop",
                  tenant_id, job["kind"])
        return True, ""

    errors: list[str] = []
    for r in enabled:
        try:
            _execute_on_router(r, job)
        except (AuthError, ConnectError) as e:
            errors.append(f"router={r['id']}({r['host']}): {e}")
        except MikrotikTrap as e:
            errors.append(f"router={r['id']}({r['host']}) trap: {e.message}")
        except MikrotikError as e:
            errors.append(f"router={r['id']}({r['host']}): {e}")
    if errors:
        return False, " | ".join(errors)
    return True, ""


# ─────────────── per-router executor ───────────────


def _execute_on_router(router: dict, job: dict) -> None:
    kind = job["kind"]
    payload = job.get("payload") or {}
    with acquire_mt(router) as c:
        if kind == "subscriber_upsert":
            _sync_subscriber(c, payload)
        elif kind == "subscriber_delete":
            _delete_subscriber(c, payload)
        elif kind == "plan_upsert":
            _sync_plan(c, payload)
        elif kind == "plan_delete":
            _delete_plan(c, payload)
        elif kind == "pool_upsert":
            _sync_pool(c, payload)
        elif kind == "disconnect":
            _disconnect(c, payload)
        elif kind == "reset_password":
            _reset_password(c, payload)
        else:
            raise MikrotikError(f"unknown sync kind: {kind!r}")


# ─────────────── operations ───────────────


def _find_user_id(c: MikrotikClient, username: str) -> Optional[str]:
    for r in c.print_("/ip/hotspot/user/print", queries=[f"?name={username}"]):
        return r.get(".id")
    return None


def _find_profile_id(c: MikrotikClient, name: str) -> Optional[str]:
    for r in c.print_("/ip/hotspot/user/profile/print", queries=[f"?name={name}"]):
        return r.get(".id")
    return None


def _find_pool_id(c: MikrotikClient, name: str) -> Optional[str]:
    for r in c.print_("/ip/pool/print", queries=[f"?name={name}"]):
        return r.get(".id")
    return None


def _sync_subscriber(c: MikrotikClient, p: dict) -> None:
    """upsert /ip/hotspot/user/."""
    attrs = {
        "name": p["username"],
        "password": p["password"],
        "disabled": "yes" if p.get("status") == "disabled" else "no",
    }
    if p.get("profile_name"): attrs["profile"] = p["profile_name"]
    if p.get("mac_lock"):     attrs["mac-address"] = p["mac_lock"]
    if p.get("static_ip"):    attrs["address"] = p["static_ip"]
    if p.get("email"):        attrs["email"] = p["email"]
    if p.get("remark"):       attrs["comment"] = p["remark"][:200]

    existing = _find_user_id(c, p["username"])
    if existing is None:
        c.run("/ip/hotspot/user/add", attrs)
    else:
        attrs2 = {k: v for k, v in attrs.items() if k != "name"}
        c.run("/ip/hotspot/user/set", {**attrs2, ".id": existing})


def _delete_subscriber(c: MikrotikClient, p: dict) -> None:
    uid = _find_user_id(c, p["username"])
    if uid is not None:
        c.run("/ip/hotspot/user/remove", {".id": uid})


def _sync_plan(c: MikrotikClient, p: dict) -> None:
    """upsert /ip/hotspot/user/profile/."""
    attrs = {"name": p["name"]}
    if p.get("speed_up_kbps") or p.get("speed_down_kbps"):
        attrs["rate-limit"] = f"{p.get('speed_up_kbps',0)}k/{p.get('speed_down_kbps',0)}k"
    if p.get("session_timeout_sec"): attrs["session-timeout"] = f"{p['session_timeout_sec']}s"
    if p.get("idle_timeout_sec"):    attrs["idle-timeout"] = f"{p['idle_timeout_sec']}s"
    if p.get("concurrent_sessions"): attrs["shared-users"] = str(p["concurrent_sessions"])
    if p.get("address_pool"):        attrs["address-pool"] = p["address_pool"]

    existing = _find_profile_id(c, p["name"])
    if existing is None:
        c.run("/ip/hotspot/user/profile/add", attrs)
    else:
        attrs2 = {k: v for k, v in attrs.items() if k != "name"}
        c.run("/ip/hotspot/user/profile/set", {**attrs2, ".id": existing})


def _delete_plan(c: MikrotikClient, p: dict) -> None:
    pid = _find_profile_id(c, p["name"])
    if pid is not None:
        c.run("/ip/hotspot/user/profile/remove", {".id": pid})


def _sync_pool(c: MikrotikClient, p: dict) -> None:
    attrs = {"name": p["name"], "ranges": p["ranges"]}
    existing = _find_pool_id(c, p["name"])
    if existing is None:
        c.run("/ip/pool/add", attrs)
    else:
        c.run("/ip/pool/set", {"ranges": p["ranges"], ".id": existing})


def _disconnect(c: MikrotikClient, p: dict) -> None:
    """يجد الجلسة النشطة ويحذفها."""
    target_id = None
    for r in c.print_("/ip/hotspot/active/print", queries=[f"?user={p['username']}"]):
        target_id = r.get(".id")
        break
    if target_id is None:
        return  # لا جلسة نشطة — تمام
    c.run("/ip/hotspot/active/remove", {".id": target_id})


def _reset_password(c: MikrotikClient, p: dict) -> None:
    uid = _find_user_id(c, p["username"])
    if uid is None:
        return  # المستخدم غير موجود على MT بعد — sync لاحقًا سيُنشئه
    c.run("/ip/hotspot/user/set", {".id": uid, "password": p["password"]})


# ─────────────── payloads ───────────────


def _subscriber_payload(s: Subscriber) -> dict:
    """payload خفيف فيه ما يحتاجه MT — snapshot وقت الـ enqueue."""
    plan_name = ""
    if s.plan_id:
        try:
            from ..db.repos import plans_repo
            pl = plans_repo.get_plan(s.tenant_id, s.plan_id)
            if pl: plan_name = pl.name
        except Exception:  # noqa: BLE001
            pass
    return {
        "username": s.username, "password": s.password,
        "status": s.status, "mac_lock": s.mac_lock or "",
        "static_ip": s.static_ip or "", "email": s.email or "",
        "remark": s.remark or "",
        "plan_id": s.plan_id, "profile_name": plan_name,
    }


def _plan_payload(p: AccessPlan) -> dict:
    return {
        "name": p.name,
        "speed_up_kbps": p.speed_up_kbps,
        "speed_down_kbps": p.speed_down_kbps,
        "session_timeout_sec": p.session_timeout_sec,
        "idle_timeout_sec": p.idle_timeout_sec,
        "concurrent_sessions": p.concurrent_sessions,
        "address_pool": p.address_pool or "",
    }
