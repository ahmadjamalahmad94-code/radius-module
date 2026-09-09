# -*- coding: utf-8 -*-
"""«وقت اليوم» on the SUBSCRIBERS LIST (/admin/radius/subscribers) renders the
same thirds-colored used/total pill as the connected views (owner: «اعملها
وقت اليوم بالمشتركين نفس الي عملناه بالمتصلين»).

Same shared pieces, so the logic cannot diverge: data from
services/online_time_budget.day_time_cells (subscriber path — effective daily
cap, else total limit, else unlimited), pill component .du-pill in hub_v2.css,
bidi-safe Latin token in .du-cell.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ulpill_")
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


def _elapsed_today_sec() -> int:
    """الثواني المنقضية من اليوم المحلّيّ — وهي سقفُ ما يُحتسَب لأيّ جلسة.

    يُتخطّى الاختبارُ في أوّل خمسِ دقائقَ بعد منتصف الليل: النافذةُ أضيقُ من
    أن تُميّز أثلاثًا، والتخطّي أصدقُ من توكيدٍ يترنّح.
    """
    import pytest as _pytest
    from calendar import timegm
    from app.radius.services.policy_engine import _local_day_start_utc
    from app.radius.services.device_limit import _parse_acct_dt
    start = _parse_acct_dt(_local_day_start_utc(1))
    now_ = _dt.datetime.utcnow()
    elapsed = int(timegm(now_.timetuple()) - timegm(start.timetuple()))
    if elapsed < 300:
        _pytest.skip("أقلُّ من خمس دقائقَ منذ منتصف الليل المحلّيّ")
    return elapsed


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat() + "Z"


@pytest.fixture
def seeded(app):
    """Three subscribers: green (30m of a 3h daily cap), red (2h30m of 3h),
    and uncapped (neutral «/ ∞»).

    The test admin is created FIRST and the subscribers carry
    ``manager_id=<that admin>`` so they stay visible under the list's
    owner-scoping (only the root/owner admin sees unowned rows — the
    same pattern as test_users_list_state_effect)."""
    from uuid import uuid4
    from app.radius.db.repos import admins_repo
    now = _dt.datetime.utcnow()
    with app.app_context():
        u = f"ul_{uuid4().hex[:10]}"
        admin = admins_repo.create_admin(
            username=u, password="ul-pass", full_name="UL Tester",
            is_super_admin=True)
        admin_id = int(getattr(admin, "id", 0) or 0)
        app._ul_login_user = u  # type: ignore[attr-defined]
        from app.radius.db.connection import transaction
        with transaction() as c:
            def sub(username, *, daily_min=0):
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, password, "
                    "daily_connection_time_min, connection_time_limit_enabled, "
                    "manager_id, created_at) VALUES (1,?,?,?,?,?,?)",
                    (username, "pw", daily_min, 1 if daily_min else 0,
                     admin_id, _iso(now)))

            def usage(username, seconds):
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, "
                    "acctuniqueid, username, nasipaddress, acctstarttime, "
                    "acctupdatetime, acctsessiontime) VALUES (1,?,?,?,?,?,?,?)",
                    (f"s-{username}", f"u-{username}", username, "10.10.0.2",
                     _iso(now), _iso(now), int(seconds)))

            # 🔴 «وقت اليوم» **مُقيَّدٌ بالمنقضي منذ منتصف الليل المحلّي**:
            # جلسةٌ طولُها ساعتان ونصفٌ تُحتسَب ٤٧ دقيقةً إن كانت الساعةُ
            # ٠٠:٤٧. فأرقامٌ ثابتةٌ (1800/9000) تجعل الاختبارَ يفشل كلَّ ليلةٍ
            # بين منتصف الليل والثانية والنصف — عطبٌ في الاختبار لا في الشيفرة.
            # فنشتقُّ الأرقامَ من المنقضي نفسِه لتكون النسبةُ ثابتةً أيّ ساعةٍ كان.
            elapsed = _elapsed_today_sec()
            cap_min = max(1, elapsed // 60)
            sub("ul-green", daily_min=cap_min)
            usage("ul-green", int(elapsed * 0.05))   # ٥٪ من السقف → أخضر
            sub("ul-red", daily_min=cap_min)
            usage("ul-red", int(elapsed * 0.95))     # ٩٥٪ من السقف → أحمر
            sub("ul-free")
            usage("ul-free", int(elapsed * 0.2))     # بلا سقف → محايد
    return app


def _login(app):
    client = app.test_client()
    u = getattr(app, "_ul_login_user", None)
    assert u, "seeded fixture must run first (it mints the scoped admin)"
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "ul-pass"})
    assert res.status_code in {302, 303}
    return client


def _pill_for(body: str, username: str) -> str:
    m = re.search(
        r'data-username="%s".*?data-col="daily_used".*?'
        r'(?:<span class="du-pill (du-[a-z]+)"|—)' % re.escape(username),
        body, re.S)
    assert m, f"daily_used cell for {username} not found"
    return m.group(1) or ""


def test_users_list_renders_thirds_pills(seeded):
    body = _login(seeded).get(
        "/admin/radius/subscribers").get_data(as_text=True)
    assert _pill_for(body, "ul-green") == "du-green"
    assert _pill_for(body, "ul-red") == "du-red"
    assert _pill_for(body, "ul-free") == "du-neutral"
    # الصيغةُ اللاتينيّةُ المشتركةُ الآمنةُ ثنائيَّ الاتّجاه («30m / 3h»)
    # تبقى داخل الرقاقة. والأرقامُ مشتقّةٌ من المنقضي فلا تُوكَّد حرفيًّا —
    # يُوكَّد **شكلُها**: مدّةٌ ثمّ « / » ثمّ مدّة.
    assert re.search(r'du-pill du-red">.{0,240}?[0-9].{0,40}?/.{0,40}?[0-9]',
                     body, re.S), "صيغةُ «مستهلَك / سقف» تغيّرت"
    assert "∞" in body


def test_pill_component_is_central_not_inline(seeded):
    """The pill must come from hub_v2.css (shared with /online), not a page
    copy — so the two screens can never drift apart visually."""
    css_path = os.path.join(os.path.dirname(__file__), "..", "app", "static",
                            "css", "hub_v2.css")
    with open(css_path, encoding="utf-8") as fh:
        css = fh.read()
    assert ".du-pill.du-green" in css and ".du-pill.du-neutral" in css
    for tpl in ("users_list.html", "sessions_list.html"):
        path = os.path.join(os.path.dirname(__file__), "..", "app",
                            "templates", "radius", tpl)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert ".du-pill.du-green" not in src, f"{tpl} carries an inline copy"
