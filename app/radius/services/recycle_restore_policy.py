"""fix3 (F01 F4) — who may restore WHAT from the recycle bin (web + API).

``cards.restore`` was the only gate of the generic restore, so a card operator
un-archived deleted admins, routers, plans and other managers' subscribers.
Now each entity type needs its own key, and the manager's scope applies:

* card batches  → ``cards.restore`` + the batch is in his card-batch scope;
* subscribers   → ``users.create`` + the subscriber is in his scope;
* plans         → ``plans.create``;  * NAS/routers → ``nas.create``;
* admins, roles → owner / co-owner only.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

from typing import Iterable, Optional

RESTORE_PERMISSION = {
    "card_batches": "cards.restore",
    "subscribers": "users.create",
    "access_plans": "plans.create",
    "nas_devices": "nas.create",
}
OWNER_ONLY_TABLES = frozenset({"admins", "roles"})

_OWNER_ONLY_AR = N_("استعادة المدراء والأدوار مقصورة على المالك أو الشريك.")


def table_visible(table: str, *, is_owner: bool, perms: Iterable[str]) -> bool:
    """Should this entity type be listed to the viewer at all?"""
    if is_owner:
        return True
    if table in OWNER_ONLY_TABLES:
        return False
    need = RESTORE_PERMISSION.get(table)
    return bool(need and need in set(perms or ()))


def restore_denial(table: str, entity_id: int, *, admin_id: Optional[int],
                   is_owner: bool, perms: Iterable[str],
                   tenant_id: int = 1) -> Optional[dict]:
    """None = allowed; else ``{"message": Arabic, "permission": key, "reason": …}``."""
    if is_owner:
        return None
    if table in OWNER_ONLY_TABLES:
        return {"message": _OWNER_ONLY_AR, "permission": "", "reason": "owner_only"}
    need = RESTORE_PERMISSION.get(table)
    if not need:
        return {"message": _tr("نوع العنصر غير مدعوم في سلة المحذوفات."), "permission": "",
                "reason": "unsupported"}
    if need not in set(perms or ()):
        try:
            from .permission_labels import permission_label
            label = permission_label(need)
        except Exception:  # noqa: BLE001
            label = need
        return {"message": _tr('تنقصك الصلاحية: %(label)s — لازمة لاستعادة هذا النوع.', label=label),
                "permission": need, "reason": "permission"}
    if table == "subscribers":
        from .subscriber_scope import OUT_OF_SCOPE_AR, subscriber_accessible
        if not subscriber_accessible(admin_id, subscriber_id=int(entity_id),
                                     tenant_id=tenant_id):
            return {"message": OUT_OF_SCOPE_AR, "permission": "", "reason": "out_of_scope"}
    if table == "card_batches":
        from .card_batch_scope import OUT_OF_SCOPE_BATCH_AR, batch_accessible
        if not batch_accessible(int(entity_id), admin_id, tenant_id=tenant_id):
            return {"message": OUT_OF_SCOPE_BATCH_AR, "permission": "",
                    "reason": "out_of_scope"}
    return None


__all__ = ["RESTORE_PERMISSION", "OWNER_ONLY_TABLES", "table_visible", "restore_denial"]
