# -*- coding: utf-8 -*-
"""وصول عن بُعد — عرض العنوان (IP) والمنفذ الثابت مع أزرار النسخ.

يغطّي:
  • القالب يعرض بطاقة الاتصال بالعنوان + المنفذ + أزرار النسخ (data-rh-inv-copy)
    عند وجود جلسة نشطة، وكذلك حالة «المنفذ الثابت» بلا جلسة نشطة.
  • المنفذ يصبح ثابتًا لكل جهاز: stable_external_port حتميّ ومتجنّب للتصادم،
    و open_session يثبّته على الجهاز ويعيد استخدامه عبر كل فتح.
"""
import os
import tempfile

import pytest


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture(scope="module")
def app():
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "rac.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k",
        HOBERADIUS_VPS_PUBLIC_IP="187.77.70.18",
    )
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    return create_app()


def _seed_device(app, *, remote_ext_port=0):
    """Insert a router + one device; optionally pre-pin the port."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import network_devices_repo
        with transaction() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM nas_devices"
            ).fetchone()["c"]
            conn.execute(
                "INSERT INTO nas_devices "
                "(tenant_id, name, address, secret, vendor, nas_type, "
                " enabled, api_user, api_password, created_at) "
                "VALUES (1,?,'10.0.0.1','s','mikrotik','hotspot',1,"
                "'api','pw','2026-01-01')",
                (f"راوتر {n + 1}",),
            )
            router_id = conn.execute(
                "SELECT id FROM nas_devices ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
        dev_id = network_devices_repo.create(
            tenant_id=1, router_id=int(router_id), name=f"نقطة وصول {n + 1}",
            device_type="ap", ip_address="192.168.88.50", management_port=8291,
        )
        if remote_ext_port:
            network_devices_repo.set_remote_ext_port(1, dev_id, remote_ext_port)
        return network_devices_repo.get_by_id(1, dev_id)


def _render(app, **ctx):
    # render_template applies the app's context processors (get_locale,
    # text_dir, csrf, …) that the admin layout depends on — a bare
    # jinja_env.render would leave those Undefined.
    from flask import render_template
    with app.test_request_context():
        return render_template("radius/remote_device_access.html", **ctx)


# ───────────────────── القالب: بطاقة الاتصال ─────────────────────

def test_view_shows_ip_port_and_copy_when_session_active(app):
    device = _seed_device(app, remote_ext_port=40050)
    active = {
        "id": 7, "status": "active", "protocol": "winbox",
        "external_port": 40050, "expires_at": "2026-06-21 12:00:00",
        "created_at": "2026-06-21 11:00:00", "requested_by": "admin",
    }
    html = _render(
        app, device=device, nas={"name": "راوتر", "address": "10.7.0.1"},
        sessions=[active], active_session=active, fixed_port=40050,
        vps_public_host="187.77.70.18",
    )
    # العنوان والمنفذ ظاهران بشكل بارز.
    assert "187.77.70.18" in html
    assert "40050" in html
    assert "187.77.70.18:40050" in html
    # أزرار نسخ تستخدم عنصر تحكّم نظام التصميم (HobeCopy) لا alert.
    assert 'data-rh-inv-copy="187.77.70.18:40050"' in html
    assert 'data-rh-inv-copy="187.77.70.18"' in html
    assert 'data-rh-inv-copy="40050"' in html
    assert "copy_helper.js" in html
    # وسم الخدمة وينبوكس + بطاقة حيّة.
    assert "وينبوكس" in html
    assert "rac-card--live" in html
    # توست النسخ موصول (لا alert الأصلي).
    assert "UDS.toast" in html


def test_view_shows_fixed_port_when_no_active_session(app):
    device = _seed_device(app, remote_ext_port=40090)
    html = _render(
        app, device=device, nas={"name": "راوتر", "address": "10.7.0.1"},
        sessions=[], active_session=None, fixed_port=40090,
        vps_public_host="187.77.70.18",
    )
    # حتى بلا جلسة نشطة: العنوان والمنفذ الثابت + النسخ ظاهرة.
    assert "187.77.70.18:40090" in html
    assert 'data-rh-inv-copy="187.77.70.18:40090"' in html
    assert "rac-card--idle" in html


# ───────────────────── المنفذ الثابت لكل جهاز ─────────────────────

def test_stable_external_port_is_deterministic_and_collision_free(app):
    with app.app_context():
        from app.radius.db.repos import remote_access_sessions_repo as repo
        # حتميّ: نفس device_id ⇒ نفس المنفذ الأساسي.
        p1 = repo.stable_external_port(123)
        assert p1 == 40000 + (123 % 20000)
        # preferred يُحترم في next_free_external_port.
        assert repo.next_free_external_port(123, preferred=40555) == 40555


def test_open_session_pins_and_reuses_same_port(app, monkeypatch):
    from types import SimpleNamespace
    device = _seed_device(app)            # remote_ext_port = 0 (غير مثبّت)
    assert device["remote_ext_port"] == 0

    from app.radius.services import remote_device_access as svc

    monkeypatch.setattr(
        svc.mac, "_safe_dial",
        lambda **kw: SimpleNamespace(ok=True, error=""),
    )
    monkeypatch.setattr(
        svc.vps_port_proxy, "start_proxy", lambda **kw: (True, ""),
    )
    monkeypatch.setattr(svc.vps_port_proxy, "stop_proxy", lambda *a, **k: None)

    with app.app_context():
        from app.radius.db.repos import network_devices_repo

        ok, err, s1 = svc.open_session(
            nas={"address": "10.7.0.1"}, device=device,
            requested_by="t", ttl_minutes=30, protocol="winbox",
        )
        assert ok, err
        pinned = network_devices_repo.get_by_id(1, device["id"])["remote_ext_port"]
        assert pinned > 0
        assert s1["external_port"] == pinned

        # فتح ثانٍ على نفس الجهاز (بعد إغلاق الأول) ⇒ نفس المنفذ الثابت.
        svc.close_session(nas={"address": "10.7.0.1"}, session=s1)
        device2 = network_devices_repo.get_by_id(1, device["id"])
        ok2, err2, s2 = svc.open_session(
            nas={"address": "10.7.0.1"}, device=device2,
            requested_by="t", ttl_minutes=30, protocol="http",
        )
        assert ok2, err2
        assert s2["external_port"] == pinned
