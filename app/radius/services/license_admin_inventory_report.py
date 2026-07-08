"""Admin-inventory reporter (admins-report v2, additive/backward-compatible).

Sends the local admin roster to the license panel so the panel can decide which
admins may be super AND prune admins deleted on the instance from its «managed
admins» view. The panel returns its decisions as ``admin_super_overrides`` in
the identity-sync response (consumed by :mod:`license_admin_identity_sync`).

Two report shapes:

* ``report_once(full_snapshot=True)`` — the DEFAULT and the fix for the
  «deleted-still-shows» bug. Sends the COMPLETE current admin roster with
  ``full_snapshot: true`` so the panel prunes any admin whose id is absent.
* ``report_tombstone(admin_id=...)`` — differential single-delete signal for
  precise revoke UX right after a local delete. Sends the primary admin +
  a tombstone row ``{"id": N, "deleted": true}`` with ``full_snapshot: false``.

Invariants (enforced here + at the transport in
:meth:`AdminPanelClient.post_admins_report`):

* Only non-secret identity fields are sent — never a password hash.
* The PRIMARY/LOCAL admin is ALWAYS included in every report — the panel
  otherwise has no visibility of the local owner and can wrongly consider it
  deleted.
* Never send an empty ``admins: []`` (would delete every admin from the panel).
* Cap at 200 admins per report (contract limit).
"""
from __future__ import annotations

from typing import Any, Iterable

from app.radius.db.repos import admins_repo
from app.radius.services.admin_panel_client import (
    AdminBridgeConfig,
    AdminPanelClient,
)

# Exactly the fields the panel contract expects (no password material).
# ``is_primary`` was added in admins-report v2 so the panel can mark the local
# owner clearly in its UI without guessing by min-id.
_REPORT_FIELDS = (
    "id",
    "username",
    "role",
    "is_super_admin",
    "is_primary",
    "enabled",
    "managed_by_license_admin",
    "external_identity_provider",
)

# Cap per the contract.
_MAX_ADMINS_PER_REPORT = 200


def _role_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for role in admins_repo.list_roles(include_deleted=True):
        names[role.id] = role.name
    return names


def _admin_to_report_row(admin, *, primary_id: int | None,
                        role_names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": int(admin.id),
        "username": str(admin.username or ""),
        "role": role_names.get(admin.role_id, "") if admin.role_id else "",
        "is_super_admin": bool(admin.is_super_admin),
        # ``is_primary`` = this row is THE primary/local owner (min-id
        # convention). Sent so the panel can mark the local owner in its UI
        # and refuse operations that would revoke it.
        "is_primary": bool(primary_id is not None and int(admin.id) == int(primary_id)),
        "enabled": bool(admin.enabled),
        "managed_by_license_admin": bool(admin.managed_by_license_admin),
        "external_identity_provider": admin.external_identity_provider or "",
    }


def build_admin_inventory() -> list[dict[str, Any]]:
    """Return the local admin roster as plain dicts for the report payload.

    Rows are ordered by id ASC (stable), so the primary/local admin (min-id) is
    always the FIRST entry. Truncates at :data:`_MAX_ADMINS_PER_REPORT`, but
    the primary admin is guaranteed to be in the truncated slice because it
    lives at index 0.
    """
    primary_id = admins_repo.primary_admin_id()
    names = _role_names()
    inventory: list[dict[str, Any]] = []
    for admin in admins_repo.list_admins():
        inventory.append(_admin_to_report_row(
            admin, primary_id=primary_id, role_names=names,
        ))
        if len(inventory) >= _MAX_ADMINS_PER_REPORT:
            break
    return inventory


