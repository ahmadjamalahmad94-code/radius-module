"""قوالب الصلاحيات (F2) — المالك يَحفظ حزمة منوحات مسمّاة ويُطبّقها على أيّ
مدير بضغطة. القالب لقطةٌ من أعمدة المنوحات الخمسة لسياسة المدير
(permissions/limits/section_access/action_grants/field_grants). التطبيق يَكتب
هذه الأعمدة على صفّ سياسة المدير الهدف (يَستبدل منوحاته) — ثم يُمكن للمالك
التعديل يدويًّا بعدها.

يَبني على [[manager-granular-grants]] — يُعيد استخدام صفّ manager_distributor_policies
وأدوات manager_grants (لا نظام موازٍ).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..db.connection import db
from ..db.helpers import now_iso, row_to_dict

# أعمدة المنوحات التي يَلتقطها/يُطبّقها القالب.
_GRANT_COLS = (
    "permissions_json", "limits_json", "section_access_json",
    "action_grants_json", "field_grants_json",
)


class ManagerPresetError(ValueError):
    """خطأ تحقّق آمن لعمليّات القوالب."""


def _tid(tenant_id: int) -> int:
    return int(tenant_id or 1)


def list_presets(tenant_id: int = 1) -> list[dict[str, Any]]:
    rows = db().execute(
        "SELECT id, name, created_at, updated_at FROM manager_permission_presets "
        "WHERE tenant_id=? ORDER BY name",
        (_tid(tenant_id),),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_preset(preset_id: int, *, tenant_id: int = 1) -> dict[str, Any]:
    row = db().execute(
        "SELECT * FROM manager_permission_presets WHERE tenant_id=? AND id=?",
        (_tid(tenant_id), int(preset_id)),
    ).fetchone()
    if not row:
        raise ManagerPresetError("القالب غير موجود.")
    return row_to_dict(row)


def _manager_grant_cols(manager_id: int, tenant_id: int) -> dict[str, str]:
    """أعمدة منوحات المدير الحاليّة (JSON نصّيّ) — أو افتراضات فارغة."""
    row = db().execute(
        f"SELECT {', '.join(_GRANT_COLS)} FROM manager_distributor_policies "
        "WHERE tenant_id=? AND entity_type='manager' AND entity_id=?",
        (_tid(tenant_id), int(manager_id)),
    ).fetchone()
    if not row:
        return {c: "{}" for c in _GRANT_COLS}
    return {c: (row[c] or "{}") for c in _GRANT_COLS}


def create_preset(name: str, *, tenant_id: int = 1, source_manager_id: Optional[int] = None,
                  by: int = 0) -> dict[str, Any]:
    """يُنشئ قالبًا. إن مُرِّر ``source_manager_id`` يَلتقط منوحاته الحاليّة،
    وإلّا يُنشئ قالبًا فارغًا (أساس مقيَّد)."""
    clean = (name or "").strip()
    if not clean:
        raise ManagerPresetError("اسم القالب مطلوب.")
    exists = db().execute(
        "SELECT 1 FROM manager_permission_presets WHERE tenant_id=? AND name=?",
        (_tid(tenant_id), clean),
    ).fetchone()
    if exists:
        raise ManagerPresetError("يوجد قالبٌ بهذا الاسم.")
    cols = (_manager_grant_cols(source_manager_id, tenant_id)
            if source_manager_id else {c: "{}" for c in _GRANT_COLS})
    now = now_iso()
    cur = db().execute(
        f"""INSERT INTO manager_permission_presets
            (tenant_id, name, {', '.join(_GRANT_COLS)}, created_by, created_at, updated_at)
            VALUES (?,?,{','.join('?' for _ in _GRANT_COLS)},?,?,?)""",
        (_tid(tenant_id), clean, *[cols[c] for c in _GRANT_COLS], int(by), now, now),
    )
    return get_preset(int(cur.lastrowid), tenant_id=tenant_id)


def rename_preset(preset_id: int, name: str, *, tenant_id: int = 1) -> dict[str, Any]:
    clean = (name or "").strip()
    if not clean:
        raise ManagerPresetError("اسم القالب مطلوب.")
    get_preset(preset_id, tenant_id=tenant_id)  # existence
    db().execute(
        "UPDATE manager_permission_presets SET name=?, updated_at=? WHERE tenant_id=? AND id=?",
        (clean, now_iso(), _tid(tenant_id), int(preset_id)),
    )
    return get_preset(preset_id, tenant_id=tenant_id)


def delete_preset(preset_id: int, *, tenant_id: int = 1) -> None:
    db().execute(
        "DELETE FROM manager_permission_presets WHERE tenant_id=? AND id=?",
        (_tid(tenant_id), int(preset_id)),
    )


def apply_preset(preset_id: int, manager_id: int, *, tenant_id: int = 1) -> None:
    """يُطبّق القالب على مدير: يَكتب أعمدة المنوحات الخمسة على صفّ سياسته
    (يَستبدل منوحاته). يُنشئ الصفّ إن لم يكن موجودًا."""
    preset = get_preset(preset_id, tenant_id=tenant_id)
    # اضمن وجود صفّ السياسة (يُنشأ بالافتراضات إن غاب، دون مسّ أعمدة أخرى).
    from .manager_grants import _ensure_policy_row, _invalidate_cache
    _ensure_policy_row(int(manager_id), _tid(tenant_id))
    db().execute(
        f"""UPDATE manager_distributor_policies
            SET {', '.join(f'{c}=?' for c in _GRANT_COLS)}, updated_at=?
            WHERE tenant_id=? AND entity_type='manager' AND entity_id=?""",
        (*[json.dumps(_load(preset.get(c)), ensure_ascii=False) for c in _GRANT_COLS],
         now_iso(), _tid(tenant_id), int(manager_id)),
    )
    _invalidate_cache()


def _load(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


__all__ = [
    "ManagerPresetError", "list_presets", "get_preset", "create_preset",
    "rename_preset", "delete_preset", "apply_preset",
]
