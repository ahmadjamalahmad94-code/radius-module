# -*- coding: utf-8 -*-
"""FIX C + FIX D — /online sessions table rendering.

FIX C: a subscriber whose plan has an UNLIMITED/open rate (speed 0 / no
Mikrotik-Rate-Limit) used to render «— / —» in «سرعة العرض»/«السرعة الحالية»
as if the speed were unknown. Unlimited must render «غير محدود / غير محدود»;
«— / —» is reserved for genuinely-unknown (no plan / no data).

FIX D: the offer name in «العرض» was plain text. It must be a clickable
entity link (hr-entity-link) to the plan's page — only when the plan id is
known («من أي مكان أضغط أنتقل»).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_speedlink_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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
    u = f"spd_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="spd-pass",
                             full_name="Speed Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "spd-pass"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _seed(app, *, plan_speed_down=0, plan_speed_up=0, with_plan=True):
    """One live session for subscriber 'omar'; plan optional; returns plan id."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            plan_id = None
            if with_plan:
                c.execute(
                    "INSERT INTO access_plans (tenant_id, name, "
                    "speed_down_kbps, speed_up_kbps, created_at) "
                    "VALUES (1, 'عرض مفتوح', ?, ?, ?)",
                    (plan_speed_down, plan_speed_up, _now()))
                plan_id = c.execute(
                    "SELECT last_insert_rowid() AS id").fetchone()["id"]
            c.execute(
                "INSERT INTO subscribers (tenant_id, username, password, "
                "user_type, status, plan_id, created_at) "
                "VALUES (1, 'omar', 'p', 'subscriber', 'enabled', ?, ?)",
                (plan_id, _now()))
            c.execute(
                "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, "
                "username, nasipaddress, acctstarttime, acctupdatetime, "
                "acctstoptime) VALUES (1, 's1', 'u1', 'omar', '10.0.0.1', "
                "?, ?, NULL)", (_now(), _now()))
            return plan_id


def _page(client) -> str:
    res = client.get("/admin/radius/online")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_unlimited_plan_renders_ghair_mahdud(app, client):
    """Plan exists with speed 0/0 (open) → both speed columns say غير محدود,
    never — / —."""
    _seed(app, plan_speed_down=0, plan_speed_up=0)
    _login(client)
    html = _page(client)
    assert "غير محدود / غير محدود" in html
    # the session row itself must not carry the unknown dash pair
    assert "— / —" not in html


def test_limited_plan_still_renders_numbers(app, client):
    """Regression guard: a real rate still renders numerically."""
    _seed(app, plan_speed_down=4096, plan_speed_up=1024)
    _login(client)
    html = _page(client)
    assert "4.1 Mbps" in html
    assert "1.0 Mbps" in html
    # scoped to the speed-pair pattern — the single phrase legitimately
    # appears in unrelated page strings (modals/filters)
    assert "غير محدود / غير محدود" not in html


def test_no_plan_renders_unknown_dashes(app, client):
    """No plan at all → genuinely unknown → «— / —», not «غير محدود»."""
    _seed(app, with_plan=False)
    _login(client)
    html = _page(client)
    assert "— / —" in html
    assert "غير محدود / غير محدود" not in html


def test_plan_name_is_entity_link_to_plan_page(app, client):
    """FIX D: the offer name links to its plan page with the entity-link
    style; the href carries the real plan id."""
    plan_id = _seed(app, plan_speed_down=2048, plan_speed_up=512)
    _login(client)
    html = _page(client)
    assert f'href="/admin/radius/plans/{plan_id}/edit"' in html
    import re
    m = re.search(
        r'<a class="hr-entity-link" href="/admin/radius/plans/%d/edit">([^<]+)</a>'
        % plan_id, html)
    assert m and "عرض مفتوح" in m.group(1)


def test_no_plan_name_is_not_linked(app, client):
    """Sessions without a resolvable plan id render plain text (no dead link).
    Scoped to NUMERIC per-plan links — the sidebar legitimately carries
    /plans, /plans/new, /plans/overview."""
    import re
    _seed(app, with_plan=False)
    _login(client)
    html = _page(client)
    assert not re.search(r'href="/admin/radius/plans/\d+', html)
