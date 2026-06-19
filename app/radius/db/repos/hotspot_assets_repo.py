# -*- coding: utf-8 -*-
"""hotspot_assets_repo — أصول يرفعها المشغّل من المصمّم (فيديو/خط).

تُخزَّن BLOB محدودة الحجم لكل (مستأجر، راوتر، اسم ملف)، وتُرفع للراوتر
عند النشر فتعمل ذاتيًّا بلا أي walled-garden (مستضافة على الراوتر).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..connection import db, transaction

# حدود الحجم — سخيّة لكنها تمنع تضخيم قاعدة البيانات/زمن الرفع.
MAX_VIDEO_BYTES = 12 * 1024 * 1024   # 12 MB
MAX_FONT_BYTES = 2 * 1024 * 1024     # 2 MB
KINDS = {"video", "font"}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def max_bytes(kind: str) -> int:
    return MAX_VIDEO_BYTES if kind == "video" else MAX_FONT_BYTES


def save_asset(tenant_id: int, *, nas_id: int, kind: str, filename: str,
               content: bytes, content_type: str = "") -> None:
    """UPSERT أصل على (tenant, nas, filename). يرمي ValueError عند نوع
    مجهول أو تجاوز الحجم."""
    if kind not in KINDS:
        raise ValueError("نوع أصل غير مدعوم.")
    if not content:
        raise ValueError("الملف فارغ.")
    if len(content) > max_bytes(kind):
        raise ValueError(
            f"حجم الملف يتجاوز الحدّ ({max_bytes(kind) // (1024 * 1024)}م.ب).")
    with transaction() as c:
        c.execute(
            "INSERT INTO hotspot_assets "
            "(tenant_id, nas_id, kind, filename, content_type, size_bytes, "
            " content, updated_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, nas_id, filename) DO UPDATE SET "
            "  kind=excluded.kind, content_type=excluded.content_type, "
            "  size_bytes=excluded.size_bytes, content=excluded.content, "
            "  updated_at=excluded.updated_at",
            (int(tenant_id), int(nas_id), kind, filename, content_type,
             len(content), content, _now()))


def list_assets(tenant_id: int, nas_id: int) -> list[dict[str, Any]]:
    """أصول الراوتر بلا العمود BLOB (للعرض في المصمّم)."""
    rows = db().execute(
        "SELECT id, kind, filename, content_type, size_bytes, updated_at "
        "FROM hotspot_assets WHERE tenant_id=? AND nas_id=? "
        "ORDER BY kind, filename",
        (int(tenant_id), int(nas_id))).fetchall()
    return [dict(r) for r in rows]


def get_asset(tenant_id: int, nas_id: int, filename: str) -> dict[str, Any] | None:
    row = db().execute(
        "SELECT id, kind, filename, content_type, size_bytes, content "
        "FROM hotspot_assets WHERE tenant_id=? AND nas_id=? AND filename=?",
        (int(tenant_id), int(nas_id), filename)).fetchone()
    if not row:
        return None
    out = dict(row)
    c = out.get("content")
    out["content"] = bytes(c) if c is not None else b""
    return out


def delete_asset(tenant_id: int, nas_id: int, asset_id: int) -> None:
    with transaction() as c:
        c.execute("DELETE FROM hotspot_assets "
                  "WHERE tenant_id=? AND nas_id=? AND id=?",
                  (int(tenant_id), int(nas_id), int(asset_id)))


def brand_font_filename(tenant_id: int, nas_id: int) -> str:
    """اسم ملف خط العلامة المرفوع لهذا الراوتر (إن وُجد) — يستعمله
    منتقي الخطوط لحقن @font-face نسبيّ. '' إن لا خط."""
    row = db().execute(
        "SELECT filename FROM hotspot_assets "
        "WHERE tenant_id=? AND nas_id=? AND kind='font' "
        "ORDER BY updated_at DESC LIMIT 1",
        (int(tenant_id), int(nas_id))).fetchone()
    return row["filename"] if row else ""


__all__ = [
    "MAX_VIDEO_BYTES", "MAX_FONT_BYTES", "KINDS", "max_bytes",
    "save_asset", "list_assets", "get_asset", "delete_asset",
    "brand_font_filename",
]
