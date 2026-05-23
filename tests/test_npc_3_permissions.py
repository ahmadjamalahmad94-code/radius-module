"""NPC Phase 3 — permission catalogue extension."""
from __future__ import annotations

from types import SimpleNamespace


# ─── Catalogue pin ───────────────────────────────────────────


def test_npc_permissions_present_in_allowlist():
    """Every NPC permission is enumerated in ALL_PERMISSIONS
    so the matrix page (O11) renders them all."""
    from app.radius.services.mt_permissions import ALL_PERMISSIONS
    expected = {
        "npc.remote_access.view",
        "npc.remote_access.manage",
        "npc.remote_access.preview",
        "npc.remote_access.apply",
        "npc.web_block.view",
        "npc.web_block.manage",
        "npc.web_block.preview",
        "npc.web_block.apply",
        "npc.walled_garden.view",
        "npc.walled_garden.manage",
        "npc.walled_garden.preview",
        "npc.walled_garden.apply",
    }
    assert expected.issubset(set(ALL_PERMISSIONS))


def test_npc_permissions_exported_as_module_constants():
    """Route handlers import the constants by name; renaming
    these is a breaking change. Pin the surface."""
    from app.radius.services import mt_permissions as mp
    assert mp.PERM_NPC_REMOTE_ACCESS_VIEW    == "npc.remote_access.view"
    assert mp.PERM_NPC_REMOTE_ACCESS_MANAGE  == "npc.remote_access.manage"
    assert mp.PERM_NPC_REMOTE_ACCESS_PREVIEW == "npc.remote_access.preview"
    assert mp.PERM_NPC_REMOTE_ACCESS_APPLY   == "npc.remote_access.apply"
    assert mp.PERM_NPC_WEB_BLOCK_VIEW        == "npc.web_block.view"
    assert mp.PERM_NPC_WEB_BLOCK_MANAGE      == "npc.web_block.manage"
    assert mp.PERM_NPC_WEB_BLOCK_PREVIEW     == "npc.web_block.preview"
    assert mp.PERM_NPC_WEB_BLOCK_APPLY       == "npc.web_block.apply"
    assert mp.PERM_NPC_WALLED_GARDEN_VIEW    == "npc.walled_garden.view"
    assert mp.PERM_NPC_WALLED_GARDEN_MANAGE  == "npc.walled_garden.manage"
    assert mp.PERM_NPC_WALLED_GARDEN_PREVIEW == "npc.walled_garden.preview"
    assert mp.PERM_NPC_WALLED_GARDEN_APPLY   == "npc.walled_garden.apply"


# ─── PERM_ADMIN posture ──────────────────────────────────────


def test_super_admin_holds_every_npc_permission():
    from app.radius.services.mt_permissions import (
        ALL_PERMISSIONS, admin_permissions,
    )
    admin = SimpleNamespace(id=1, is_super_admin=True)
    held = admin_permissions(admin)
    npc_perms = {p for p in ALL_PERMISSIONS
                 if p.startswith("npc.")}
    assert npc_perms <= held
    # Plus the .apply ones (super admin == god mode).
    assert "npc.remote_access.apply" in held
    assert "npc.web_block.apply" in held
    assert "npc.walled_garden.apply" in held


def test_perm_admin_implies_view_manage_preview_not_apply(monkeypatch):
    """PERM_ADMIN must grant `.view`, `.manage`, `.preview` for
    every NPC sub-service, but NOT `.apply`. Apply is opt-in
    even for admins — destructive surface gated explicitly."""
    from app.radius.services import mt_permissions as mp
    admin = SimpleNamespace(id=42, is_super_admin=False)

    class _Svc:
        def permissions_of(self, _admin):
            return [mp.PERM_ADMIN]

    monkeypatch.setattr(
        "app.radius.services.admins.get_admins_service",
        lambda: _Svc(),
    )

    held = mp.admin_permissions(admin)
    for service in ("remote_access", "web_block",
                    "walled_garden"):
        assert f"npc.{service}.view" in held
        assert f"npc.{service}.manage" in held
        assert f"npc.{service}.preview" in held
        # Apply is the line.
        assert f"npc.{service}.apply" not in held


def test_explicit_apply_grant_works_independently(monkeypatch):
    """An admin given JUST `npc.web_block.apply` holds it —
    apply doesn't require PERM_ADMIN; it's an independent perm."""
    from app.radius.services import mt_permissions as mp
    admin = SimpleNamespace(id=7, is_super_admin=False)

    class _Svc:
        def permissions_of(self, _admin):
            return [mp.PERM_NPC_WEB_BLOCK_APPLY]

    monkeypatch.setattr(
        "app.radius.services.admins.get_admins_service",
        lambda: _Svc(),
    )

    held = mp.admin_permissions(admin)
    assert "npc.web_block.apply" in held
    # No spillover — apply alone doesn't grant view/manage.
    assert "npc.web_block.view" not in held
    assert "npc.web_block.manage" not in held


def test_has_rejects_unknown_perm_strings():
    """`has(admin, 'invented.string')` must return False even
    for a super-admin — the function is allow-listed."""
    from app.radius.services.mt_permissions import has
    admin = SimpleNamespace(id=1, is_super_admin=True)
    assert not has(admin, "npc.invented.permission")
    assert not has(admin, "")
