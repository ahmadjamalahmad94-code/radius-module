"""site_exit_scripts_repo — VX2 generated-script versions.

Append-only history. Every preview that produces a (forward,
rollback) pair gets a row here so the operator can:
  - reproduce a past deployment exactly (audit)
  - diff between versions (UI in VX2.4)
  - inspect a rollback before clicking it

Body content rules:
  - script_body must NOT contain secrets — WireGuard private
    keys never reach this layer. The planner/renderer (VX2.3)
    enforces this; the repo is the last line of defence and
    rejects any body containing 'private-key=' as a tripwire.
  - command_count is the renderer's count of effective lines.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from ..connection import db, transaction


_SECRET_TRIPWIRES = (
    "private-key=",
    "PrivateKey =",
    "private_key=",
    "BEGIN PRIVATE KEY",
)


class SecretInScriptError(ValueError):
    """Raised when a script body contains a tripwire substring.

    Surfaced loudly so the renderer's safety contract is never
    bypassed accidentally."""


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def compute_hash(body: str) -> str:
    """SHA-256 of the body in UTF-8. The same algorithm the
    deployment row stores so the two can be cross-referenced."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _assert_no_secrets(body: str) -> None:
    if not body:
        return
    for tripwire in _SECRET_TRIPWIRES:
        if tripwire in body:
            raise SecretInScriptError(
                f"refusing to store script — tripwire {tripwire!r}"
                " detected. WireGuard private keys must not"
                " leak into the generated script."
            )


def record(
    *, policy_id: int,
    script_body: str,
    rollback_script_body: str = "",
    deployment_id: Optional[int] = None,
    generated_by_admin_id: Optional[int] = None,
    command_count: Optional[int] = None,
) -> int:
    _assert_no_secrets(script_body)
    _assert_no_secrets(rollback_script_body)
    h = compute_hash(script_body)
    if command_count is None:
        # crude line count of non-blank, non-comment lines —
        # callers pass an authoritative count when they have
        # one (see the renderer).
        command_count = sum(
            1 for ln in (script_body or "").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO site_exit_script_versions
                (policy_id, deployment_id, script_hash,
                 script_body, rollback_script_body,
                 command_count, generated_by_admin_id,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(policy_id),
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
        "SELECT * FROM site_exit_script_versions WHERE id=?",
        (int(version_id),),
    ).fetchone()
    return dict(row) if row else None


def get_by_hash(script_hash: str) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM site_exit_script_versions "
        "WHERE script_hash=? ORDER BY id DESC LIMIT 1",
        (str(script_hash),),
    ).fetchone()
    return dict(row) if row else None


def latest_for_policy(policy_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM site_exit_script_versions "
        "WHERE policy_id=? ORDER BY id DESC LIMIT 1",
        (int(policy_id),),
    ).fetchone()
    return dict(row) if row else None


def list_for_policy(
    policy_id: int, *, limit: int = 50,
) -> list[dict]:
    """Returns versions newest-first, without bodies, to keep
    the response small. Callers that need a body call
    get_by_id."""
    rows = db().execute(
        "SELECT id, policy_id, deployment_id, script_hash, "
        "       command_count, generated_by_admin_id, created_at "
        "FROM site_exit_script_versions "
        "WHERE policy_id=? "
        "ORDER BY id DESC LIMIT ?",
        (int(policy_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "SecretInScriptError",
    "compute_hash",
    "record", "get_by_id", "get_by_hash",
    "latest_for_policy", "list_for_policy",
]
