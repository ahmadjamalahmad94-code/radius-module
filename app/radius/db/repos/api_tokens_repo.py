"""API Tokens repo — hash-only storage."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Optional

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, parse_dt


def _row(r) -> dict:
    return {
        "id": r["id"], "tenant_id": r["tenant_id"], "name": r["name"],
        "token_hash": r["token_hash"],
        "scopes": json_load(r["scopes_json"], default=[]),
        "last_used_at": r["last_used_at"], "expires_at": r["expires_at"],
        "revoked": bool(r["revoked"]),
        "created_by": r["created_by"] or 0,
        "created_at": r["created_at"],
    }


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def gen_plain_token() -> str:
    """يولّد token جديد. يُعرض مرّة واحدة فقط."""
    return "hr_" + secrets.token_urlsafe(32)


def list_tokens(tenant_id: int) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM api_tokens WHERE tenant_id = ? ORDER BY id DESC",
        (tenant_id,))
    return [_row(r) for r in cur.fetchall()]


def create_token(*, tenant_id: int, name: str, scopes: list[str] | None = None,
                  created_by: int = 0,
                  expires_at: Optional[datetime] = None) -> tuple[dict, str]:
    """يُرجع (record, plaintext_token). الـ plaintext يُعرض مرّة واحدة.

    `expires_at` (UTC datetime) is stored as ISO-8601 with a trailing "Z" so
    `parse_dt` can read it back. None = never expires (legacy behaviour)."""
    plain = gen_plain_token()
    th = hash_token(plain)
    exp_iso = (expires_at.isoformat() + "Z") if expires_at else None
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO api_tokens(tenant_id, name, token_hash, scopes_json,
                revoked, created_by, created_at, expires_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (tenant_id, name, th, json_dump(scopes or []), 0, created_by, now_iso(), exp_iso))
        new_id = cur.lastrowid
    cur = db().execute("SELECT * FROM api_tokens WHERE id = ?", (new_id,))
    return _row(cur.fetchone()), plain


def revoke_token(tenant_id: int, tid: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE api_tokens SET revoked = 1 WHERE tenant_id = ? AND id = ?",
                     (tenant_id, tid))


def resolve_by_plain(plain: str) -> Optional[dict]:
    """يبحث عن token بواسطة plaintext (للـ auth middleware)."""
    th = hash_token(plain)
    cur = db().execute(
        "SELECT * FROM api_tokens WHERE token_hash = ? AND revoked = 0",
        (th,))
    row = cur.fetchone()
    return _row(row) if row else None


def touch_used(tid: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                     (now_iso(), tid))
