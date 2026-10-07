"""Persistence for the operations-assistant executor (migration 210).

Every read is keyed by (tenant_id, admin_id) of the CURRENT credential, so a
conversation id from another tenant or another admin is simply not found.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterable, Optional

from ...db.connection import db, transaction
from ...db.helpers import now_iso


def new_conversation(tenant_id: int, admin_id: int, event: Optional[dict]) -> str:
    cid = uuid.uuid4().hex
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO ops_conversations(id, tenant_id, admin_id, event_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (cid, int(tenant_id), int(admin_id),
             json.dumps(event, ensure_ascii=False) if event else None, now, now))
    return cid


def get_conversation(cid: str, tenant_id: int, admin_id: int) -> Optional[dict]:
    if not cid or not isinstance(cid, str) or len(cid) > 64:
        return None
    row = db().execute(
        "SELECT * FROM ops_conversations WHERE id=? AND tenant_id=? AND admin_id=?",
        (cid, int(tenant_id), int(admin_id))).fetchone()
    if not row:
        return None
    out = dict(row)
    out["event"] = json.loads(out["event_json"]) if out.get("event_json") else None
    return out


def issue(cid: str, tenant_id: int, kind: str, values: Iterable[Any], source: str) -> None:
    now = now_iso()
    rows = []
    for v in values:
        if v in (None, ""):
            continue
        try:
            rows.append((cid, int(tenant_id), kind, _norm(kind, v), source, now))
        except (TypeError, ValueError):
            continue                  # never issue a value that is not a clean id
    if not rows:
        return
    with transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO ops_issued_choices(conversation_id, tenant_id, kind, value, "
            "source, created_at) VALUES (?,?,?,?,?,?)", rows)


def _norm(kind: str, value: Any) -> str:
    # usernames are unique case-insensitively (users.py) → compare lower-cased
    if kind == "subscriber":
        return str(value).strip().lower()
    # record ids: a real integer (or its plain decimal text) only — never
    # True → 1, 1.9 → 1 or "1e0" (``/choices`` passes the body value as is)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError("record id must be an integer")
    text = str(value).strip()
    if not text.isdigit() or not text.isascii():
        raise ValueError("record id must be an integer")
    return str(int(text))


def is_issued(cid: str, tenant_id: int, kind: str, value: Any) -> bool:
    try:
        key = _norm(kind, value)
    except (TypeError, ValueError):
        return False
    row = db().execute(
        "SELECT 1 FROM ops_issued_choices WHERE conversation_id=? AND tenant_id=? AND kind=? "
        "AND value=?", (cid, int(tenant_id), kind, key)).fetchone()
    return row is not None


def save_proposal(*, cid: str, tenant_id: int, admin_id: int, action: str, mode: str,
                  proposal: dict, proposal_hash: str) -> dict:
    pid = uuid.uuid4().hex
    key = "ops-" + uuid.uuid4().hex
    now = now_iso()
    status = "draft" if mode == "draft" else "pending"
    with transaction() as conn:
        conn.execute(
            "INSERT INTO ops_proposals(id, conversation_id, tenant_id, admin_id, action, mode, "
            "proposal_json, proposal_hash, idempotency_key, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, cid, int(tenant_id), int(admin_id), action, mode,
             json.dumps(proposal, ensure_ascii=False, sort_keys=True), proposal_hash, key,
             status, now, now))
    return {"id": pid, "idempotency_key": key, "status": status}


def get_proposal(pid: str, cid: str, tenant_id: int, admin_id: int) -> Optional[dict]:
    if not pid or not isinstance(pid, str) or len(pid) > 64:
        return None
    row = db().execute(
        "SELECT * FROM ops_proposals WHERE id=? AND conversation_id=? AND tenant_id=? "
        "AND admin_id=?", (pid, cid, int(tenant_id), int(admin_id))).fetchone()
    if not row:
        return None
    out = dict(row)
    out["proposal"] = json.loads(out["proposal_json"])
    out["result"] = json.loads(out["result_json"]) if out.get("result_json") else None
    return out


def proposal_by_key(tenant_id: int, key: str) -> Optional[dict]:
    row = db().execute("SELECT id, conversation_id, status FROM ops_proposals "
                       "WHERE tenant_id=? AND idempotency_key=?",
                       (int(tenant_id), key)).fetchone()
    return dict(row) if row else None


def set_key(pid: str, tenant_id: int, key: str) -> None:
    with transaction() as conn:
        conn.execute("UPDATE ops_proposals SET idempotency_key=?, updated_at=? "
                     "WHERE id=? AND tenant_id=? AND status='pending'",
                     (key, now_iso(), pid, int(tenant_id)))


def claim(pid: str, tenant_id: int) -> bool:
    """pending → executing, atomically (one confirmation runs, never two)."""
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE ops_proposals SET status='executing', confirmed_at=?, updated_at=? "
            "WHERE id=? AND tenant_id=? AND status='pending'",
            (now_iso(), now_iso(), pid, int(tenant_id)))
        return cur.rowcount == 1


def finish(pid: str, tenant_id: int, status: str, result: dict) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE ops_proposals SET status=?, result_json=?, updated_at=? "
            "WHERE id=? AND tenant_id=?",
            (status, json.dumps(result, ensure_ascii=False, default=str), now_iso(),
             pid, int(tenant_id)))


__all__ = ["new_conversation", "get_conversation", "issue", "is_issued", "save_proposal",
           "get_proposal", "proposal_by_key", "set_key", "claim", "finish"]
