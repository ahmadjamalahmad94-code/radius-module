"""hotspot_error_messages_repo — تخزين رسائل أخطاء الهوت سبوت.

صف واحد لكل (tenant_id, router_id, error_key) في جدول
hotspot_error_messages (migration 102). router_id=0 = الافتراضي
العام للمستأجر (المستخدم في v1)؛ القيم >0 محجوزة لتجاوز لكل راوتر.

نستخدم 0 بدل NULL للصف العام لأن SQLite يعتبر NULLs متمايزة في
فهارس UNIQUE — فلو كان NULL لانكسر UPSERT على الصف العام.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ...services.hotspot_error_messages import (
    ERROR_KEYS, default_messages,
)
from ..connection import db, transaction

# router_id للصف العام (الافتراضي على مستوى المستأجر).
GLOBAL_ROUTER_ID = 0


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def list_messages(tenant_id: int,
                  router_id: int = GLOBAL_ROUTER_ID) -> dict[str, dict[str, Any]]:
    """يعيد {error_key: {"message_ar": str, "enabled": bool,
    "updated_at": str}} لكل صف مخزّن لهذا (tenant, router)."""
    rows = db().execute(
        "SELECT error_key, message_ar, enabled, updated_at "
        "FROM hotspot_error_messages "
        "WHERE tenant_id=? AND router_id=?",
        (int(tenant_id), int(router_id)),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[r["error_key"]] = {
            "message_ar": r["message_ar"] or "",
            "enabled": bool(r["enabled"]),
            "updated_at": r["updated_at"] or "",
        }
    return out


def seed_defaults(tenant_id: int,
                  router_id: int = GLOBAL_ROUTER_ID) -> int:
    """يزرع أي مفتاح قياسي غائب بنصّه العربي الافتراضي ومفعّلًا.

    idempotent: INSERT OR IGNORE على القيد الفريد — لا يلمس الصفوف
    الموجودة (فلا يدوس على تعديلات المشغّل). يعيد عدد الصفوف المُضافة
    فعليًا. يُستدعى عند أول فتح للصفحة فتظهر كل المفاتيح مهيّأة."""
    defaults = default_messages()
    n = 0
    now = _now()
    with transaction() as c:
        for e in ERROR_KEYS:
            cur = c.execute(
                "INSERT OR IGNORE INTO hotspot_error_messages "
                "  (tenant_id, router_id, error_key, message_ar, "
                "   enabled, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (int(tenant_id), int(router_id), e.key,
                 defaults[e.key], now),
            )
            n += cur.rowcount or 0
    return n


def save_message(tenant_id: int, error_key: str, *,
                 message_ar: str, enabled: bool,
                 router_id: int = GLOBAL_ROUTER_ID) -> None:
    """UPSERT صفّ مفتاح واحد على القيد الفريد
    (tenant_id, router_id, error_key)."""
    with transaction() as c:
        c.execute(
            "INSERT INTO hotspot_error_messages "
            "  (tenant_id, router_id, error_key, message_ar, "
            "   enabled, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, router_id, error_key) DO UPDATE SET "
            "  message_ar = excluded.message_ar, "
            "  enabled = excluded.enabled, "
            "  updated_at = excluded.updated_at",
            (int(tenant_id), int(router_id), error_key,
             message_ar, 1 if enabled else 0, _now()),
        )


def reset_message(tenant_id: int, error_key: str,
                  router_id: int = GLOBAL_ROUTER_ID) -> None:
    """يستعيد مفتاحًا واحدًا لنصّه الافتراضي ومفعّلًا."""
    defaults = default_messages()
    if error_key not in defaults:
        return
    save_message(
        tenant_id, error_key,
        message_ar=defaults[error_key], enabled=True,
        router_id=router_id)


def reset_all(tenant_id: int,
              router_id: int = GLOBAL_ROUTER_ID) -> None:
    """يستعيد كل المفاتيح للنصوص الافتراضية ومفعّلة."""
    defaults = default_messages()
    now = _now()
    with transaction() as c:
        for e in ERROR_KEYS:
            c.execute(
                "INSERT INTO hotspot_error_messages "
                "  (tenant_id, router_id, error_key, message_ar, "
                "   enabled, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(tenant_id, router_id, error_key) DO UPDATE SET "
                "  message_ar = excluded.message_ar, "
                "  enabled = 1, "
                "  updated_at = excluded.updated_at",
                (int(tenant_id), int(router_id), e.key,
                 defaults[e.key], now),
            )


def resolved_messages(tenant_id: int,
                      router_id: int = GLOBAL_ROUTER_ID,
                      ) -> tuple[dict[str, str], dict[str, bool]]:
    """يجمع الرسائل الفعّالة لبناء errors.txt:

      • messages — {key: النص المخزّن أو الافتراضي} لكل مفتاح قياسي.
      • enabled  — {key: bool} حالة التفعيل (المفتاح غير المخزّن =
        مفعّل بالنص الافتراضي).

    يُستخدم في مسار النشر/الحزمة (build_errors_txt) فلا يحتاج
    المستدعي معرفة تفاصيل التخزين."""
    stored = list_messages(tenant_id, router_id)
    defaults = default_messages()
    messages: dict[str, str] = {}
    enabled: dict[str, bool] = {}
    for e in ERROR_KEYS:
        row = stored.get(e.key)
        if row:
            messages[e.key] = row["message_ar"] or defaults[e.key]
            enabled[e.key] = row["enabled"]
        else:
            messages[e.key] = defaults[e.key]
            enabled[e.key] = True
    return messages, enabled


__all__ = [
    "GLOBAL_ROUTER_ID",
    "list_messages",
    "seed_defaults",
    "save_message",
    "reset_message",
    "reset_all",
    "resolved_messages",
]
