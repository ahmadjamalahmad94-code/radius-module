"""منع التكرار (Idempotency) — منطقٌ واحد للـAPI والويب.

المفتاح (``Idempotency-Key`` أو ``client_request_id``) يُحجز «pending» قبل
التنفيذ (UNIQUE لكل مستأجر + مفتاح + نطاق)، ويُحفظ الردّ عند النجاح، ويُحرَّر
عند الفشل. موجة الإصلاح 2 أضافت **بصمة الطلب**: المفتاح نفسه مع جسمٍ مختلف أو
على مسارٍ آخر (مشترك آخر) ⇒ ``mismatch`` (422) بدل إعادة نتيجة الطلب الأوّل
بصمت. ويُرفض المفتاح الأطول من ``MAX_KEY``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from ..db.connection import db, transaction

TTL_HOURS = 24
MAX_KEY = 200
MISMATCH_AR = "مفتاح التكرار استُخدم لطلب مختلف."
TOO_LONG_AR = f"مفتاح التكرار طويل جدًا (الحدّ {MAX_KEY} حرفًا)."

CLAIMED = "claimed"
REPLAY = "replay"
IN_PROGRESS = "in_progress"
MISMATCH = "mismatch"


def fingerprint(method: str, path: str, body: Any) -> str:
    """بصمة الطلب: الطريقة + المسار + الجسم (مرتّب المفاتيح، بلا client_request_id)."""
    if isinstance(body, dict):
        body = {k: v for k, v in body.items()
                if k not in {"client_request_id", "_csrf_token", "csrf_token"}}
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{method} {path}\n{raw}".encode("utf-8")).hexdigest()


def claim(tenant_id: int, key: str, scope: str, request_hash: str
          ) -> tuple[str, Optional[dict]]:
    """احجز المفتاح. يُعيد (الحالة، الصفّ المحفوظ عند REPLAY)."""
    now = datetime.utcnow()
    cutoff = (now - timedelta(hours=TTL_HOURS)).isoformat()
    tid = int(tenant_id or 1)
    with transaction() as conn:
        conn.execute("DELETE FROM api_idempotency_keys WHERE created_at < ?", (cutoff,))
        rows = conn.execute(
            "SELECT scope, request_hash, state, status_code, response_json "
            "FROM api_idempotency_keys WHERE tenant_id = ? AND idem_key = ?",
            (tid, key),
        ).fetchall()
        for row in rows:
            if row["scope"] != scope or (row["request_hash"] and row["request_hash"] != request_hash):
                return MISMATCH, None
        if rows:
            row = rows[0]
            if row["state"] == "done":
                return REPLAY, dict(row)
            return IN_PROGRESS, None
        try:
            conn.execute(
                "INSERT INTO api_idempotency_keys(tenant_id, idem_key, scope, state, "
                "created_at, request_hash) VALUES(?,?,?,'pending',?,?)",
                (tid, key, scope, now.isoformat(), request_hash))
        except sqlite3.IntegrityError:
            return IN_PROGRESS, None
    return CLAIMED, None


def finish(tenant_id: int, key: str, scope: str, status_code: int,
           response_text: str) -> None:
    """احفظ ردّ النجاح (يُعاد لأيّ تكرارٍ لاحق بالمفتاح نفسه)."""
    try:
        with transaction() as conn:
            conn.execute(
                "UPDATE api_idempotency_keys SET state = 'done', status_code = ?, "
                "response_json = ? WHERE tenant_id = ? AND idem_key = ? AND scope = ?",
                (int(status_code), response_text, int(tenant_id or 1), key, scope))
    except Exception:  # noqa: BLE001 — الإجراء نفسه نجح
        release(tenant_id, key, scope)


def release(tenant_id: int, key: str, scope: str) -> None:
    """حرّر مفتاحًا فشل طلبه كي يُعاد المحاولة به."""
    try:
        with transaction() as conn:
            conn.execute(
                "DELETE FROM api_idempotency_keys WHERE tenant_id = ? AND idem_key = ? "
                "AND scope = ? AND state = 'pending'", (int(tenant_id or 1), key, scope))
    except Exception:  # noqa: BLE001 — لا نُخفي النتيجة الحقيقيّة
        pass


def stored(tenant_id: int, key: str, scope: str) -> Optional[dict]:
    row = db().execute(
        "SELECT state, status_code, response_json FROM api_idempotency_keys "
        "WHERE tenant_id = ? AND idem_key = ? AND scope = ?",
        (int(tenant_id or 1), key, scope)).fetchone()
    return dict(row) if row else None


__all__ = ["CLAIMED", "IN_PROGRESS", "MAX_KEY", "MISMATCH", "MISMATCH_AR", "REPLAY",
           "TOO_LONG_AR", "TTL_HOURS", "claim", "finish", "fingerprint", "release",
           "stored"]
