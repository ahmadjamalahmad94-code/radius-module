"""NPC Safe-Execution Phase 4 — guarded apply engine + route."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_se4_")
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


@pytest.fixture
def client(app):
    return app.test_client()


def _login_super(client, monkeypatch):
    sa = SimpleNamespace(id=1, username="alice",
                         is_super_admin=True)

    class _Store:
        @staticmethod
        def get_admin(_):
            return sa

    class _Svc:
        _store = _Store()
        def permissions_of(self, _):
            return ()

    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "alice"
        s["tenant_id"] = 1


def _csrf(client):
    client.get(
        "/admin/radius/network-policy/remote-access/new"
    )
    with client.session_transaction() as s:
        return s.get("_csrf_token") or ""


def _seed_router(app, *, name=None, address=None):
    """Per-call unique name+address by default so multi-router
    tests don't trip the (tenant, name) UNIQUE index."""
    import secrets as _sec
    suffix = _sec.token_hex(3)
    nm = name or f"rt-{suffix}"
    addr = address or f"10.0.{int(suffix, 16) % 256}.1"
    with app.app_context():
        from app.radius.db.connection import db, transaction
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


def _good_remote_policy(client, csrf, rid):
    client.post(
        "/admin/radius/network-policy/remote-access/new",
        data={"_csrf_token": csrf,
              "name": "good", "router_id": str(rid),
              "allow_winbox": "on",
              "source_address_list": "ops",
              "expires_at": "2027-01-01T00:00:00Z",
              "enabled": "on"},
        follow_redirects=False,
    )
    with client.application.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as r,
        )
        return r.list_for_tenant(1)[-1]["id"]


def _install_fakes(app, monkeypatch):
    """Inject a fake state reader (so snapshot capture
    succeeds) + a fake executor (so apply has something to
    drive). Returns the executor for assertion."""
    from app.radius.services import (
        npc_router_executor as exec_mod,
        npc_router_state_reader as reader_mod,
    )
    # Reader: just enough to make capture succeed with one
    # firewall filter row.
    reader_mod.set_state_reader(reader_mod.FakeStateReader({
        reader_mod.SECTION_FIREWALL_FILTER: [
            {"item_kind": "firewall_filter_rule",
             "source_id": "*1",
             "display_text": "input accept admin",
             "payload": {"chain": "input",
                          "action": "accept"}},
        ],
    }))
    # Executor: programmable.
    fake_exec = exec_mod.FakeRouterExecutor()
    exec_mod.set_router_executor(fake_exec)
    # Tear down at end of test via monkeypatch finaliser.
    monkeypatch.setattr(
        exec_mod, "_OVERRIDE", fake_exec, raising=False,
    )
    return fake_exec


def _teardown_fakes():
    from app.radius.services import (
        npc_router_executor as exec_mod,
        npc_router_state_reader as reader_mod,
    )
    reader_mod.set_state_reader(None)
    exec_mod.set_router_executor(None)


# ─── Apply route presence + perm gate ───────────────────────


def test_apply_route_exists_post_only(app):
    with app.app_context():
        rules = [str(r) for r in app.url_map.iter_rules()
                 if "/network-policy/" in str(r)
                 and "/apply" in str(r)]
    # Three sub-services × one apply route each.
    assert any("remote-access" in u for u in rules)
    assert any("web-block" in u for u in rules)
    assert any("walled-garden" in u for u in rules)


def test_apply_get_method_not_allowed(app, client, monkeypatch):
    _login_super(client, monkeypatch)
    r = client.get(
        "/admin/radius/network-policy/remote-access/1/apply"
    )
    assert r.status_code == 405


def test_apply_unauth_redirects_to_login(app, client):
    r = client.post(
        "/admin/radius/network-policy/remote-access/1/apply",
        data={},
        follow_redirects=False,
    )
    # Unauth → either 302 to login or 405 if blocked before.
    assert r.status_code in (302, 303, 405)


def test_apply_without_apply_perm_is_403(
    app, client, monkeypatch,
):
    """A signed-in admin who lacks `npc.remote_access.apply`
    must get 403 on the apply route. The perm decorator is
    the first gate after CSRF."""
    rid = _seed_router(app)
    # Seed a policy directly so we have a target id.
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as r,
        )
        pid = r.create(tenant_id=1, router_id=rid,
                        name="x", allow_winbox=True,
                        source_address_list="ops",
                        expires_at="2027-01-01T00:00:00Z")
    # Stub admins service: Bob has only a non-apply perm.
    bob = SimpleNamespace(id=2, username="bob",
                          is_super_admin=False)

    class _Store:
        @staticmethod
        def get_admin(_):
            return bob

    class _Svc:
        _store = _Store()
        def permissions_of(self, _):
            return ("npc.remote_access.view",)

    import app.radius.services.admins as am
    monkeypatch.setattr(am, "get_admins_service",
                        lambda: _Svc())
    # Seed both session admin_id AND a deterministic CSRF
    # token in one shot — avoids depending on which pages
    # carry forms in the test environment.
    with client.session_transaction() as s:
        s["admin_id"] = 2
        s["admin_user"] = "bob"
        s["tenant_id"] = 1
        s["_csrf_token"] = "test-token"
    r = client.post(
        f"/admin/radius/network-policy/remote-access/{pid}/apply",
        data={"_csrf_token": "test-token"},
        follow_redirects=False,
    )
    assert r.status_code == 403


