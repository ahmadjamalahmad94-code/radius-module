"""O4 — fleet health summary cards on Operations Center."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o4_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "X" * 43 + "=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "1.2.3.4:51820")
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
    u = f"o4_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="o4-pass", full_name="O4 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o4-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed(app, nas_id: int, *, enabled: bool) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     created_at)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, 'hr-test', 'pw', ?)""",
                (nas_id, f"rt-{nas_id}", f"10.0.0.{nas_id}",
                 int(enabled), now),
            )


def test_fleet_summary_renders_with_zero_routers(app, client):
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    # The empty-state path doesn't render the KPI grid; OK, just
    # confirm no 500 + the empty-state message shows.
    assert "لا توجد راوترات" in html


def test_fleet_summary_renders_all_four_live_cards(app, client):
    _seed(app, 50, enabled=True)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    # Wrapper + each card by its data-mt-fleet attribute.
    assert "data-mt-fleet-summary" in html
    assert 'data-mt-fleet="connected"' in html
    assert 'data-mt-fleet="unreachable"' in html
    assert 'data-mt-fleet="partial"' in html
    assert 'data-mt-fleet="disabled"' in html


def test_fleet_summary_disabled_count_is_server_rendered(app, client):
    """The 'معطَّل' card pulls from the DB at render time so the
    operator sees the right number before JS even runs."""
    _seed(app, 51, enabled=True)
    _seed(app, 52, enabled=False)
    _seed(app, 53, enabled=False)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    # The disabled card's value is rendered server-side; the
    # other three cards start as em-dash and get filled by JS.
    import re
    m = re.search(
        r'data-mt-fleet="disabled".*?<div class="hub-kpi-value">\s*(\d+)\s*</div>',
        html, re.DOTALL,
    )
    assert m is not None, "disabled card value not found"
    assert m.group(1) == "2", \
        f"expected 2 disabled routers, found {m.group(1)}"


def test_fleet_summary_live_cards_start_em_dash(app, client):
    """العقد الجديد بعد إصلاح radacct:
      • بطاقة «متصل» تبدأ بعدّ radacct الموثوق (رقم) لا «—» — فهذا الرقم
        حقيقة فورية (جلسات RADIUS نشطة)، لا يَعتمد على أول استطلاع API.
      • بطاقتا «غير متصل»/«جزئي» مفهومان خاصّان بالـAPI فقط، فتبدآن «—»
        حتى يُجيب الاستطلاع.
    بلا أي جلسة نشطة (لم تُزرع radacct) ⇒ «متصل» = 0 (صفر صادق)."""
    _seed(app, 60, enabled=True)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    import re
    # بطاقة «متصل» مزروعة من radacct (رقم) لا «—».
    assert 'data-mt-radacct-connected="0"' in html
    conn = re.search(
        r'data-mt-fleet="connected"[^>]*>.*?data-mt-fleet-value[^>]*>\s*([^<\s]+)\s*</div>',
        html, re.S)
    assert conn and conn.group(1).isdigit(), \
        f"connected card must start with a radacct number, got {conn and conn.group(1)}"
    # بطاقتا API فقط (غير متصل/جزئي) ما زالتا تبدآن «—».
    matches = re.findall(r'data-mt-fleet-value[^>]*>\s*([^<\s]+)\s*</div>', html)
    assert sum(1 for v in matches if v == "—") >= 2, \
        f"expected ≥2 em-dash (unreachable/partial), got {matches}"
