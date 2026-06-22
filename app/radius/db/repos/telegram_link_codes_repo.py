"""telegram_link_codes repo — رموز ربط تيليجرام لمرّة واحدة + إزاحة getUpdates.

فوق مخطّط migration 133 (telegram_link_codes + telegram_poll_state). يدعم مسار
«اربط تيليجرام» بضغطة واحدة:
  • create_code(...)  → يولّد رمزًا فريدًا قصيرًا ويُبطل رموز النطاق السابقة.
  • get_by_code(code) → مطابقة ``/start <code>`` الواردة من البوت.
  • get_active(...)   → الرمز المعلّق غير المنتهي لنطاق/مشترك (للاستطلاع).
  • mark_linked(...)  → تخزين chat_id الملتقَط.
  • cursor get/set    → إزاحة getUpdates لكل مستأجر (= لكل بوت).

عمود المخطّط ``target`` (admin|subscriber)؛ تبقى واجهة بايثون باسم ``scope``
لوضوح المستدعي. النافذة قصيرة (افتراضي 120ث) فالرمز يصلح مرّة واحدة؛ المنتهية
تُكنَس كسولًا. الرمز من أبجديّة غير ملتبسة لتفادي الخلط البصري في الرابط/المسح.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso, parse_dt

# أبجديّة بلا أحرف ملتبسة (0/O، 1/I/l). الرمز ضمن مسموح تيليجرام لـ/start
# ([A-Za-z0-9_-]، ≤64 بايت) فلا حاجة لترميز إضافي.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 10
_DEFAULT_TTL_SEC = 120


def _gen_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def _row_to_dict(r) -> dict:
    # نكشف العمود ``target`` تحت المفتاح ``scope`` لثبات واجهة الخدمة.
    return {
        "code": r["code"],
        "tenant_id": int(r["tenant_id"]),
        "scope": r["target"],
        "subscriber_id": int(r["subscriber_id"]),
        "status": r["status"],
        "chat_id": r["chat_id"] or "",
        "account_name": r["account_name"] or "",
        "created_at": r["created_at"] or "",
        "expires_at": r["expires_at"] or "",
        "linked_at": r["linked_at"] or "",
    }


def _is_expired(row: dict, *, now: Optional[str] = None) -> bool:
    exp = parse_dt(row.get("expires_at") or "")
    if not exp:
        return False
    ref = parse_dt(now or now_iso())
    return ref is not None and ref > exp


def expire_stale(*, now: Optional[str] = None) -> int:
    """يُعلّم الرموز المعلّقة المنتهية ``expired`` (كنس كسول، idempotent)."""
    ref = now or now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE telegram_link_codes SET status='expired' "
            "WHERE status='pending' AND expires_at <> '' AND expires_at < ?",
            (ref,),
        )
        return cur.rowcount or 0


def create_code(
    *,
    tenant_id: int,
    scope: str = "admin",
    subscriber_id: int = 0,
    ttl_sec: int = _DEFAULT_TTL_SEC,
) -> dict:
    """يولّد رمز ربط جديدًا. يُبطل أيّ رمز معلّق سابق لنفس النطاق/المشترك كي
    يبقى رمز نشط واحد فقط (يمنع التباس استطلاعين متزامنين)."""
    if scope not in ("admin", "subscriber"):
        raise ValueError("scope غير صالح")
    now = parse_dt(now_iso())
    created = now_iso()
    expires = (now + timedelta(seconds=int(ttl_sec))).isoformat() + "Z" if now else ""
    with transaction() as conn:
        # أبطِل المعلّق السابق لنفس النطاق.
        conn.execute(
            "UPDATE telegram_link_codes SET status='expired' "
            "WHERE tenant_id=? AND target=? AND subscriber_id=? AND status='pending'",
            (int(tenant_id), scope, int(subscriber_id)),
        )
        # ولّد رمزًا فريدًا (إعادة محاولة عند التصادم النادر).
        code = ""
        for _ in range(8):
            cand = _gen_code()
            exists = conn.execute(
                "SELECT 1 FROM telegram_link_codes WHERE code=?", (cand,)
            ).fetchone()
            if not exists:
                code = cand
                break
        if not code:  # احتمال شبه معدوم
            raise RuntimeError("تعذّر توليد رمز فريد")
        conn.execute(
            "INSERT INTO telegram_link_codes ("
            "  code, tenant_id, target, subscriber_id, status,"
            "  created_at, expires_at"
            ") VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (code, int(tenant_id), scope, int(subscriber_id), created, expires),
        )
    return {
        "code": code, "tenant_id": int(tenant_id), "scope": scope,
        "subscriber_id": int(subscriber_id), "status": "pending",
        "chat_id": "", "account_name": "", "created_at": created,
        "expires_at": expires, "linked_at": "",
    }


def get_by_code(code: str) -> Optional[dict]:
    if not code:
        return None
    r = db().execute(
        "SELECT * FROM telegram_link_codes WHERE code=?", (code,)
    ).fetchone()
    return _row_to_dict(r) if r else None


def get_active(
    *, tenant_id: int, scope: str = "admin", subscriber_id: int = 0,
    now: Optional[str] = None,
) -> Optional[dict]:
    """آخر رمز معلّق غير منتهٍ لنطاق/مشترك معيّن (يُستخدم في الاستطلاع)."""
    r = db().execute(
        "SELECT * FROM telegram_link_codes "
        "WHERE tenant_id=? AND target=? AND subscriber_id=? AND status='pending' "
        "ORDER BY created_at DESC LIMIT 1",
        (int(tenant_id), scope, int(subscriber_id)),
    ).fetchone()
    if not r:
        return None
    row = _row_to_dict(r)
    if _is_expired(row, now=now):
        return None
    return row


def mark_linked(code: str, *, chat_id: str, account_name: str = "",
                now: Optional[str] = None) -> bool:
    """يثبّت الالتقاط: status=linked + chat_id + الاسم. idempotent (يتجاهل
    الرموز غير المعلّقة)."""
    ts = now or now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE telegram_link_codes "
            "SET status='linked', chat_id=?, account_name=?, linked_at=? "
            "WHERE code=? AND status='pending'",
            (str(chat_id), str(account_name or ""), ts, code),
        )
        return bool(cur.rowcount)


def recent(*, tenant_id: int, scope: str = "admin",
           subscriber_id: int = 0) -> Optional[dict]:
    """آخر سجل (بأي حالة) لنطاق/مشترك — لإظهار «متصل ✓» بعد انتهاء النافذة."""
    r = db().execute(
        "SELECT * FROM telegram_link_codes "
        "WHERE tenant_id=? AND target=? AND subscriber_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (int(tenant_id), scope, int(subscriber_id)),
    ).fetchone()
    return _row_to_dict(r) if r else None


# ── إزاحة getUpdates لكل مستأجر (telegram_poll_state) ────────────────────
def get_cursor(tenant_id: int) -> int:
    r = db().execute(
        "SELECT last_update_id FROM telegram_poll_state WHERE tenant_id=?",
        (int(tenant_id),),
    ).fetchone()
    return int(r["last_update_id"]) if r else 0


def set_cursor(tenant_id: int, next_offset: int) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO telegram_poll_state (tenant_id, last_update_id, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET "
            "  last_update_id=excluded.last_update_id, updated_at=excluded.updated_at",
            (int(tenant_id), int(next_offset), now_iso()),
        )
