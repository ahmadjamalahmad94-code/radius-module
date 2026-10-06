"""Availability of the operations assistant for a tenant.

1. Feature flag ``ops_assistant.enabled`` (tenant setting) — default OFF.
2. Owner rule (2026-10-06): the assistant is UNAVAILABLE for a tenant while any
   admin who can act on it still has a default / seed / temporary password.
   Same idea as ``hoberadius-ai-support/src/readgw/activation.py``, implemented
   in-app with the app's own ``admins_repo.verify_password``:

   admins that can act on tenant t = enabled, non-deleted admins that are an
   active member of t, or carry the raw ``is_super_admin`` flag, or — for
   t = 1 — have no membership at all (the login lands them in tenant 1).

   An admin is "weak" when ``must_change_password`` is set (temporary /
   one-time password not yet changed) or the hash verifies against a public
   default (seed values, common defaults, the username itself).

``password_hash`` is read in-process only to verify candidates in memory; it is
never returned or logged. The result carries COUNTS only.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Optional

FLAG_KEY = "ops_assistant.enabled"

DEFAULT_PASSWORD_CANDIDATES = ("admin", "operator", "password", "123456", "12345678",
                               "123456789", "admin123", "hoberadius", "changeme",
                               "change-me")

# verdict per (admin_id, sha256(password_hash)): a hash never changes its
# verdict, so a password change invalidates the entry by itself.
_VERDICTS: dict[tuple[int, str], bool] = {}
_LOCK = threading.Lock()


def flag_enabled(tenant_id: int) -> bool:
    from ...db.repos import tenants_repo
    try:
        raw = tenants_repo.get_setting(int(tenant_id), FLAG_KEY, "0")
    except Exception:  # noqa: BLE001 — fail closed
        return False
    return str(raw or "0").strip().lower() in {"1", "true", "yes", "on"}


def set_flag(tenant_id: int, enabled: bool, *, by: int = 0) -> None:
    from ...db.repos import tenants_repo
    tenants_repo.set_setting(int(tenant_id), FLAG_KEY, "1" if enabled else "0", by=int(by or 0))


def _candidates(username: str) -> tuple[str, ...]:
    try:
        from ...core.initial_credentials import KNOWN_DEFAULT_PASSWORDS
        known = tuple(sorted(p for p in KNOWN_DEFAULT_PASSWORDS if p))
    except Exception:  # noqa: BLE001
        known = ()
    seen, out = set(), []
    for c in (*DEFAULT_PASSWORD_CANDIDATES, *known, str(username or "")):
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return tuple(out)


def _is_default_password(admin_id: int, username: str, password_hash: str) -> bool:
    key = (int(admin_id), hashlib.sha256(str(password_hash or "").encode()).hexdigest())
    with _LOCK:
        hit = _VERDICTS.get(key)
    if hit is not None:
        return hit
    from ...db.repos import admins_repo
    weak = False
    for cand in _candidates(username):
        try:
            if admins_repo.verify_password(cand, str(password_hash or "")):
                weak = True
                break
        except Exception:  # noqa: BLE001 — unverifiable ≠ weak
            continue
    with _LOCK:
        _VERDICTS[key] = weak
    return weak


def _cols(table: str) -> set[str]:
    from ...db.connection import db
    return {r[1] for r in db().execute(f"PRAGMA table_info({table})").fetchall()}


def password_gate(tenant_id: int) -> dict:
    """``{"ready": bool, "admins_checked", "default_password_count",
    "must_change_count"}`` — counts only, never ids/hashes."""
    from ...db.connection import db
    t = int(tenant_id)
    a = _cols("admins")
    m = _cols("tenant_memberships")
    conds = ["a.is_super_admin = 1"] if "is_super_admin" in a else []
    if m:
        mstatus = "AND m.status = 'active'" if "status" in m else ""
        conds.append("EXISTS (SELECT 1 FROM tenant_memberships m WHERE m.admin_id = a.id "
                     f"AND m.tenant_id = :t {mstatus})")
        if t == 1:
            conds.append("NOT EXISTS (SELECT 1 FROM tenant_memberships m WHERE m.admin_id = a.id)")
    else:
        conds.append("1 = 1")
    where = ["(" + " OR ".join(conds) + ")"]
    if "enabled" in a:
        where.append("a.enabled = 1")
    if "deleted_at" in a:
        where.append("a.deleted_at IS NULL")
    mc = "a.must_change_password" if "must_change_password" in a else "0"
    rows = db().execute(
        f"SELECT a.id, a.username, a.password_hash, {mc} AS must FROM admins a "
        f"WHERE {' AND '.join(where)}", {"t": t}).fetchall()
    weak = pending = 0
    for r in rows:
        if r["must"]:
            pending += 1
        if _is_default_password(int(r["id"]), r["username"], r["password_hash"]):
            weak += 1
    return {"ready": weak == 0 and pending == 0, "admins_checked": len(rows),
            "default_password_count": weak, "must_change_count": pending}


def availability(tenant_id: int) -> dict:
    """``{"available", "flag_enabled", "reason", "password_gate"}``."""
    enabled = flag_enabled(tenant_id)
    gate: Optional[dict] = password_gate(tenant_id) if enabled else None
    if not enabled:
        reason = "disabled"
    elif not gate["ready"]:
        reason = "weak_admin_passwords"
    else:
        reason = ""
    return {"available": enabled and bool(gate and gate["ready"]), "flag_enabled": enabled,
            "reason": reason, "password_gate": gate}


def reset_cache() -> None:
    with _LOCK:
        _VERDICTS.clear()


__all__ = ["FLAG_KEY", "flag_enabled", "set_flag", "password_gate", "availability",
           "reset_cache"]
