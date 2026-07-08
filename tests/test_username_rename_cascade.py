# -*- coding: utf-8 -*-
"""feat/username-rename-cascade — SAFE rename of a subscriber's LOGIN username.

The username is the RADIUS auth key referenced BY VALUE across auth
(radcheck/radreply/radusergroup), accounting/session history (radacct), money,
per-user rules and portal tables. A rename must cascade to EVERY reference
atomically or the account is left with a broken login / orphaned accounting.

Covers:
  * The cascade moves subscribers + radcheck + radreply + radusergroup + radacct
    to the new name and leaves NOTHING under the old name.
  * After rename the OLD username no longer authenticates (no radcheck row) while
    the NEW one does (its Cleartext-Password row carried over).
  * A rename into an existing subscriber/card name is rejected (Arabic error),
    and NOTHING changes (atomic rollback / pre-check).
  * The audit log records «اسم الدخول: من X إلى Y» (login_username before→after).
  * A live session (open radacct row) is detected and its history is carried to
    the new name.
  * Charset / empty validation is enforced.

Run this file alone (per-file isolation — memory test-isolation-per-file).
"""
from __future__ import annotations

import json
import os

import pytest


# ════════════════════════════════════════════════════════════════════════
# Fixture: fresh migrated DB + sqlite RADIUS adapter (real cascade path)
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "username_rename.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")   # no MikroTik sync worker
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("RADIUS_MODE", "sqlite")       # exercise the real cascade
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app.radius.integration.factory import reset_radius_adapter_for_tests
    reset_radius_adapter_for_tests()
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app
    reset_radius_adapter_for_tests()


TID = 1


def _mk_sub(username="alice", password="pw-alice", status="enabled", **kw):
    """Create a subscriber row + provision its FreeRADIUS rows (radcheck /
    radreply / radusergroup) exactly like the live path does."""
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    from app.radius.services import freeradius_translator
    base = dict(id=None, username=username, password=password, tenant_id=TID,
                status=status)
    base.update(kw)
    saved = subscribers_repo.upsert_subscriber(Subscriber(**base))
    # Provision RADIUS (writes radcheck Cleartext-Password + radusergroup link).
    freeradius_translator.sync_subscriber(saved, None)
    return saved


def _seed_radacct(username, session_id="sess-1", open_session=True):
    """Insert a radacct row for username (open = live session)."""
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, username, "
            "nasipaddress, acctstarttime, acctstoptime) "
            "VALUES(?,?,?,?,datetime('now'),?)",
            (TID, session_id, username, "10.0.0.1",
             None if open_session else "2020-01-01 00:00:00"),
        )


# ── Authentication model: FreeRADIUS rlm_sql authenticates a subscriber iff a
#    radcheck Cleartext-Password row exists for (tenant, username). ──
def _authenticates(username, password):
    from app.radius.db.repos import freeradius_repo
    for c in freeradius_repo.list_user_check(TID, username):
        if c["attribute"] == "Cleartext-Password" and c["value"] == password:
            return True
    return False


def _rows_for(table, username, col="username"):
    from app.radius.db.connection import db
    return db().execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id=? AND {col}=?",
        (TID, username),
    ).fetchone()["c"]


def _svc():
    from app.radius.services.users import get_users_service
    return get_users_service()


# ════════════════════════════════════════════════════════════════════════
# (1) Cascade moves every reference to the new name
# ════════════════════════════════════════════════════════════════════════
class TestCascade:

    def test_subscribers_radcheck_radusergroup_radacct_move(self, app_ctx):
        _mk_sub("alice", "pw-alice")
        _seed_radacct("alice", open_session=False)

        # Sanity: everything lives under the OLD name first.
        assert _rows_for("subscribers", "alice") == 1
        assert _rows_for("radcheck", "alice") >= 1
        assert _rows_for("radusergroup", "alice") == 1
        assert _rows_for("radacct", "alice") == 1

        result = _svc().rename_username(
            actor="owner", old_username="alice", new_username="bob")
        assert result["renamed"] is True

        # NEW name holds every reference now …
        assert _rows_for("subscribers", "bob") == 1
        assert _rows_for("radcheck", "bob") >= 1
        assert _rows_for("radusergroup", "bob") == 1
        assert _rows_for("radacct", "bob") == 1
        assert "subscribers" in result["tables"]
        assert "radacct" in result["tables"]

        # … and NOTHING is left under the OLD name (no orphans).
        assert _rows_for("subscribers", "alice") == 0
        assert _rows_for("radcheck", "alice") == 0
        assert _rows_for("radreply", "alice") == 0
        assert _rows_for("radusergroup", "alice") == 0
        assert _rows_for("radacct", "alice") == 0

    def test_old_no_longer_authenticates_new_does(self, app_ctx):
        _mk_sub("alice", "secret-123")
        assert _authenticates("alice", "secret-123") is True

        _svc().rename_username(actor="owner", old_username="alice",
                               new_username="carol")

        # The auth key followed the rename: old is dead, new is live.
        assert _authenticates("alice", "secret-123") is False
        assert _authenticates("carol", "secret-123") is True


