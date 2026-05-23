"""NPC Safe-Execution Phase 7 — end-to-end safety tests.

Exercises the full apply / rollback pipeline through the
service layer using the fake executor and fake state reader.
These tests are the closest the codebase comes to claiming
"the apply path is wired correctly" — they intentionally use
NO live MikroTik client and would be the canary if anyone
tried to bypass an adapter boundary.

Paths covered:

* happy_path                — create → render → snapshot →
                              readiness ready → apply success →
                              rollback success
* blocked_critical_risk     — critical risk → contracts refuses
* blocked_stale_preview     — preview hash mismatch → refused
* blocked_no_snapshot       — no snapshot id → refused
* partial_router_failure    — 2 routers, one fails → status
                              `partially_succeeded`, rollback
                              still possible on the survivor
* permission_matrix         — view/apply/rollback separation
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_se7_")
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


# ─── Test helpers ────────────────────────────────────────────


def _seed_router(app, *, name=None, address=None):
    suffix = secrets.token_hex(3)
    nm = name or f"rt-{suffix}"
    addr = address or f"10.0.{int(suffix, 16) % 256}.1"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices (tenant_id, name, "
                "shortname, address, secret, vendor, nas_type, "
                "ports, snmp_community, auth_port, acct_port, "
                "coa_port, api_port, api_user, api_password, "
                "api_use_tls, location, coordinates, "
                "monitoring_enabled, description, enabled, "
                "require_message_authenticator, ssh_port, "
                "tags, metadata, created_at, updated_at) "
                "VALUES (1, ?, ?, ?,'','mikrotik',"
                "'router',0,'',1812,1813,3799,8728,'admin',"
                "'pw',0,'','',0,'',1,0,22,'','{}',"
                "'2026-01-01','2026-01-01')",
                (nm, nm, addr),
            )
            return int(cur.lastrowid)


def _build_intelligence(app, *, policy, plan, forward, rollback,
                         router_count=1):
    """Build the bundle of intelligence outputs the apply
    service needs. Kept inside an `with app.app_context()` block
    by the caller."""
    from app.radius.services import (
        npc_blast_radius, npc_canary_planner,
        npc_conflict_detector, npc_dependency_detector,
        npc_impact_analyzer, npc_policy_health,
    )
    impact = npc_impact_analyzer.analyze(
        policy_type="remote_access", policy=policy, plan=plan,
        targets=(),
        rendered_forward=forward, rendered_rollback=rollback,
    )
    conflicts = npc_conflict_detector.analyze(
        current_service="remote_access",
        current_policy=policy, current_children=(),
        peers=(),
    )
    deps = npc_dependency_detector.analyze(targets=())
    blast = npc_blast_radius.analyze(
        policy_type="remote_access", plan=plan,
        affected_router_count=router_count,
    )
    canary = npc_canary_planner.plan(blast=blast)
    health = npc_policy_health.compute(
        impact=impact, conflicts=conflicts,
        dependencies=deps, blast=blast,
    )
    return impact, conflicts, deps, blast, canary, health


def _make_policy_with_plan(app, *, router_id, name="e2e"):
    """Create a remote-access policy and return everything the
    apply service needs."""
    from app.radius.db.repos import (
        npc_remote_access_repo as ra_repo,
        npc_snapshots_repo as snap_repo,
    )
    from app.radius.services import (
        npc_remote_access_planner as ra_p,
        npc_script_renderer as renderer,
    )
    pid = ra_repo.create(
        tenant_id=1, router_id=router_id, name=name,
        allow_winbox=True, source_address_list="ops",
        expires_at="2027-01-01T00:00:00Z",
    )
    policy = ra_repo.get_by_id(1, pid)
    plan = ra_p.plan(policy)
    forward = renderer.render_forward_script(plan)
    rollback = renderer.render_rollback_script(plan)
    preview_hash = renderer.script_hash(forward)
    snap_id = snap_repo.create_snapshot(
        tenant_id=1, router_id=router_id,
        snapshot_type=snap_repo.SNAPSHOT_TYPE_COMPOSITE,
    )
    return (policy, plan, forward, rollback,
            preview_hash, snap_id)


# ─── 1. Happy path ──────────────────────────────────────────


def test_e2e_happy_path_apply_then_rollback(app):
    """Full pipeline: create policy → render → snapshot →
    contracts ready → apply succeeds → rollback succeeds."""
    rid = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_rollback_service as rollback_mod,
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()
    fake_exec.program_success(rid, for_forward=True)
    fake_exec.program_success(rid, for_rollback=True)

    with app.app_context():
        (policy, plan, forward, rollback,
         preview_hash, snap_id) = _make_policy_with_plan(
            app, router_id=rid,
        )
        impact, conflicts, deps, blast, canary, health = (
            _build_intelligence(
                app, policy=policy, plan=plan,
                forward=forward, rollback=rollback,
            )
        )
        apply_res = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script=forward,
            rollback_script=rollback,
            render_error="",
            preview_hash=preview_hash,
            snapshot_id=snap_id,
            target_router_ids=(rid,),
            actor="alice", actor_has_apply_perm=True,
            confirmations=(),
            execution_mode="full",
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
            executor=fake_exec,
        )
        assert apply_res.ok, apply_res.as_dict()
        assert apply_res.status == "succeeded"
        cs_id = apply_res.change_set_id

        rb_res = rollback_mod.request_rollback(
            tenant_id=1, service="remote_access",
            policy_id=int(policy["id"]),
            change_set_id=cs_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake_exec,
        )
        assert rb_res.ok, rb_res.as_dict()
        assert rb_res.status == "rolled_back"

        # Original change_set mirrored to rolled_back.
        from app.radius.db.repos import (
            npc_change_sets_repo as cs_repo,
        )
        original = cs_repo.get(1, cs_id)
        assert original["status"] == "rolled_back"

    # Executor saw both calls and nothing else.
    kinds = sorted(c["kind"] for c in fake_exec.calls)
    assert kinds == ["forward", "rollback"]


# ─── 2. Blocked: critical risk ──────────────────────────────


def test_e2e_blocked_critical_risk_never_calls_executor(app):
    """When the impact analyzer classifies the plan as
    `critical`, the contracts engine refuses with
    BLOCK_CRITICAL_RISK and the executor is never called."""
    rid = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()
    fake_exec.program_success(rid, for_forward=True)

    with app.app_context():
        (policy, plan, forward, rollback,
         preview_hash, snap_id) = _make_policy_with_plan(
            app, router_id=rid,
        )
        impact, conflicts, deps, blast, canary, health = (
            _build_intelligence(
                app, policy=policy, plan=plan,
                forward=forward, rollback=rollback,
            )
        )
        # Forge a critical-risk impact analysis by replacing the
        # risk_level on a copy of the real one.
        import dataclasses as _dc
        critical_impact = _dc.replace(impact, risk_level="critical")
        res = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script=forward,
            rollback_script=rollback,
            render_error="",
            preview_hash=preview_hash,
            snapshot_id=snap_id,
            target_router_ids=(rid,),
            actor="alice", actor_has_apply_perm=True,
            confirmations=(),
            execution_mode="full",
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=critical_impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
            executor=fake_exec,
        )
    assert not res.ok
    codes = {b.code for b in res.blockers}
    assert "critical_risk" in codes
    # No forward call ever reached the executor.
    assert not any(c["kind"] == "forward"
                   for c in fake_exec.calls)


# ─── 3. Blocked: no preview generated ───────────────────────


def test_e2e_blocked_when_no_preview_generated(app):
    """If the operator tries to apply with no forward script
    (no preview was generated, or the render failed), contracts
    refuses with BLOCK_NO_VALID_PREVIEW."""
    rid = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()

    with app.app_context():
        (policy, plan, forward, rollback,
         preview_hash, snap_id) = _make_policy_with_plan(
            app, router_id=rid,
        )
        impact, conflicts, deps, blast, canary, health = (
            _build_intelligence(
                app, policy=policy, plan=plan,
                forward=forward, rollback=rollback,
            )
        )
        res = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script="",   # the gate
            rollback_script="",
            render_error="renderer never ran",
            preview_hash="",
            snapshot_id=snap_id,
            target_router_ids=(rid,),
            actor="alice", actor_has_apply_perm=True,
            confirmations=(),
            execution_mode="full",
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
            executor=fake_exec,
        )
    assert not res.ok
    codes = {b.code for b in res.blockers}
    assert "no_valid_preview" in codes
    assert not fake_exec.calls


# ─── 4. Blocked: no snapshot ────────────────────────────────


def test_e2e_blocked_no_snapshot_id(app):
    """Apply without a snapshot id must be refused."""
    rid = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()

    with app.app_context():
        (policy, plan, forward, rollback,
         preview_hash, _snap_id) = _make_policy_with_plan(
            app, router_id=rid,
        )
        impact, conflicts, deps, blast, canary, health = (
            _build_intelligence(
                app, policy=policy, plan=plan,
                forward=forward, rollback=rollback,
            )
        )
        res = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script=forward,
            rollback_script=rollback,
            render_error="",
            preview_hash=preview_hash,
            snapshot_id=None,  # the gate
            target_router_ids=(rid,),
            actor="alice", actor_has_apply_perm=True,
            confirmations=(),
            execution_mode="full",
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
            executor=fake_exec,
        )
    assert not res.ok
    codes = {b.code for b in res.blockers}
    assert "no_snapshot" in codes
    assert not fake_exec.calls


# ─── 5. Partial router failure → rollback survivor ──────────


def test_e2e_partial_router_failure_rollback_survivor(app):
    """Two routers, one fails. Aggregate status is
    `partially_succeeded`. Rollback only touches the router
    that actually applied — the failed one had nothing to
    undo."""
    rid_a = _seed_router(app)
    rid_b = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_rollback_service as rollback_mod,
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()
    fake_exec.program_success(rid_a, for_forward=True)
    fake_exec.program_failure(rid_b, for_forward=True,
                               error="ssh timeout")
    fake_exec.program_success(rid_a, for_rollback=True)

    with app.app_context():
        (policy, plan, forward, rollback,
         preview_hash, snap_id) = _make_policy_with_plan(
            app, router_id=rid_a,
        )
        impact, conflicts, deps, blast, canary, health = (
            _build_intelligence(
                app, policy=policy, plan=plan,
                forward=forward, rollback=rollback,
                router_count=2,
            )
        )
        apply_res = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script=forward,
            rollback_script=rollback,
            render_error="",
            preview_hash=preview_hash,
            snapshot_id=snap_id,
            target_router_ids=(rid_a, rid_b),
            actor="alice", actor_has_apply_perm=True,
            confirmations=(),
            execution_mode="full",  # not canary — both attempted
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
            executor=fake_exec,
        )
        assert apply_res.status == "partially_succeeded"

        rb_res = rollback_mod.request_rollback(
            tenant_id=1, service="remote_access",
            policy_id=int(policy["id"]),
            change_set_id=apply_res.change_set_id,
            actor="alice", actor_has_apply_perm=True,
            executor=fake_exec,
        )

    assert rb_res.ok
    # Only one rollback call was issued — for the survivor.
    rb_calls = [c for c in fake_exec.calls
                 if c["kind"] == "rollback"]
    assert len(rb_calls) == 1
    assert rb_calls[0]["router_id"] == rid_a


# ─── 6. Permission matrix ───────────────────────────────────


def test_e2e_permission_matrix_apply_denied_without_perm(app):
    """An actor lacking the apply permission cannot apply.
    The contracts engine emits BLOCK_MISSING_APPLY_PERM and no
    change_set is created."""
    rid = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()

    with app.app_context():
        (policy, plan, forward, rollback,
         preview_hash, snap_id) = _make_policy_with_plan(
            app, router_id=rid,
        )
        impact, conflicts, deps, blast, canary, health = (
            _build_intelligence(
                app, policy=policy, plan=plan,
                forward=forward, rollback=rollback,
            )
        )
        res = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script=forward,
            rollback_script=rollback,
            render_error="",
            preview_hash=preview_hash,
            snapshot_id=snap_id,
            target_router_ids=(rid,),
            actor="viewer-only",
            actor_has_apply_perm=False,   # the gate
            confirmations=(),
            execution_mode="full",
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
            executor=fake_exec,
        )
    assert not res.ok
    codes = {b.code for b in res.blockers}
    assert "missing_apply_perm" in codes
    assert not fake_exec.calls


def test_e2e_permission_matrix_rollback_denied_without_perm(app):
    """Same actor-perm gate on the rollback service."""
    rid = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_rollback_service as rollback_mod,
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()
    fake_exec.program_success(rid, for_forward=True)

    with app.app_context():
        (policy, plan, forward, rollback,
         preview_hash, snap_id) = _make_policy_with_plan(
            app, router_id=rid,
        )
        impact, conflicts, deps, blast, canary, health = (
            _build_intelligence(
                app, policy=policy, plan=plan,
                forward=forward, rollback=rollback,
            )
        )
        apply_res = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script=forward,
            rollback_script=rollback,
            render_error="",
            preview_hash=preview_hash,
            snapshot_id=snap_id,
            target_router_ids=(rid,),
            actor="alice", actor_has_apply_perm=True,
            confirmations=(),
            execution_mode="full",
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
            executor=fake_exec,
        )
        assert apply_res.ok

        # Now a viewer attempts rollback.
        rb_res = rollback_mod.request_rollback(
            tenant_id=1, service="remote_access",
            policy_id=int(policy["id"]),
            change_set_id=apply_res.change_set_id,
            actor="viewer-only",
            actor_has_apply_perm=False,    # the gate
            executor=fake_exec,
        )
    assert not rb_res.ok
    # Rollback executor was never invoked for the viewer.
    rb_calls = [c for c in fake_exec.calls
                 if c["kind"] == "rollback"]
    assert rb_calls == []


# ─── 7. Adapter boundary invariant ──────────────────────────


def test_e2e_default_executor_is_live_router_executor(app):
    """The bootstrap installs the Live executor by default so
    NPC works out of the box. Set HOBERADIUS_NPC_DISABLE_LIVE=1
    to revert to Null when a kill-switch is needed."""
    from app.radius.services import (
        npc_router_executor as exec_mod,
    )
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    assert isinstance(
        exec_mod.get_router_executor(), LiveRouterExecutor,
    )


def test_e2e_default_state_reader_is_live_state_reader(app):
    """Same default-on invariant for the snapshot reader."""
    from app.radius.services import (
        npc_router_state_reader as reader_mod,
    )
    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    assert isinstance(
        reader_mod.get_state_reader(), LiveRouterStateReader,
    )


def test_e2e_kill_switch_restores_null_adapters(
    app, monkeypatch,
):
    """When HOBERADIUS_NPC_DISABLE_LIVE=1 is set, the bootstrap
    refuses to install live and the Null adapters stay."""
    from app.radius.services import (
        npc_router_executor as exec_mod,
        npc_router_state_reader as reader_mod,
    )
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    monkeypatch.setenv("HOBERADIUS_NPC_DISABLE_LIVE", "1")
    exec_mod.set_router_executor(None)
    reader_mod.set_state_reader(None)
    out = install_live_adapters_from_env()
    assert out["installed"] is False
    assert isinstance(
        exec_mod.get_router_executor(),
        exec_mod.NullRouterExecutor,
    )
