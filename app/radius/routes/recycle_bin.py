"""Web UI for soft-deleted operational records."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..db.connection import db
from ..db.helpers import row_to_dict
from ..db.repos import admins_repo, cards_repo, nas_repo, plans_repo, subscribers_repo
from ..services.lifecycle import retention_status


_ENTITY_TABLES = {
    "subscribers": "subscribers",
    "plans": "access_plans",
    "nas": "nas_devices",
    "admins": "admins",
    "roles": "roles",
    "card_batches": "card_batches",
}

_ENTITY_LABELS = {
    "subscribers": "المستفيدون",
    "plans": "الباقات",
    "nas": "أجهزة الشبكة",
    "admins": "المدراء",
    "roles": "الأدوار",
    "card_batches": "حزم البطاقات",
}


def register_recycle_bin_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/recycle-bin", "recycle_bin", recycle_bin, methods=["GET"])
    bp.add_url_rule(
        "/recycle-bin/<entity_type>/<int:entity_id>/restore",
        "recycle_bin_restore",
        recycle_bin_restore,
        methods=["POST"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or 1))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _label(row: dict) -> str:
    return (
        row.get("username")
        or row.get("name")
        or row.get("batch_code")
        or row.get("display_name")
        or str(row.get("id"))
    )


def _serialize(table: str, row: dict) -> dict:
    retention = retention_status(row)
    return {
        "entity_type": _table_to_entity(table),
        "table": table,
        "id": row.get("id"),
        "label": _label(row),
        "status": row.get("status") or ("enabled" if row.get("enabled") else "disabled"),
        "deleted_at": row.get("deleted_at"),
        "deleted_by": row.get("deleted_by") or "",
        "delete_reason": row.get("delete_reason") or "",
        "archive_source": row.get("archive_source") or ("manual" if row.get("deleted_at") else ""),
        "archive_policy_id": row.get("archive_policy_id"),
        "retention_expires_at": row.get("retention_expires_at"),
        "restore_allowed": retention["restore_allowed"],
        "retention_expired": retention["retention_expired"],
    }


def _table_to_entity(table: str) -> str:
    for entity, mapped in _ENTITY_TABLES.items():
        if mapped == table:
            return entity
    return table


def _deleted_rows(table: str, *, limit: int = 250) -> list[dict]:
    tenant_tables = {"subscribers", "access_plans", "nas_devices", "card_batches"}
    if table in tenant_tables:
        rows = db().execute(
            f"SELECT * FROM {table} WHERE tenant_id = ? AND deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT ?",
            (_tid(), limit),
        ).fetchall()
    elif table == "roles":
        rows = db().execute(
            "SELECT * FROM roles WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM admins WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_serialize(table, row_to_dict(row)) for row in rows]


def _restore_subscriber(entity_id: int) -> bool:
    row = db().execute(
        "SELECT username FROM subscribers WHERE tenant_id = ? AND id = ? "
        "AND deleted_at IS NOT NULL",
        (_tid(), entity_id),
    ).fetchone()
    if not row:
        return False
    return subscribers_repo.restore_subscriber(_tid(), row["username"], actor=_actor())


def _restore(entity_type: str, entity_id: int) -> bool:
    if entity_type == "subscribers":
        return _restore_subscriber(entity_id)
    if entity_type == "plans":
        return plans_repo.restore_plan(_tid(), entity_id, actor=_actor())
    if entity_type == "nas":
        return nas_repo.restore_nas(_tid(), entity_id, actor=_actor())
    if entity_type == "admins":
        return admins_repo.restore_admin(entity_id, actor=_actor())
    if entity_type == "roles":
        return admins_repo.restore_role(entity_id, actor=_actor())
    if entity_type == "card_batches":
        return cards_repo.restore_batch(_tid(), entity_id, actor=_actor())
    return False


def recycle_bin():
    selected = (request.args.get("entity_type") or "").strip()
    entities = [selected] if selected in _ENTITY_TABLES else list(_ENTITY_TABLES)
    items: list[dict] = []
    for entity in entities:
        items.extend(_deleted_rows(_ENTITY_TABLES[entity]))
    items.sort(key=lambda item: item.get("deleted_at") or "", reverse=True)
    counts = {
        entity: len(_deleted_rows(table, limit=1000))
        for entity, table in _ENTITY_TABLES.items()
    }
    return render_template(
        "radius/recycle_bin.html",
        items=items,
        selected=selected,
        entity_labels=_ENTITY_LABELS,
        counts=counts,
    )


def recycle_bin_restore(entity_type: str, entity_id: int):
    if entity_type not in _ENTITY_TABLES:
        flash("نوع العنصر غير مدعوم في سلة المحذوفات.", "error")
        return redirect(url_for("radius.recycle_bin"))
    if _restore(entity_type, entity_id):
        flash("تمت استعادة العنصر. راجعه قبل إعادة استخدامه تشغيليًا.", "success")
    else:
        flash("تعذرت الاستعادة: العنصر غير موجود أو لم يعد مؤرشفًا.", "error")
    return redirect(url_for("radius.recycle_bin", entity_type=entity_type))
