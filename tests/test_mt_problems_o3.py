"""O3 — Operations Problems Center: aggregator + route."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o3_")
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


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"o3_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o3-pass", full_name="O3",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o3-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id, name=None, enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, 'direct')""",
                (nas_id, name or f"o3-rtr-{nas_id}",
                 f"203.0.113.{nas_id}",
                 1 if enabled else 0, now),
            )


# ─── Aggregator ──────────────────────────────────────────────


def test_empty_tenant_returns_zero_problems(app):
    with app.app_context():
        from app.radius.services.mt_problems import build_problems
        p = build_problems(1)
    assert p["total"] == 0
    assert p["now"] == [] and p["soon"] == [] and p["info"] == []


def test_disabled_router_is_info_bucket(app):
    _seed_nas(app, nas_id=1, enabled=False)
    with app.app_context():
        from app.radius.services.mt_problems import (
            build_problems, PROBLEM_DISABLED,
        )
        p = build_problems(1)
    assert p["total"] == 1
    assert any(x.type == PROBLEM_DISABLED for x in p["info"])


def test_critical_alert_lands_in_now_bucket(app):
    _seed_nas(app, nas_id=2)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:2",
            router_id=2, title_ar="crit", severity="critical",
        )
        from app.radius.services.mt_problems import (
            build_problems, PROBLEM_CRITICAL_ALERT,
        )
        p = build_problems(1)
    types_now = {x.type for x in p["now"]}
    assert PROBLEM_CRITICAL_ALERT in types_now


def test_missing_backup_lands_in_soon_bucket(app):
    _seed_nas(app, nas_id=3)
    with app.app_context():
        from app.radius.services.mt_problems import (
            build_problems, PROBLEM_BACKUP_MISSING,
        )
        p = build_problems(1)
    assert any(x.type == PROBLEM_BACKUP_MISSING for x in p["soon"])


def test_partial_apply_lands_in_now_bucket(app):
    _seed_nas(app, nas_id=4)
    with app.app_context():
        from app.radius.db.repos import audit_repo
        audit_repo.record(
            tenant_id=1, actor="op",
            action="mt.programming.hotspot.apply",
            target_type="mikrotik_nas", target_id="4",
            router_id=4, severity="warning",
            result_status="partial",
        )
        from app.radius.services.mt_problems import (
            build_problems, PROBLEM_PARTIAL_APPLY,
        )
        p = build_problems(1)
    assert any(x.type == PROBLEM_PARTIAL_APPLY for x in p["now"])


def test_filter_by_router(app):
    _seed_nas(app, nas_id=5)
    _seed_nas(app, nas_id=6)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:5",
            router_id=5, title_ar="t", severity="critical",
        )
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:6",
            router_id=6, title_ar="t", severity="critical",
        )
        from app.radius.services.mt_problems import build_problems
        p = build_problems(1, router_id=5)
    # Only router 5's problems surface.
    assert all(item.router_id == 5
               for bucket in ("now", "soon", "info")
               for item in p[bucket])


def test_filter_by_severity_keeps_only_matching(app):
    _seed_nas(app, nas_id=7)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:7:c",
            router_id=7, title_ar="t", severity="critical",
        )
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:7:w",
            router_id=7, title_ar="t", severity="warning",
        )
        from app.radius.services.mt_problems import build_problems
        p = build_problems(1, severity="critical")
    assert p["soon"] == []  # warning filtered out
    assert all(item.severity == "critical" for item in p["now"])


def test_filter_by_type(app):
    _seed_nas(app, nas_id=8)
    with app.app_context():
        from app.radius.services.mt_problems import (
            build_problems, PROBLEM_BACKUP_MISSING,
        )
        # New router → has missing-backup signal. Filter to that.
        p = build_problems(1, type=PROBLEM_BACKUP_MISSING)
    items = p["now"] + p["soon"] + p["info"]
    assert all(x.type == PROBLEM_BACKUP_MISSING for x in items)


def test_each_problem_includes_router_name_and_link(app):
    _seed_nas(app, nas_id=9, name="prob-rtr")
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:9",
            router_id=9, title_ar="t", severity="critical",
        )
        from app.radius.services.mt_problems import build_problems
        p = build_problems(1)
    item = next(iter(p["now"]))
    assert item.router_name == "prob-rtr"
    assert "/admin/radius" in item.suggested_href


# ─── Route layer ─────────────────────────────────────────────


def test_problems_route_login_guarded(client):
    res = client.get("/admin/radius/problems",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_problems_route_renders_shell(app, client):
    _login(client)
    html = client.get("/admin/radius/problems").get_data(as_text=True)
    assert "data-mt-problems-page" in html
    assert "data-mt-problems-filter-severity" in html
    assert "data-mt-problems-filter-type" in html
    assert "data-mt-problems-filter-router" in html
    assert "data-mt-problems-empty" in html


def test_problems_route_renders_buckets_when_problems_exist(
        app, client):
    _seed_nas(app, nas_id=10)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:10",
            router_id=10, title_ar="حرج",
            severity="critical",
        )
    _login(client)
    html = client.get("/admin/radius/problems").get_data(as_text=True)
    assert 'data-mt-problems-bucket="now"' in html
    # Critical alert from router 10 surfaces.
    assert 'data-mt-problem-router="10"' in html
    assert "alert.critical" in html  # the type identifier


def test_filter_via_query_string_narrows(app, client):
    _seed_nas(app, nas_id=11)
    _seed_nas(app, nas_id=12)
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:11",
            router_id=11, title_ar="t", severity="critical",
        )
        alerts_repo.open(
            tenant_id=1, rule="x", dedup_key="o3:12",
            router_id=12, title_ar="t", severity="critical",
        )
    _login(client)
    html = client.get(
        "/admin/radius/problems?router_id=11").get_data(as_text=True)
    assert 'data-mt-problem-router="11"' in html
    assert 'data-mt-problem-router="12"' not in html
