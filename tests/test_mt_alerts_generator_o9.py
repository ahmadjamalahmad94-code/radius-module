"""O9 — Smart alert generator from O3 problems."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o9_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed_nas(app, *, nas_id):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct')""",
                (nas_id, f"o9-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )


def test_empty_tenant_creates_nothing(app):
    with app.app_context():
        from app.radius.services.mt_alerts_generator import (
            refresh_alerts_from_problems,
        )
        out = refresh_alerts_from_problems(1)
    assert out["opened"] == 0
    assert out["resolved"] == 0


def test_critical_alert_seeds_auto_alert(app):
    _seed_nas(app, nas_id=1)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="src:1",
            router_id=1, title_ar="crit", severity="critical",
        )
        from app.radius.services.mt_alerts_generator import (
            refresh_alerts_from_problems,
        )
        out = refresh_alerts_from_problems(1)
    # Multiple auto-alerts open because problems include the
    # critical alert + missing backup.
    assert out["opened"] >= 2


def test_rerun_only_refreshes_does_not_duplicate(app):
    _seed_nas(app, nas_id=2)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        from app.radius.services.mt_alerts_generator import (
            refresh_alerts_from_problems,
        )
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="src:2",
            router_id=2, title_ar="crit", severity="critical",
        )
        first = refresh_alerts_from_problems(1)
        second = refresh_alerts_from_problems(1)
    # First pass opens new; second pass refreshes — no new opens.
    assert first["opened"] >= 1
    assert second["opened"] == 0
    assert second["refreshed"] >= 1


def test_resolved_when_underlying_problem_clears(app):
    _seed_nas(app, nas_id=3)
    with app.app_context():
        from app.radius.db.repos import (
            alerts_repo, router_backups_repo as br,
        )
        from app.radius.services.mt_alerts_generator import (
            refresh_alerts_from_problems,
        )
        first = refresh_alerts_from_problems(1)
        # Backup-missing alert was opened. Take a backup → next
        # refresh should resolve it.
        br.record(tenant_id=1, router_id=3,
                   backup_type="binary", filename="x.backup",
                   status="success")
        second = refresh_alerts_from_problems(1)
    assert second["resolved"] >= 1


def test_only_auto_alerts_are_resolved(app):
    """A non-auto alert opened externally must NOT be touched
    by the generator when its underlying problem isn't tracked."""
    _seed_nas(app, nas_id=4)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="external.something",
            dedup_key="manual:nope",
            router_id=4, title_ar="kept",
            severity="warning",
        )
        from app.radius.services.mt_alerts_generator import (
            refresh_alerts_from_problems,
        )
        refresh_alerts_from_problems(1)
        rows = alerts_repo.list_open(1, router_id=4)
    keys = {r.get("dedup_key") for r in rows}
    # Externally-opened alert kept open.
    assert "manual:nope" in keys


def test_auto_alert_carries_problem_metadata(app):
    _seed_nas(app, nas_id=5)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="src:5",
            router_id=5, title_ar="crit", severity="critical",
        )
        from app.radius.services.mt_alerts_generator import (
            refresh_alerts_from_problems,
        )
        refresh_alerts_from_problems(1)
        rows = alerts_repo.list_open(1, router_id=5)
    auto_rows = [r for r in rows
                 if (r.get("rule") or "").startswith("auto.")]
    assert auto_rows
    row = auto_rows[0]
    # Recommended action is the problem's suggested step.
    assert row["recommended_action_ar"]