# ════════════════════════════════════════════════════════════════════════
# (2) Collision is rejected + nothing changes
# ════════════════════════════════════════════════════════════════════════
class TestUniqueness:

    def test_rename_to_existing_subscriber_rejected(self, app_ctx):
        from app.radius.core.errors import RadiusValidationError
        _mk_sub("alice", "pw-a")
        _mk_sub("taken", "pw-t")

        with pytest.raises(RadiusValidationError):
            _svc().rename_username(actor="owner", old_username="alice",
                                   new_username="taken")

        # Atomic: both accounts stay exactly as they were.
        assert _rows_for("subscribers", "alice") == 1
        assert _rows_for("subscribers", "taken") == 1
        assert _authenticates("alice", "pw-a") is True
        assert _authenticates("taken", "pw-t") is True

    def test_rename_into_card_name_rejected(self, app_ctx):
        from app.radius.core.errors import RadiusValidationError
        from app.radius.db.connection import db
        _mk_sub("alice", "pw-a")
        # A card row (shares the login namespace) under 'cardx'. Insert with FK
        # enforcement off so we don't have to stand up a full batch/plan graph —
        # the collision check only reads (tenant_id, username) from cards.
        conn = db()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, "
            "plan_id, created_at) VALUES(?,?,?,?,?,datetime('now'))",
            (TID, 1, "cardx", "cpw", 1),
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

        with pytest.raises(RadiusValidationError):
            _svc().rename_username(actor="owner", old_username="alice",
                                   new_username="cardx")
        assert _rows_for("subscribers", "alice") == 1

    def test_empty_and_bad_charset_rejected(self, app_ctx):
        from app.radius.core.errors import RadiusValidationError
        _mk_sub("alice", "pw-a")
        for bad in ("", "   ", "has space", "bad/slash", "قوس"):
            with pytest.raises(RadiusValidationError):
                _svc().rename_username(actor="owner", old_username="alice",
                                       new_username=bad)
        assert _rows_for("subscribers", "alice") == 1


# ════════════════════════════════════════════════════════════════════════
# (3) Audit records old→new
# ════════════════════════════════════════════════════════════════════════
class TestAudit:

    def test_audit_records_login_username_before_after(self, app_ctx):
        _mk_sub("alice", "pw-a")
        _svc().rename_username(actor="owner", old_username="alice",
                               new_username="dave")

        from app.radius.db.connection import db
        row = db().execute(
            "SELECT before_json, after_json, target_id FROM audit_log "
            "WHERE tenant_id=? AND target_type='user' AND target_id='dave' "
            "ORDER BY id DESC LIMIT 1",
            (TID,),
        ).fetchone()
        assert row is not None
        before = json.loads(row["before_json"] or "{}")
        after = json.loads(row["after_json"] or "{}")
        assert before.get("login_username") == "alice"
        assert after.get("login_username") == "dave"


# ════════════════════════════════════════════════════════════════════════
# (4) Live session is detected + carried to the new name
# ════════════════════════════════════════════════════════════════════════
class TestLiveSession:

    def test_open_session_flagged_and_history_renamed(self, app_ctx):
        _mk_sub("alice", "pw-a")
        _seed_radacct("alice", session_id="live-1", open_session=True)

        result = _svc().rename_username(
            actor="owner", old_username="alice", new_username="erin",
            disconnect=False)  # keep the row open so we can assert it moved

        assert result["had_live_session"] is True
        # The (still-open) accounting row now belongs to the new name.
        assert _rows_for("radacct", "erin") == 1
        assert _rows_for("radacct", "alice") == 0


# ════════════════════════════════════════════════════════════════════════
# (5) No-op rename (same name) is safe
# ════════════════════════════════════════════════════════════════════════
class TestNoop:

    def test_same_username_is_noop(self, app_ctx):
        _mk_sub("alice", "pw-a")
        result = _svc().rename_username(actor="owner", old_username="alice",
                                        new_username="alice")
        assert result["renamed"] is False
        assert _rows_for("subscribers", "alice") == 1
