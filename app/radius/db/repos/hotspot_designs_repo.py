"""hotspot_designs_repo — persistence for R2 designer state.

One row per (tenant_id, nas_id). UPSERT semantics: if a row
already exists for this nas, we replace it. Tests verify the
unique constraint holds.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..connection import db, transaction


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def get_design(tenant_id: int, nas_id: int) -> dict[str, Any] | None:
    row = db().execute(
        "SELECT id, tenant_id, nas_id, template_slug, variables_json, "
        "       updated_at "
        "FROM hotspot_designs "
        "WHERE tenant_id=? AND nas_id=?",
        (int(tenant_id), int(nas_id)),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["variables"] = json.loads(out["variables_json"] or "{}")
    except (TypeError, ValueError):
        out["variables"] = {}
    return out


def save_design(
    tenant_id: int, nas_id: int, *,
    template_slug: str, variables: dict[str, str],
) -> None:
    """UPSERT a design. `variables` is JSON-serialized at the
    boundary so the caller doesn't have to remember the column
    is text; nothing else writes to this table."""
    payload = json.dumps(variables, ensure_ascii=False)
    with transaction() as c:
        # SQLite UPSERT — relies on the UNIQUE(tenant_id, nas_id)
        # index from the 036 migration.
        c.execute(
            "INSERT INTO hotspot_designs "
            "  (tenant_id, nas_id, template_slug, variables_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, nas_id) DO UPDATE SET "
            "  template_slug = excluded.template_slug, "
            "  variables_json = excluded.variables_json, "
            "  updated_at = excluded.updated_at",
            (int(tenant_id), int(nas_id), template_slug, payload, _now()),
        )


def delete_design(tenant_id: int, nas_id: int) -> None:
    with transaction() as c:
        c.execute(
            "DELETE FROM hotspot_designs "
            "WHERE tenant_id=? AND nas_id=?",
            (int(tenant_id), int(nas_id)),
        )


# ─── «قوالب محفوظة» — مكتبة مصغّرة لكل راوتر (migration 096) ────
# يحفظ المشغّل مجموعة المتغيّرات الحالية باسم ويعيد تطبيقها لاحقًا.
# UPSERT على (tenant_id, nas_id, name) فالحفظ بنفس الاسم يحدّث.


def list_presets(tenant_id: int, nas_id: int) -> list[dict[str, Any]]:
    rows = db().execute(
        "SELECT id, name, template_slug, variables_json, updated_at "
        "FROM hotspot_design_presets "
        "WHERE tenant_id=? AND nas_id=? "
        "ORDER BY updated_at DESC",
        (int(tenant_id), int(nas_id)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["variables"] = json.loads(d["variables_json"] or "{}")
        except (TypeError, ValueError):
            d["variables"] = {}
        out.append(d)
    return out


def get_preset(tenant_id: int, nas_id: int,
               preset_id: int) -> dict[str, Any] | None:
    row = db().execute(
        "SELECT id, name, template_slug, variables_json, updated_at "
        "FROM hotspot_design_presets "
        "WHERE tenant_id=? AND nas_id=? AND id=?",
        (int(tenant_id), int(nas_id), int(preset_id)),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["variables"] = json.loads(out["variables_json"] or "{}")
    except (TypeError, ValueError):
        out["variables"] = {}
    return out


def save_preset(
    tenant_id: int, nas_id: int, *,
    name: str, template_slug: str, variables: dict[str, str],
) -> None:
    payload = json.dumps(variables, ensure_ascii=False)
    with transaction() as c:
        c.execute(
            "INSERT INTO hotspot_design_presets "
            "  (tenant_id, nas_id, name, template_slug, "
            "   variables_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, nas_id, name) DO UPDATE SET "
            "  template_slug = excluded.template_slug, "
            "  variables_json = excluded.variables_json, "
            "  updated_at = excluded.updated_at",
            (int(tenant_id), int(nas_id), name, template_slug,
             payload, _now()),
        )


def delete_preset(tenant_id: int, nas_id: int, preset_id: int) -> None:
    with transaction() as c:
        c.execute(
            "DELETE FROM hotspot_design_presets "
            "WHERE tenant_id=? AND nas_id=? AND id=?",
            (int(tenant_id), int(nas_id), int(preset_id)),
        )


# ─── «تصاميم خاصة» مرفوعة — على مستوى المستأجر (migration 097) ──
# المدير يرفع HTML خاصًا به فيظهر في معرض التصاميم بجانب المكتبة
# المدمجة بصيغة slug = custom:<id>. UPSERT على (tenant_id, name)
# فالرفع بنفس الاسم يحدّث التصميم الموجود.


def list_custom_templates(
        tenant_id: int, *, with_html: bool = False,
) -> list[dict[str, Any]]:
    """قائمة التصاميم الخاصة للمستأجر — بلا عمود html افتراضيًا
    (قد يصل 2MB لكل صف) حتى لا تثقل صفحة المصمّم؛ المعاينة/النشر
    يجلبان الصف المطلوب وحده عبر get_custom_template."""
    cols = "id, name, updated_at" + (", html" if with_html else "")
    rows = db().execute(
        f"SELECT {cols} FROM hotspot_custom_templates "
        "WHERE tenant_id=? ORDER BY updated_at DESC",
        (int(tenant_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_custom_template(tenant_id: int,
                        template_id: int) -> dict[str, Any] | None:
    row = db().execute(
        "SELECT id, name, html, updated_at "
        "FROM hotspot_custom_templates "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(template_id)),
    ).fetchone()
    return dict(row) if row else None


def save_custom_template(tenant_id: int, *, name: str, html: str) -> int:
    """UPSERT تصميم خاص باسمه — يعيد id الصف (الجديد أو المحدَّث)."""
    with transaction() as c:
        c.execute(
            "INSERT INTO hotspot_custom_templates "
            "  (tenant_id, name, html, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, name) DO UPDATE SET "
            "  html = excluded.html, "
            "  updated_at = excluded.updated_at",
            (int(tenant_id), name, html, _now()),
        )
    row = db().execute(
        "SELECT id FROM hotspot_custom_templates "
        "WHERE tenant_id=? AND name=?",
        (int(tenant_id), name),
    ).fetchone()
    return int(row["id"]) if row else 0


def delete_custom_template(tenant_id: int, template_id: int) -> None:
    with transaction() as c:
        c.execute(
            "DELETE FROM hotspot_custom_templates "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(template_id)),
        )


__all__ = [
    "get_design", "save_design", "delete_design",
    "list_presets", "get_preset", "save_preset", "delete_preset",
    "list_custom_templates", "get_custom_template",
    "save_custom_template", "delete_custom_template",
]