# ─── Apply succeeds with fake executor ─────────────────────


def test_apply_success_creates_change_set_with_targets(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    fake_exec = _install_fakes(app, monkeypatch)
    try:
        fake_exec.program_success(rid, for_forward=True)
        r = client.post(
            f"/admin/radius/network-policy/remote-access/{pid}"
            "/apply",
            data={"_csrf_token": csrf,
                  "execution_mode": "full"},
            follow_redirects=False,
        )
        # Route redirects back to the preview.
        assert r.status_code in (302, 303)

        with app.app_context():
            from app.radius.db.repos import (
                npc_change_sets_repo as cs,
            )
            sets = cs.list_for_policy(
                1, service="remote_access", policy_id=pid,
            )
            assert sets, "no change_set created"
            change_set = sets[0]
            assert change_set["action_type"] == "apply"
            assert change_set["status"] == "succeeded"
            targets = cs.list_targets(change_set["id"])
            assert len(targets) == 1
            assert targets[0]["status"] == "succeeded"
            assert targets[0]["router_id"] == rid

        # Executor saw one forward call.
        assert any(c["kind"] == "forward"
                   and c["router_id"] == rid
                   for c in fake_exec.calls)
    finally:
        _teardown_fakes()


def test_apply_records_apply_audit_event(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    fake_exec = _install_fakes(app, monkeypatch)
    try:
        fake_exec.program_success(rid, for_forward=True)
        client.post(
            f"/admin/radius/network-policy/remote-access/{pid}"
            "/apply",
            data={"_csrf_token": csrf},
            follow_redirects=False,
        )
        with app.app_context():
            from app.radius.db.connection import db
            actions = [r["action"] for r in db().execute(
                "SELECT action FROM audit_log "
                "WHERE target_type='npc_remote_access_policy' "
                "ORDER BY id"
            ).fetchall()]
        assert "npc.remote_access.applied" in actions
    finally:
        _teardown_fakes()


# ─── Failure path ───────────────────────────────────────────


def test_apply_failure_creates_partial_status(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    fake_exec = _install_fakes(app, monkeypatch)
    try:
        # Program the executor to fail.
        fake_exec.program_failure(
            rid, for_forward=True,
            error="MikroTikTrap: invalid syntax",
        )
        r = client.post(
            f"/admin/radius/network-policy/remote-access/{pid}"
            "/apply",
            data={"_csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            from app.radius.db.repos import (
                npc_change_sets_repo as cs,
            )
            sets = cs.list_for_policy(
                1, service="remote_access", policy_id=pid,
            )
            change_set = sets[0]
            # Single-router failure → aggregate FAILED.
            assert change_set["status"] == "failed"
            targets = cs.list_targets(change_set["id"])
            assert targets[0]["status"] == "failed"
            assert "invalid syntax" in (targets[0]["error_message"] or "")
    finally:
        _teardown_fakes()


def test_apply_blocks_when_render_unsafe(
    app, client, monkeypatch,
):
    """A render-unsafe plan never reaches the executor — the
    contracts engine refuses with `unsafe_script`."""
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    fake_exec = _install_fakes(app, monkeypatch)
    try:
        # Force the renderer to refuse by patching the
        # renderer module to raise on every call.
        import app.radius.services.npc_script_renderer as rmod
        original = rmod.render_forward_script

        def _bad(_plan):
            raise rmod.RenderSafetyError(
                "tripwire 'password=' detected"
            )
        monkeypatch.setattr(rmod, "render_forward_script", _bad)

        r = client.post(
            f"/admin/radius/network-policy/remote-access/{pid}"
            "/apply",
            data={"_csrf_token": csrf},
            follow_redirects=False,
        )
        # Route redirects; the change_set is NOT created
        # because contracts refused.
        assert r.status_code in (302, 303)
        with app.app_context():
            from app.radius.db.repos import (
                npc_change_sets_repo as cs,
            )
            sets = cs.list_for_policy(
                1, service="remote_access", policy_id=pid,
            )
            assert sets == []
        # Executor saw NO forward calls.
        assert not any(c["kind"] == "forward"
                        for c in fake_exec.calls)

        monkeypatch.setattr(
            rmod, "render_forward_script", original,
        )
    finally:
        _teardown_fakes()


def test_apply_blocks_when_no_snapshot(
    app, client, monkeypatch,
):
    """If the state reader stays null (no snapshot captured)
    the contracts engine refuses with `no_snapshot`."""
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    # Note: NOT installing the fake reader → snapshot capture
    # raises StateReaderNotConfigured → snapshot_id stays None
    # → contracts engine refuses.
    from app.radius.services import (
        npc_router_executor as exec_mod,
    )
    fake_exec = exec_mod.FakeRouterExecutor()
    exec_mod.set_router_executor(fake_exec)
    try:
        r = client.post(
            f"/admin/radius/network-policy/remote-access/{pid}"
            "/apply",
            data={"_csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        with app.app_context():
            from app.radius.db.repos import (
                npc_change_sets_repo as cs,
            )
            sets = cs.list_for_policy(
                1, service="remote_access", policy_id=pid,
            )
        # No change_set created — contracts refused.
        assert sets == []
        # Executor never called.
        assert fake_exec.calls == []
    finally:
        exec_mod.set_router_executor(None)


# ─── Canary stops on first failure ─────────────────────────


def test_canary_mode_stops_after_first_failure_via_service(
    app, monkeypatch,
):
    """Direct service-level test — apply two routers in
    canary mode, first one fails, second should be marked
    `skipped` and NOT receive an executor call."""
    rid_a = _seed_router(app)
    rid_b = _seed_router(app)
    from app.radius.services import (
        npc_apply_service as apply_mod,
        npc_router_executor as exec_mod,
        npc_router_state_reader as reader_mod,
        npc_remote_access_planner as ra_p,
        npc_script_renderer as renderer,
        npc_blast_radius, npc_canary_planner,
        npc_conflict_detector, npc_dependency_detector,
        npc_impact_analyzer, npc_policy_health,
    )

    reader_mod.set_state_reader(reader_mod.FakeStateReader({}))
    fake_exec = exec_mod.FakeRouterExecutor()
    exec_mod.set_router_executor(fake_exec)
    # Router A fails, Router B would succeed but is skipped.
    fake_exec.program_failure(rid_a, for_forward=True,
                               error="bad")
    fake_exec.program_success(rid_b, for_forward=True)

    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as ra_repo,
            npc_snapshots_repo as snap_repo,
        )
        pid = ra_repo.create(
            tenant_id=1, router_id=rid_a, name="cn",
            allow_winbox=True, source_address_list="ops",
            expires_at="2027-01-01T00:00:00Z",
        )
        policy = ra_repo.get_by_id(1, pid)
        plan = ra_p.plan(policy)
        forward = renderer.render_forward_script(plan)
        rollback = renderer.render_rollback_script(plan)
        # Create a real snapshot so the no_snapshot blocker
        # doesn't fire.
        snap_id = snap_repo.create_snapshot(
            tenant_id=1, router_id=rid_a,
            snapshot_type=snap_repo.SNAPSHOT_TYPE_COMPOSITE,
        )

        # Build the intelligence inputs the apply service
        # expects (use real values from the planner).
        impact = npc_impact_analyzer.analyze(
            policy_type="remote_access", policy=policy,
            plan=plan, targets=(),
            rendered_forward=forward,
            rendered_rollback=rollback,
        )
        conflicts = npc_conflict_detector.analyze(
            current_service="remote_access",
            current_policy=policy, current_children=(),
            peers=(),
        )
        deps = npc_dependency_detector.analyze(targets=())
        blast = npc_blast_radius.analyze(
            policy_type="remote_access", plan=plan,
            affected_router_count=2,
        )
        canary = npc_canary_planner.plan(blast=blast)
        health = npc_policy_health.compute(
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
        )

        result = apply_mod.request_apply(
            tenant_id=1, service="remote_access",
            policy=policy, policy_children=(),
            forward_script=forward,
            rollback_script=rollback,
            render_error="",
            preview_hash=renderer.script_hash(forward),
            snapshot_id=snap_id,
            target_router_ids=(rid_a, rid_b),
            actor="test", actor_has_apply_perm=True,
            confirmations=(),
            execution_mode="canary",
            canary_opt_in=False,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=impact, conflicts=conflicts,
            dependencies=deps, blast=blast,
            health=health, canary=canary,
        )

    try:
        # Aggregate is PARTIAL because A failed and B was skipped.
        assert result.status in ("failed", "partially_succeeded")
        # Second router got SKIPPED, not actually executed.
        statuses = {t.router_id: t.status for t in result.targets}
        assert statuses[rid_a] == "failed"
        assert statuses[rid_b] == "skipped"
        # Executor saw only the first router's forward call.
        forward_calls = [c for c in fake_exec.calls
                          if c["kind"] == "forward"]
        assert len(forward_calls) == 1
        assert forward_calls[0]["router_id"] == rid_a
    finally:
        reader_mod.set_state_reader(None)
        exec_mod.set_router_executor(None)


# ─── No direct MikroTik client import in route/service ──────


def test_apply_service_does_not_import_mikrotik_client():
    """Hard invariant: the apply service goes through the
    executor adapter. It must not import the platform's
    MikrotikClient directly."""
    import importlib
    import app.radius.services.npc_apply_service as m
    importlib.reload(m)
    assert "MikrotikClient" not in dir(m)


def test_apply_route_does_not_import_mikrotik_client():
    """Same invariant for the route module — only the executor
    adapter is allowed."""
    import importlib
    import app.radius.routes.network_policy as r
    importlib.reload(r)
    assert "MikrotikClient" not in dir(r)
