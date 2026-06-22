"""Regression tests for two defects fixed on fix/tunnels-v2.

1. ``/admin/radius/tunnels`` 500'd whenever the license bridge was actually
   configured, because ``_render`` read ``config.shared_secret`` — a field
   REMOVED from ``AdminBridgeConfig`` in the يونيو 2026 SIMPLE_LINK purge. The
   unconfigured state short-circuited on ``enabled`` so the bug only surfaced
   for linked customers. The page must render 200 in all three states:
   bridge-not-configured/empty, bridge-configured/empty, bridge-configured/rows.

2. Two migrations shared the ``123`` prefix (123_access_control_blocks.sql and
   123_data_connection.sql). The latter was renumbered to 132 + aliased so it
   applies exactly once on both fresh and already-migrated DBs (its non-idempotent
   ``ALTER TABLE subscribers ADD COLUMN transport`` would crash on re-run).

All bridge I/O is avoided — the page only reads local config + the local
bridge_tunnels table.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


# ── App / client harness ────────────────────────────────────────────────────
def _fresh_modules() -> None:
    for k in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[k]


def _make_app(monkeypatch, *, bridge: bool):
    tmp = tempfile.mkdtemp(prefix="hr_tun_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    if bridge:
        # Configured bridge: enabled + HTTPS panel URL + license_key. This is the
        # exact state that used to reach the dead ``config.shared_secret`` term.
        monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "1")
        monkeypatch.setenv("HOBERADIUS_ADMIN_BASE_URL", "https://panel.example.com")
        monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "lic_test_aaaa1111")
    else:
        for k in ("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "HOBERADIUS_ADMIN_BASE_URL",
                  "HOBERADIUS_LICENSE_KEY"):
            monkeypatch.delenv(k, raising=False)
    _fresh_modules()
    from app import create_app
    return create_app()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"tn_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="tn-pass", full_name="TN Tester",
                             is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "tn-pass"})
    assert res.status_code in {302, 303}


# ── 1. tunnels page renders in all three states ─────────────────────────────
def test_tunnels_page_unconfigured_empty(monkeypatch):
    """Bridge not configured + zero tunnels → 200 with the clear «not ready»
    state and the empty-state, never a 500."""
    app = _make_app(monkeypatch, bridge=False)
    client = app.test_client()
    _login(client)
    res = client.get("/admin/radius/tunnels")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "الجسر مع لوحة التراخيص غير جاهز" in html  # bridge-not-configured state
    assert "لا توجد أنفاق بعد" in html  # empty-state
    # The request form must NOT be offered while the bridge is unusable.
    assert 'name="tunnel_type"' not in html


def test_tunnels_page_configured_empty(monkeypatch):
    """Bridge configured + zero tunnels → 200 with the request form (this is the
    state that previously 500'd on the removed ``shared_secret`` field)."""
    app = _make_app(monkeypatch, bridge=True)
    client = app.test_client()
    _login(client)
    res = client.get("/admin/radius/tunnels")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'name="tunnel_type"' in html  # request form is shown (bridge_ready)
    assert "الجسر مع لوحة التراخيص غير جاهز" not in html
    assert "لا توجد أنفاق بعد" in html  # empty-state


def test_tunnels_page_configured_with_rows(monkeypatch):
    """Bridge configured + existing local tunnels → 200 with the table/rows."""
    app = _make_app(monkeypatch, bridge=True)
    client = app.test_client()
    _login(client)
    with app.app_context():
        from app.radius.db.repos import bridge_tunnels_repo
        bridge_tunnels_repo.upsert_tunnel(
            tenant_id=1, remote_name="rtr-branch-a", tunnel_type="sstp",
            status="active", username="u-branch-a", remote_address="10.20.0.5",
        )
    res = client.get("/admin/radius/tunnels")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "rtr-branch-a" in html
    assert 'class="tn-table"' in html


def test_admin_bridge_config_has_no_shared_secret():
    """Guard: the route must not reference a field that no longer exists. If
    ``shared_secret`` is ever re-added, this asserts the contract explicitly."""
    _fresh_modules()
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    cfg = AdminBridgeConfig.from_env()
    assert not hasattr(cfg, "shared_secret")


# ── 2. migration runner handles the 123 collision ───────────────────────────
def _migration_names():
    from app.radius.db.migrations_runner import list_migrations
    return [p.name for p in list_migrations()]


def test_dup_123_prefix_resolved(monkeypatch):
    """Exactly one file keeps the 123 prefix; data_connection moved to 132."""
    app = _make_app(monkeypatch, bridge=False)
    with app.app_context():
        names = _migration_names()
    prefix_123 = [n for n in names if n.startswith("123_")]
    assert prefix_123 == ["123_access_control_blocks.sql"], prefix_123
    assert "132_data_connection.sql" in names
    assert "123_data_connection.sql" not in names


# Two duplicate prefixes pre-date this fix and are out of scope: 027
# (lifecycle_retention + subscriber_groups) and 085 (card_user_portal_passwords
# + recharge_cards). Both pairs apply fine because the runner keys on the FULL
# filename, not the prefix. This guard tolerates them while catching the 123
# collision returning or any NEW collision being introduced.
_KNOWN_LEGACY_DUP_PREFIXES = {"027", "085"}


def test_no_unexpected_duplicate_migration_prefixes(monkeypatch):
    """123 must stay resolved, and no new duplicate prefix may appear."""
    import re
    from collections import Counter

    app = _make_app(monkeypatch, bridge=False)
    with app.app_context():
        names = _migration_names()
    prefixes = []
    for name in names:
        m = re.match(r"^(\d+)_", name)
        assert m, f"migration filename without numeric prefix: {name}"
        prefixes.append(m.group(1))
    dupes = {p for p, c in Counter(prefixes).items() if c > 1}
    assert "123" not in dupes, "the 123 collision must stay resolved"
    unexpected = dupes - _KNOWN_LEGACY_DUP_PREFIXES
    assert not unexpected, f"new duplicate migration prefixes introduced: {unexpected}"


def test_fresh_db_has_all_expected_tables(monkeypatch):
    """Fresh migrated DB must contain bridge_tunnels + access-control +
    data-connection objects, and the non-idempotent ``transport`` column."""
    app = _make_app(monkeypatch, bridge=False)
    with app.app_context():
        from app.radius.db.connection import db
        tables = {
            r["name"] for r in db().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"bridge_tunnels", "access_blocks", "login_failure_tracker",
                "data_connection_wg_peers"} <= tables
        cols = {r["name"] for r in db().execute(
            "PRAGMA table_info(subscribers)").fetchall()}
        assert "transport" in cols


def test_runner_idempotent_rerun(monkeypatch):
    """Re-running the runner on an already-migrated DB applies 0 migrations
    (no double-apply of the non-idempotent ALTER)."""
    app = _make_app(monkeypatch, bridge=False)
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        assert run_pending_migrations() == 0


def test_alias_skips_renumbered_on_legacy_db(monkeypatch):
    """A DB that recorded the OLD ``123_data_connection.sql`` name must treat
    ``132_data_connection.sql`` as already applied — otherwise its
    non-idempotent ALTER would crash with «duplicate column name: transport».

    Simulate the legacy record, then assert the runner skips the renamed file.
    """
    app = _make_app(monkeypatch, bridge=False)
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.migrations_runner import _applied, run_pending_migrations
        # Pretend this DB was migrated under the pre-renumber filename.
        db().execute(
            "INSERT OR IGNORE INTO _migrations(name, applied_at) VALUES(?, ?)",
            ("123_data_connection.sql", "2026-01-01T00:00:00Z"),
        )
        # Drop the runner's own record of the new name to force the alias path.
        db().execute("DELETE FROM _migrations WHERE name = ?",
                     ("132_data_connection.sql",))
        applied = _applied()
        assert "132_data_connection.sql" in applied  # via alias
        # And a real re-run must NOT attempt the renamed file (would raise).
        assert run_pending_migrations() == 0
