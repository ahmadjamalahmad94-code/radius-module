"""NPC Safe-Execution Phase 5 — rollback engine + route."""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_se5_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed_apply_change_set(
    app, *, router_id, succeeded=True, rollback_script=None,
):
    """Create a change_set with one successful per-router
    target that has a stored rollback script ready for the
    rollback service to consume."""
    from app.radius.db.repos import (
        npc_change_sets_repo as cs,
    )
    rb = rollback_script or (
        "/ip/firewall/filter remove "
        "[find comment~\"^HOBE_NPC_BLOCK:7:\"]\n"
        "/ip/firewall/address-list remove "
        "[find comment~\"^HOBE_NPC_BLOCK:7:\"]\n"
    )
    with app.app_context():
        cs_id = cs.create(
            tenant_id=1, service="web_block", policy_id=7,
            action_type=cs.ACTION_APPLY,
            execution_mode=cs.MODE_FULL,
            snapshot_id=42,
        )
        cs.update_status(
            1, cs_id,
            status=(
                cs.STATUS_SUCCEEDED if succeeded
                else cs.STATUS_FAILED
            ),
        )
        target_id = cs.add_target(
            change_set_id=cs_id, tenant_id=1,
            router_id=int(router_id),
            rendered_script="# applied\n",
            rollback_script=rb,
            status=(
                cs.TARGET_STATUS_SUCCEEDED if succeeded
                else cs.TARGET_STATUS_FAILED
            ),
        )
    return cs_id, target_id


# ─── Service-layer ──────────────────────────────────────────


