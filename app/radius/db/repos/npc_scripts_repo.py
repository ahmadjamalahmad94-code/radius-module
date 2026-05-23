"""npc_scripts_repo — append-only generated-script history,
shared across the three NPC sub-services.

Same shape as VX2's `site_exit_scripts_repo` — every preview
produces a (forward, rollback) pair that lands here so the
operator can reproduce, diff, or roll back exactly.

Tripwires for secrets in the body mirror VX2: WireGuard private
keys, BEGIN PRIVATE KEY blocks, and `private-key=` substrings
are refused at this layer regardless of upstream renderer
behaviour. The repo is the last line of defence.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from ..connection import db, transaction
from .npc_common import assert_service, now_iso


# Tripwire substrings — if any of these appear in a body the
# repo refuses to persist. The renderer (Phase 2) enforces the
# same set, but a defensive duplicate here lets a careless
# refactor get caught even before it reaches the test suite.
_SECRET_TRIPWIRES = (
    "private-key=",
    "PrivateKey =",
    "private_key=",
    "BEGIN PRIVATE KEY",
    # Passwords on /ip/service and /user — NPC never emits these
    # because it operates on existing admin accounts; if they
    # ever show up, something is very wrong.
    "password=",
)


class SecretInScriptError(ValueError):
    """Raised when a body contains a tripwire substring. Loud
    on purpose so the renderer's safety contract can't be
    bypassed silently."""


def compute_hash(body: str) -> str:
    """SHA-256 hex of the body in UTF-8."""
    return hashlib.sha256(
        (body or "").encode("utf-8")
    ).hexdigest()


def _assert_no_secrets(body: str) -> None:
    if not body:
        return
    for tripwire in _SECRET_TRIPWIRES:
        if tripwire in body:
            raise SecretInScriptError(
                f"refusing to store NPC script — tripwire "
                f"{tripwire!r} detected in body."
            )


def record(
    *, service: str, policy_id: int,
    script_body: str,
    rollback_script_body: str = "",
    deployment_id: Optional[int] = None,
    generated_by_admin_id: Optional[int] = None,
    command_count: Optional[int] = None,
) -> int:
    assert_service(service)
    _assert_no_secrets(script_body)
    _assert_no_secrets(rollback_script_body)
    h = compute_hash(script_body)
    if command_count is None:
        command_count = sum(
            1 for ln in (script_body or "").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_script_versions
                (service, policy_id, deployment_id,
                 script_hash, script_body,
                 rollback_script_body, command_count,
                 generated_by_admin_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service, int(policy_id),
                int(deployment_id) if deployment_id is not None
                    else None,
                h, script_body, rollback_script_body or "",
                int(command_count),
                int(generated_by_admin_id)
                    if generated_by_admin_id is not None else None,
                now,
            ),
        )
        return int(cur.lastrowid)


def get_by_id(version_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_script_versions WHERE id=?",
        (int(version_id),),
    ).fetchone()
    return dict(row) if row else None


def get_by_hash(
    *, service: str, script_hash: str,
) -> Optional[dict]:
    assert_service(service)
    row = db().execute(
        "SELECT * FROM npc_script_versions "
        "WHERE service=? AND script_hash=? "
        "ORDER BY id DESC LIMIT 1",
        (service, str(script_hash)),
    ).fetchone()
    return dict(row) if row else None


def latest_for_policy(
    *, service: str, policy_id: int,
) -> Optional[dict]:
    assert_service(service)
    row = db().execute(
        "SELECT * FROM npc_script_versions "
        "WHERE service=? AND policy_id=? "
        "ORDER BY id DESC LIMIT 1",
        (service, int(policy_id)),
    ).fetchone()
    return dict(row) if row else None


def list_for_policy(
    *, service: str, policy_id: int,
    limit: int = 50,
) -> list[dict]:
    """Newest-first listing without bodies — keeps response
    payload sane for the UI history pane. Callers wanting a
    body fetch by id."""
    assert_service(service)
    rows = db().execute(
        "SELECT id, service, policy_id, deployment_id, "
        "       script_hash, command_count, "
        "       generated_by_admin_id, created_at "
        "FROM npc_script_versions "
        "WHERE service=? AND policy_id=? "
        "ORDER BY id DESC LIMIT ?",
        (service, int(policy_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "SecretInScriptError",
    "compute_hash",
    "record", "get_by_id", "get_by_hash",
    "latest_for_policy", "list_for_policy",
]
