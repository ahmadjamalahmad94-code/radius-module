"""public_ip service card → real CoA set-IP path (follow-up to the merged
CoA engine).

These tests prove the «public_ip» / «تغيير IP الخروج» card on the router
dashboard is no longer a dead placeholder: its primary action is a real
deep-link to the per-session CoA set-IP flow, and the sessions page
honours the deep-link by showing a one-shot banner pointing at the ↻
button (which sends an actual CoA-Request with Framed-IP-Address via the
already-merged change_ip_live engine).

What we assert:
  1. The card renders even when the service is paid/disabled
     (regression of #17's spirit — card always renders).
  2. The card carries the CoA primary action (pill href → online_list
     with nas=<address> and hint=coa_setip).
  3. The legacy panel-side request modal is preserved as a secondary
     action (the «طلب سقف» pill + data-svc-spec-modal-open still
     present — we did NOT delete the existing request flow).
  4. GET /admin/radius/online?nas=<addr>&hint=coa_setip renders the
     coa-setip hint banner.
  5. GET /admin/radius/online WITHOUT the hint does NOT render the
     banner (no banner pollution on every visit).
  6. The CoA support matrix is still honest: change_ip_live on a hotspot
     session returns the unsupported surface (no packet on the wire).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


# ── shared fixtures (mirrors tests/test_mt_dashboard_ui.py) ─────────


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_coa_card_")
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
    username = f"coa_card_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username, password="pw",
        full_name="CoA Card Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "pw"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_router(app, *, nas_id: int, name: str = "edge-gw",
                 address: str = "203.0.113.42") -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                VALUES (?, 1, ?, ?, 's', 'mikrotik', 'hotspot', 1,
                        ?, 'direct')
                """,
                (nas_id, name, address, now),
            )


# ── 1+2+3: card renders + CoA deep-link + secondary spec modal ─────


def test_public_ip_card_renders_with_coa_deeplink_and_legacy_request(app, client):
    """The «public_ip» card MUST:
      (a) still render (#17's spirit — paid/disabled doesn't hide it);
      (b) carry a real CoA primary action — an <a> with
          data-rh-coa-setip and href to /online?nas=<addr>&hint=coa_setip;
      (c) keep the legacy «request the paid quota» modal pill as a
          secondary action (we did NOT replace one path with the other).
    """
    _seed_router(app, nas_id=77, name="edge-gw", address="203.0.113.42")
    _login(client)

    res = client.get("/admin/radius/mt/77/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # (a) public-IP card still rendered (now clearly labelled «Public»,
    #     after the internal-vs-public separation — June 2026)
    assert 'data-rh-svc-card="public-ip"' in html
    assert "تغيير عنوان التصفح العام (Public)" in html
    assert "مدفوعة" in html  # paid badge still there
    # the distinct, FREE internal-session CoA card is now its own surface
    assert "تغيير IP الجلسة الداخلية (CoA)" in html

    # (b) CoA action — the real deep-link now lives on the INTERNAL card
    assert "data-rh-coa-setip" in html, "missing CoA deep-link on the internal-session card"
    # Find the <a data-rh-coa-setip ...> tag and assert its href carries
    # both query params (nas filter + the hint). The HTML escaper may turn
    # `&` into `&amp;` so we accept either.
    import re as _re
    m = _re.search(r'<a[^>]*\bdata-rh-coa-setip\b[^>]*>', html)
    assert m, "expected an <a> element carrying data-rh-coa-setip"
    pill_tag = m.group(0)
    href_m = _re.search(r'href="([^"]+)"', pill_tag)
    assert href_m, f"no href on the CoA pill: {pill_tag!r}"
    href = href_m.group(1).replace("&amp;", "&")
    assert "/admin/radius/online" in href, \
        f"CoA pill must point at the sessions list — href={href!r}"
    assert "nas=203.0.113.42" in href, \
        f"CoA pill must filter the sessions list to this router's address — href={href!r}"
    assert "hint=coa_setip" in href, \
        f"CoA pill must carry hint=coa_setip — href={href!r}"
    # And the visible label is the real action (not a generic 'activate'):
    assert "تطبيق IP حيّ" in html

    # (c) legacy panel-side request preserved as secondary action
    assert 'data-svc-type="public-ip"' in html
    assert 'data-svc-action="activate"' in html
    # Spec modal markup must still be included on the page
    assert 'data-ssm-modal' in html


# ── 4+5: sessions page renders the banner only with the hint ──────


def test_online_page_with_hint_renders_coa_setip_banner(app, client):
    _seed_router(app, nas_id=78, name="banner-gw", address="203.0.113.78")
    _login(client)

    res = client.get(
        "/admin/radius/online?nas=203.0.113.78&hint=coa_setip"
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Banner anchor + label
    assert "coa-setip-hint-banner" in html, \
        "the deep-link's hint=coa_setip must render the helper banner"
    assert "تطبيق IP حيّ عبر CoA" in html
    # Hotspot mention must be present — owner's rule: never fake support
    assert "Hotspot" in html or "hotspot" in html


def test_online_page_without_hint_does_not_render_banner(app, client):
    _seed_router(app, nas_id=79, name="no-banner-gw", address="203.0.113.79")
    _login(client)

    res = client.get("/admin/radius/online")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "coa-setip-hint-banner" not in html, \
        "the banner must only render when hint=coa_setip is on the URL"


# ── 6: hotspot still gets the unsupported surface ─────────────────


def test_change_ip_live_still_surfaces_unsupported_for_hotspot():
    """The hookup must NOT bypass the support-matrix guard — a hotspot
    session for the same router still returns the «unsupported» outcome
    without sending a packet."""
    from app.radius.services.live_session_control import change_ip_live
    hotspot_session = {
        "nas_ip": "127.0.0.1",
        "nas_secret": "secret-for-hotspot-test",
        "coa_port": 3799,
        "session_id": "80FF0042",
        "framed_ip": "192.168.10.55",
        "calling_station_id": "11:22:33:44:55:66",
        "nasporttype": "Ethernet",  # hotspot
    }
    out = change_ip_live(
        tenant_id=1, username="hotspot_user",
        new_ip="192.168.10.99",
        session_id=hotspot_session["session_id"],
        session_row=hotspot_session,
    )
    assert out.ok is False
    assert out.code_name == "unsupported"
    assert "hotspot" in out.detail.lower()
    assert out.session_type == "hotspot"