def test_rollback_managed_prefix_succeeds_with_fake_executor(app):
    cs_id, _ = _seed_apply_change_set(
        app, router_id=42, succeeded=True,
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    fake.program_success(42, for_rollback=True)
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
    assert out.ok
    assert out.status == "rolled_back"
    # The original change_set's status was mirrored.
    from app.radius.db.repos import (
        npc_change_sets_repo as cs,
    )
    with app.app_context():
        original = cs.get(1, cs_id)
    assert original["status"] == "rolled_back"
    # Executor saw the rollback call.
    assert any(c["kind"] == "rollback"
               and c["router_id"] == 42
               for c in fake.calls)


def test_rollback_refuses_unmanaged_remove_pattern(app):
    """Stored rollback script that targets a non-managed
    prefix MUST be refused — defence in depth even though the
    renderer/contracts engine already block it."""
    unsafe_rb = (
        "/ip/firewall/filter remove "
        "[find comment~\"random-unmanaged\"]\n"
    )
    cs_id, _ = _seed_apply_change_set(
        app, router_id=1, succeeded=True,
        rollback_script=unsafe_rb,
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
    assert not out.ok
    assert out.status == "rollback_failed"
    # Executor NEVER ran the bad script.
    assert not fake.calls


def test_rollback_empty_script_refused(app):
    cs_id, _ = _seed_apply_change_set(
        app, router_id=1, succeeded=True,
        rollback_script="   ",
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
    assert not out.ok


def test_rollback_no_remove_instruction_refused(app):
    """A rollback script with no `remove [find ...]` lines is
    suspicious — refuse rather than run a no-op that pretends
    to roll back."""
    cs_id, _ = _seed_apply_change_set(
        app, router_id=1, succeeded=True,
        rollback_script="/log info \"nothing\"\n",
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
    assert not out.ok
    assert out.status == "rollback_failed"
    assert not fake.calls


def test_rollback_partial_when_one_router_fails(app):
    """Two routers in the apply, one rollback succeeds, one
    fails → aggregate status `partially_rolled_back`."""
    from app.radius.db.repos import (
        npc_change_sets_repo as cs,
    )
    rb_script = (
        "/ip/firewall/filter remove "
        "[find comment~\"^HOBE_NPC_BLOCK:7:\"]\n"
    )
    with app.app_context():
        cs_id = cs.create(
            tenant_id=1, service="web_block", policy_id=7,
            action_type=cs.ACTION_APPLY,
            execution_mode=cs.MODE_FULL,
            snapshot_id=1,
        )
        cs.update_status(1, cs_id,
                          status=cs.STATUS_SUCCEEDED)
        for rid in (10, 11):
            cs.add_target(
                change_set_id=cs_id, tenant_id=1,
                router_id=rid,
                rendered_script="# applied\n",
                rollback_script=rb_script,
                status=cs.TARGET_STATUS_SUCCEEDED,
            )

    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    fake.program_success(10, for_rollback=True)
    fake.program_failure(11, for_rollback=True,
                          error="ssh timeout")
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
    assert out.status == "partially_rolled_back"
    statuses = {t.router_id: t.status for t in out.targets}
    assert statuses[10] == "rolled_back"
    assert statuses[11] == "failed"
    # Original change_set mirrored to partially_rolled_back.
    with app.app_context():
        assert cs.get(1, cs_id)["status"] == \
            "partially_rolled_back"


def test_rollback_audit_emitted(app):
    cs_id, _ = _seed_apply_change_set(
        app, router_id=42, succeeded=True,
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    fake.program_success(42, for_rollback=True)
    with app.app_context():
        rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
        from app.radius.db.connection import db
        actions = [r["action"] for r in db().execute(
            "SELECT action FROM audit_log "
            "WHERE target_type='npc_web_block_policy' "
            "ORDER BY id"
        ).fetchall()]
    assert "npc.web_block.rolled_back" in actions


def test_rollback_tenant_scoping(app):
    """A change_set in tenant 1 cannot be rolled back from
    tenant 2 — the service refuses with not_found."""
    cs_id, _ = _seed_apply_change_set(
        app, router_id=1, succeeded=True,
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=2,  # WRONG tenant
            service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
    assert not out.ok


def test_rollback_permission_required(app):
    cs_id, _ = _seed_apply_change_set(
        app, router_id=1, succeeded=True,
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="bob", actor_has_apply_perm=False,
            executor=fake,
        )
    assert not out.ok
    assert "صلاحية" in out.reason_ar
    assert fake.calls == []


def test_rollback_refuses_failed_original(app):
    """A failed apply has nothing to roll back."""
    cs_id, _ = _seed_apply_change_set(
        app, router_id=1, succeeded=False,
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    fake = exec_mod.FakeRouterExecutor()
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
    assert not out.ok


def test_rollback_creates_child_change_set(app):
    cs_id, _ = _seed_apply_change_set(
        app, router_id=42, succeeded=True,
    )
    from app.radius.services import (
        npc_rollback_service as rb,
        npc_router_executor as exec_mod,
    )
    from app.radius.db.repos import (
        npc_change_sets_repo as cs,
    )
    fake = exec_mod.FakeRouterExecutor()
    fake.program_success(42, for_rollback=True)
    with app.app_context():
        out = rb.request_rollback(
            tenant_id=1, service="web_block", policy_id=7,
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake,
        )
        child = cs.get(1, out.change_set_id)
    assert child["action_type"] == "rollback"
    assert int(child["parent_change_set_id"]) == cs_id


# ─── Route presence ─────────────────────────────────────────


def test_rollback_route_exists_post_only(app):
    with app.app_context():
        rules = [str(r) for r in app.url_map.iter_rules()
                 if "/rollback" in str(r)
                 and "/network-policy/" in str(r)]
    assert any("remote-access" in u for u in rules)
    assert any("web-block" in u for u in rules)
    assert any("walled-garden" in u for u in rules)


def test_rollback_get_method_not_allowed(app):
    with app.test_client() as c:
        r = c.get(
            "/admin/radius/network-policy/web-block/"
            "1/changes/1/rollback"
        )
        assert r.status_code == 405


# ─── No direct MikroTik client ──────────────────────────────


def test_rollback_service_does_not_import_mikrotik_client():
    import importlib
    import app.radius.services.npc_rollback_service as m
    importlib.reload(m)
    assert "MikrotikClient" not in dir(m)
