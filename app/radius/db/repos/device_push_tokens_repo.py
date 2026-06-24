"""مستودع رموز دفع الأجهزة (FCM device tokens) — migration 138.

يَحفظ رمز Firebase Cloud Messaging لكل جهاز جوّال (tenant-scoped) كي
يُرسِل مُرسِل الدفع الخادمي (app/services/fcm_push.py) الإشعار إلى أجهزة
المستأجر. كل الدوال آمنة الفشل في الطبقة الأعلى — هذه الطبقة تَكتب فقط.

  • register(): upsert (tenant_id, token) — يُحدّث آخر ظهور/المنصّة بلا تكرار.
  • unregister(): حذف رمز جهاز عند تسجيل الخروج.
  • tokens_for_tenant(): كل رموز المستأجر (لإرسال multicast).
  • prune_tokens(): حذف رموز أبلغ FCM أنها غير صالحة/غير مُسجَّلة.
"""
from __future__ import annotations

from typing import Iterable

from ..connection import db, transaction
from ..helpers import now_iso


def register(tenant_id: int, token: str, *, admin_id: int = 0,
             platform: str = "", app_version: str = "") -> bool:
    """upsert رمز جهاز على (tenant_id, token). يُرجع True عند النجاح.

    التكرار (نفس الرمز لنفس المستأجر) يُحدّث آخر ظهور + المنصّة + المُسجِّل
    بدل إنشاء صفّ جديد (idempotent)."""
    tok = (token or "").strip()
    if not tok:
        return False
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO device_push_tokens("
            " tenant_id, admin_id, token, platform, app_version,"
            " last_seen_at, created_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(tenant_id, token) DO UPDATE SET"
            "   admin_id=excluded.admin_id,"
            "   platform=excluded.platform,"
            "   app_version=excluded.app_version,"
            "   last_seen_at=excluded.last_seen_at",
            (int(tenant_id), int(admin_id or 0), tok,
             (platform or "").strip(), (app_version or "").strip(), now, now),
        )
    return True


def unregister(tenant_id: int, token: str) -> int:
    """حذف رمز جهاز (تسجيل خروج). يُرجع عدد الصفوف المحذوفة (0/1)."""
    tok = (token or "").strip()
    if not tok:
        return 0
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM device_push_tokens WHERE tenant_id=? AND token=?",
            (int(tenant_id), tok),
        )
        return int(cur.rowcount or 0)


def tokens_for_tenant(tenant_id: int) -> list[str]:
    """كل رموز الدفع المُسجَّلة لهذا المستأجر (قائمة نصوص فريدة)."""
    rows = db().execute(
        "SELECT token FROM device_push_tokens WHERE tenant_id=? ORDER BY id",
        (int(tenant_id),),
    ).fetchall()
    return [r["token"] for r in rows if r["token"]]


def prune_tokens(tokens: Iterable[str]) -> int:
    """حذف رموز أبلغ FCM أنها غير صالحة/غير مُسجَّلة (عبر كل المستأجرين —
    الرمز الميّت ميّت أينما وُجد). يُرجع عدد الصفوف المحذوفة."""
    toks = [str(t).strip() for t in (tokens or []) if str(t).strip()]
    if not toks:
        return 0
    placeholders = ",".join("?" for _ in toks)
    with transaction() as conn:
        cur = conn.execute(
            f"DELETE FROM device_push_tokens WHERE token IN ({placeholders})",
            toks,
        )
        return int(cur.rowcount or 0)


def count_for_tenant(tenant_id: int) -> int:
    row = db().execute(
        "SELECT COUNT(*) AS n FROM device_push_tokens WHERE tenant_id=?",
        (int(tenant_id),),
    ).fetchone()
    return int(row["n"]) if row else 0
