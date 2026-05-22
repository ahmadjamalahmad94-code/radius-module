"""O6 — Before/after change preview."""
from __future__ import annotations

import pytest


def _build_hotspot_plan():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec, plan_hotspot,
    )
    return plan_hotspot(
        {}, HotspotProgrammingSpec(
            interface="ether2", cidr="192.168.10.0/24",
            hotspot_name="hs",
        ),
        existing_interfaces=[{"name": "ether2", "type": "ether"}],
        existing_addresses=[],
        existing_routes=[],
    )


# ─── Service ─────────────────────────────────────────────────


def test_preview_for_none_plan_returns_empty_warning():
    from app.radius.services.mt_change_preview import preview_plan
    pv = preview_plan(None)
    assert pv.total_changes() == 0
    assert pv.data_quality_warnings_ar


def test_preview_lists_each_planned_add():
    from app.radius.services.mt_change_preview import preview_plan
    plan = _build_hotspot_plan()
    pv = preview_plan(plan, snapshot_status="fresh")
    # Hotspot plan = 6 base commands + 2 walled-garden DNS
    # entries = 8 items in items_to_add.
    assert len(pv.items_to_add) >= 6
    kinds = {it.kind for it in pv.items_to_add}
    assert "pool" in kinds
    assert "address" in kinds
    assert "hotspot" in kinds


def test_preview_surfaces_data_quality_when_stale():
    from app.radius.services.mt_change_preview import preview_plan
    plan = _build_hotspot_plan()
    pv = preview_plan(plan, snapshot_status="stale")
    assert any("قديمة" in w for w in pv.data_quality_warnings_ar)


def test_preview_warns_on_missing_data():
    from app.radius.services.mt_change_preview import preview_plan
    plan = _build_hotspot_plan()
    for status in ("unknown", "failed"):
        pv = preview_plan(plan, snapshot_status=status)
        assert pv.data_quality_warnings_ar


def test_preview_impact_when_target_iface_has_existing_address():
    from app.radius.services.mt_change_preview import preview_plan
    plan = _build_hotspot_plan()
    pv = preview_plan(
        plan,
        snapshot_status="fresh",
        existing_interfaces=[{"name": "ether2", "type": "ether",
                              "running": "true"}],
        existing_addresses=[{"interface": "ether2",
                              "address": "10.0.0.1/24"}],
    )
    text = " ".join(pv.impact_ar)
    assert "ether2" in text
    assert "المستخدم" in text or "نشطة" in text


def test_preview_no_impact_when_iface_is_empty():
    from app.radius.services.mt_change_preview import preview_plan
    plan = _build_hotspot_plan()
    pv = preview_plan(
        plan, snapshot_status="fresh",
        existing_interfaces=[{"name": "ether2", "type": "ether",
                              "running": "false"}],
        existing_addresses=[],
    )
    assert pv.impact_ar == []


def test_preview_to_dict_shape():
    from app.radius.services.mt_change_preview import preview_plan
    plan = _build_hotspot_plan()
    pv = preview_plan(plan, snapshot_status="fresh")
    d = pv.to_dict()
    for k in ("items_to_add", "items_to_modify", "items_to_remove",
              "impact_ar", "data_quality_warnings_ar",
              "total_changes"):
        assert k in d


def test_preview_each_item_carries_kind_path_name():
    from app.radius.services.mt_change_preview import preview_plan
    plan = _build_hotspot_plan()
    pv = preview_plan(plan, snapshot_status="fresh")
    for it in pv.items_to_add:
        assert it.kind
        assert it.path.startswith("/")
        assert it.action == "add"


# ─── Route integration ──────────────────────────────────────


import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o6_")
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
    u = f"o6_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o6-pass", full_name="O6",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o6-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token") or ""


def _seed_nas(app, *, nas_id):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct', 'hr', 'p')""",
                (nas_id, f"o6-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )


def test_plan_route_renders_change_preview_block(app, client, monkeypatch):
    _seed_nas(app, nas_id=30)
    _login(client)
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether2", "type": "ether"}]))
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]))
    monkeypatch.setattr(
        mac, "ip_routes",
        lambda nas: MtResult(ok=True, data=[]))
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/30/program/plan",
        data={"_csrf_token": token,
              "kind": "hotspot",
              "interface": "ether2",
              "cidr": "192.168.10.0/24",
              "hotspot_name": "hs",
              "dns_servers": "8.8.8.8,1.1.1.1"},
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-change-preview" in html
    assert "data-mt-preview-additions" in html