def build_tombstone_report(deleted_admin_id: int) -> list[dict[str, Any]]:
    """Build a differential report for a single deletion.

    Includes the PRIMARY admin (invariant — every report must carry the local
    owner so the panel can't wrongly prune it), followed by a tombstone row
    ``{"id": N, "deleted": true}`` for the deleted admin. If ``deleted_admin_id``
    turns out to still exist (e.g. the caller was mistaken), the tombstone is
    still sent — the panel takes it as an authoritative delete signal.
    """
    primary_id = admins_repo.primary_admin_id()
    names = _role_names()
    rows: list[dict[str, Any]] = []
    # The primary row keeps the panel's view of the local owner intact.
    if primary_id is not None:
        primary = admins_repo.get_admin(int(primary_id))
        if primary is not None:
            rows.append(_admin_to_report_row(
                primary, primary_id=primary_id, role_names=names,
            ))
    rows.append({"id": int(deleted_admin_id), "deleted": True})
    return rows


class LicenseAdminInventoryReportService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)

    def report_once(self, *, tenant_id: int = 1,
                    full_snapshot: bool = True) -> dict[str, Any]:
        """Send the CURRENT full admin roster.

        With ``full_snapshot=True`` (default) the panel prunes any admin whose
        id is absent — this is the main fix for the «deleted-still-shows»
        symptom. Callers that want the periodic/kept-in-sync cadence use the
        default. The False variant is exposed only for legacy differential
        reporters.
        """
        admins = build_admin_inventory()
        if not admins:
            # Nothing local — refuse to send. The producer contract forbids
            # empty admins (see AdminPanelClient.post_admins_report).
            return {
                "ok": False,
                "status": "empty_admins",
                "error": {"code": "empty_admins",
                          "message": "لا يوجد مدراء محلّيّون — لن يُرسَل التقرير."},
                "reported_count": 0,
            }
        result = self.admin_client.post_admins_report(
            admins=admins, full_snapshot=full_snapshot,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "status": result.get("status") or "unavailable",
                "error": result.get("error") or {},
                "reported_count": len(admins),
                "full_snapshot": bool(full_snapshot),
            }
        return {
            "ok": True,
            "status": result.get("status") or "ok",
            "reported_count": len(admins),
            "full_snapshot": bool(full_snapshot),
            "response": result.get("response") or {},
        }

    def report_tombstone(self, *, deleted_admin_id: int,
                         tenant_id: int = 1) -> dict[str, Any]:
        """Send a differential deletion signal for a single admin.

        The report includes the primary admin (safety) + one tombstone row.
        Use this immediately after a local delete for a precise revoke; the
        next full-snapshot ``report_once`` reconciles anyway.
        """
        rows = build_tombstone_report(int(deleted_admin_id))
        result = self.admin_client.post_admins_report(
            admins=rows, full_snapshot=False,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "status": result.get("status") or "unavailable",
                "error": result.get("error") or {},
                "reported_count": len(rows),
                "tombstone": int(deleted_admin_id),
                "full_snapshot": False,
            }
        return {
            "ok": True,
            "status": result.get("status") or "ok",
            "reported_count": len(rows),
            "tombstone": int(deleted_admin_id),
            "full_snapshot": False,
            "response": result.get("response") or {},
        }


def report_admins_best_effort(*, deleted_admin_id: int | None = None,
                              full_snapshot: bool = True) -> dict[str, Any]:
    """Best-effort convenience wrapper for route handlers.

    Never raises; swallows and returns the failure dict. Route handlers call
    this from add/edit/deactivate/delete write-sites so a bridge outage never
    breaks a local admin write. When ``deleted_admin_id`` is given, sends a
    tombstone; otherwise sends the full snapshot.
    """
    try:
        svc = LicenseAdminInventoryReportService()
        if deleted_admin_id is not None:
            return svc.report_tombstone(deleted_admin_id=int(deleted_admin_id))
        return svc.report_once(full_snapshot=bool(full_snapshot))
    except Exception as exc:  # noqa: BLE001 — best-effort telemetry only
        return {"ok": False, "status": "exception", "error": {"message": str(exc)}}
