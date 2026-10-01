"""Web UI for soft-deleted operational records."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..db.connection import db
from ..db.helpers import row_to_dict
from ..db.repos import admins_repo, cards_repo, nas_repo, plans_repo, subscribers_repo
from ..services.lifecycle import recycle_display, retention_status


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
    bp.add_url_rule(
        "/recycle-bin/<entity_type>/<int:entity_id>/purge",
        "recycle_bin_purge",
        recycle_bin_purge,
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
    status = row.get("status") or ("enabled" if row.get("enabled") else "disabled")
    shown = recycle_display(table, row, status)
    return {
        "entity_type": _table_to_entity(table),
        "table": table,
        "id": row.get("id"),
        "label": shown["label"] or _label(row),
        "status": status,
        "status_label": shown["status_label"],
        "deleted_at": row.get("deleted_at"),
        "deleted_by": row.get("deleted_by") or "",
        "deleted_by_label": shown["deleted_by_label"],
        "delete_reason": shown["delete_reason"],
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


def _viewer() -> tuple[bool, list, object]:
    """(owner-like?, permissions, admin id) of the session admin."""
    return (bool(session.get("is_super_admin")), list(session.get("permissions") or []),
            session.get("admin_id"))


def _scope_clause(table: str) -> tuple[str, list]:
    """fix3 (F01 F4): a manager lists only HIS archived subscribers / batches."""
    is_owner, _perms, aid = _viewer()
    if is_owner or not aid:
        return "", []
    if table == "subscribers":
        from ..services.subscriber_scope import scope_admin_id, owner_scope_clause
        scope = scope_admin_id(int(aid), tenant_id=_tid())
        return owner_scope_clause(scope, tenant_id=_tid()) if scope is not None else ("", [])
    if table == "card_batches":
        from ..services.card_batch_scope import batch_scope_admin_id, batch_scope_clause
        scope = batch_scope_admin_id(int(aid), tenant_id=_tid())
        if scope is None:
            return "", []
        clause, vals = batch_scope_clause(scope, alias="card_batches")
        return " AND " + clause, vals
    return "", []


def _visible_entities() -> list[str]:
    from ..services.recycle_restore_policy import table_visible
    is_owner, perms, _aid = _viewer()
    return [e for e, t in _ENTITY_TABLES.items()
            if table_visible(t, is_owner=is_owner, perms=perms)]


def _deleted_rows(table: str, *, limit: int = 250) -> list[dict]:
    tenant_tables = {"subscribers", "access_plans", "nas_devices", "card_batches"}
    if table in tenant_tables:
        clause, cvals = _scope_clause(table)
        rows = db().execute(
            f"SELECT * FROM {table} WHERE tenant_id = ? AND deleted_at IS NOT NULL"
            + clause + " ORDER BY deleted_at DESC LIMIT ?",
            (_tid(), *cvals, limit),
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
    # fix3 (F01 F4): only the entity types this admin may restore, scoped.
    visible = _visible_entities()
    entities = [selected] if selected in visible else visible
    items: list[dict] = []
    for entity in entities:
        items.extend(_deleted_rows(_ENTITY_TABLES[entity]))
    items.sort(key=lambda item: item.get("deleted_at") or "", reverse=True)
    counts = {
        entity: len(_deleted_rows(table, limit=1000))
        for entity, table in _ENTITY_TABLES.items() if entity in visible
    }
    return render_template(
        "radius/recycle_bin.html",
        items=items,
        selected=selected,
        entity_labels={e: l for e, l in _ENTITY_LABELS.items() if e in visible},
        counts=counts,
    )


def recycle_bin_restore(entity_type: str, entity_id: int):
    if entity_type not in _ENTITY_TABLES:
        flash("نوع العنصر غير مدعوم في سلة المحذوفات.", "error")
        return redirect(url_for("radius.recycle_bin"))
    # fix3 (F01 F4): each entity type needs its own key (cards.restore is for
    # card batches only), admins/roles are owner-only, and the scope applies.
    from ..services.recycle_restore_policy import restore_denial
    is_owner, perms, aid = _viewer()
    denied = restore_denial(_ENTITY_TABLES[entity_type], entity_id, admin_id=aid,
                            is_owner=is_owner, perms=perms, tenant_id=_tid())
    if denied is not None:
        from flask import abort
        from .blueprint import _deny
        _deny(403, reason=denied["reason"] if denied["reason"] != "unsupported" else "permission",
              permission=denied["permission"])
        abort(403)
    try:
        restored = _restore(entity_type, entity_id)
    except RadiusError as exc:
        # f06-H1/H2: عنوان الراوتر صار لراوترٍ حيٍّ آخر، أو لا يقرؤه الرديوس.
        flash(exc.message, "error")
        return redirect(url_for("radius.recycle_bin", entity_type=entity_type))
    if restored:
        if entity_type == "nas":
            flash("تمت استعادة الراوتر معطّلًا — راجع عنوانه وكلمة سرّ الرديوس "
                  "ثم فعّله من صفحة الأجهزة.", "success")
        else:
            flash("تمت استعادة العنصر. راجعه قبل إعادة استخدامه تشغيليًا.", "success")
    else:
        flash("تعذرت الاستعادة: العنصر غير موجود أو لم يعد مؤرشفًا.", "error")
    return redirect(url_for("radius.recycle_bin", entity_type=entity_type))


def recycle_bin_purge(entity_type: str, entity_id: int):
    """PERMANENT delete («حذف نهائيّ») of an item already in the recycle bin —
    physically erases it and its footprint, unrecoverable. Currently supported
    for card batches (e.g. after a leak/theft the batch must be erased). The
    two-step flow (soft delete → purge) is deliberate for an irreversible op."""
    if entity_type != "card_batches":
        flash("الحذف النهائيّ مدعوم حاليًّا لحزم البطاقات فقط.", "error")
        return redirect(url_for("radius.recycle_bin", entity_type=entity_type))
    # only purge an item that is actually in the bin (soft-deleted first)
    row = db().execute(
        "SELECT id FROM card_batches WHERE tenant_id = ? AND id = ? "
        "AND deleted_at IS NOT NULL",
        (_tid(), entity_id),
    ).fetchone()
    if not row:
        flash("تعذّر الحذف النهائيّ: احذف الحزمة أوّلًا (تظهر في السلّة) ثمّ احذفها نهائيًّا.", "error")
        return redirect(url_for("radius.recycle_bin", entity_type=entity_type))
    summary = cards_repo.purge_batch(_tid(), entity_id)
    flash(
        "تمّ الحذف النهائيّ بلا رجعة — "
        f"بطاقات: {summary.get('cards', 0)} · حزمة: {summary.get('batch', 0)}.",
        "success",
    )
    return redirect(url_for("radius.recycle_bin", entity_type=entity_type))
