"""Smart Alerts phase 3 — DHCP-client loop detection (probe ingest + alert)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_loop_")
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
    u = f"lp_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="lp-pass",
                             full_name="Loop", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "lp-pass"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _seed_router(rid: int = 77, name: str = "راوتر الفرع") -> int:
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO nas_devices(id, tenant_id, name, address, secret,
                vendor, nas_type, enabled, created_at, connection_mode)
            VALUES(?,1,?,?,'sek','mikrotik','hotspot',1,?,'direct')
            """,
            (rid, name, f"10.0.0.{rid}", datetime.utcnow().isoformat()),
        )
    return rid


def _ingest(client, rid, probes):
    return client.post(f"/api/v1/routers/{rid}/loop/ingest",
                       json={"probes": probes}, headers=AUTH)


def test_loop_ingest_stores_probe_and_opens_alert_when_bound(app, client):
    with app.app_context():
        _seed_router(77)
    res = _ingest(client, 77, [
        {"interface": "ether2", "status": "bound",
         "address": "10.0.0.7/24", "server": "10.0.0.1"},
        {"interface": 7, "status": "searching",
         "address": "", "server": 0},
    ])
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["probes_recorded"] == 2
    with app.app_context():
        from app.radius.db.repos import alerts_repo, router_loop_probes_repo
        probes = router_loop_probes_repo.list_for_router(1, 77)
        by_iface = {p["interface"]: p for p in probes}
        assert by_iface["ether2"]["last_status"] == "bound"
        assert {p["interface"] for p in probes} == {"ether2", "7"}
        open_alerts = {a["dedup_key"]: a for a in alerts_repo.list_open(1)}
        assert "auto.router.loop:77:ether2" in open_alerts
        # the loop IP is surfaced in the alert
        assert "10.0.0.7" in open_alerts["auto.router.loop:77:ether2"]["explanation_ar"]


def test_loop_resolves_when_probe_back_to_searching(app, client):
    with app.app_context():
        _seed_router(77)
    _ingest(client, 77, [{"interface": "ether2", "status": "bound",
                          "address": "10.0.0.7/24", "server": "10.0.0.1"}])
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        assert "auto.router.loop:77:ether2" in {a["dedup_key"] for a in alerts_repo.list_open(1)}

    # probe goes back to searching (no lease) → loop cleared
    _ingest(client, 77, [{"interface": "ether2", "status": "searching",
                          "address": "", "server": ""}])
    with app.app_context():
        from app.radius.db.repos import alerts_repo
        assert "auto.router.loop:77:ether2" not in {a["dedup_key"] for a in alerts_repo.list_open(1)}


def test_loop_ingest_unknown_router_is_404(app, client):
    res = _ingest(client, 9999, [{"interface": "ether2", "status": "bound"}])
    assert res.status_code == 404


def test_loop_setup_page_renders(app, client):
    with app.app_context():
        _seed_router(77, name="راوتر الفرع")
    _login(client)
    html = client.get("/admin/radius/alerts/loop-setup").get_data(as_text=True)
    assert "/loop/ingest" in html        # generated script targets the endpoint
    assert "راوتر الفرع" in html          # router picker option
    assert "lp-script-body" in html       # script panel present


def test_my_services_tab_has_loop_tracking_tile(app, client):
    """The «خدماتي» tab shows a «تتبّع اللوب» tile that opens the loop page."""
    with app.app_context():
        _seed_router(77, name="راوتر الفرع")
    _login(client)
    html = client.get("/admin/radius/mt/77/dashboard").get_data(as_text=True)
    assert "data-rh-loop-tile" in html                              # the tile
    assert "تتبّع اللوب" in html                                     # title
    assert "كشف اللوب عبر مجس DHCP على منافذ الزبائن" in html        # description
    assert "/admin/radius/alerts/loop-setup" in html                # links to loop page
    # no probes for this router yet → غير مفعّل badge
    assert "غير مفعّل" in html
