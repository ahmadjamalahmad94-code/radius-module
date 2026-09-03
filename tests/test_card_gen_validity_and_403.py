"""Live-QA regressions:

BUG 1 — card-package GENERATION with validity "0 = use plan validity" (or a
blank time) must succeed and inherit the plan duration. The embedded speed
panel always submits hidden zero-default speed fields, which used to make every
generation try to create a bandwidth schedule with blank HH:MM times → the time
validator rejected the whole package («يجب إدخال الوقت بصيغة ساعة:دقيقة (HH:MM)»).
A speed schedule is only requested when an actual clock time is entered; an
explicit bad clock time must still error.

BUG 2 — a guarded write by an admin without permission must return a friendly
in-panel 403 (HTML) / clean JSON 403 (AJAX), not the bare werkzeug page — while
keeping the 403 status (enforcement unchanged).

Fixture/auth pattern mirrors tests/test_card_offers.py.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_gen_403.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


_PLAN_VALIDITY_DAYS = 7


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Plan7d", 60, _PLAN_VALIDITY_DAYS, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"M {username}",
        is_super_admin=False,
    )
    return int(adm.id)


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["permissions"] = []
        sess["_csrf_token"] = "off-csrf"


def _latest_batch():
    row = db().execute(
        "SELECT * FROM card_batches ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# The embedded speed panel ALWAYS submits these hidden zero-defaults
# (unit_input_picker → <input hidden value="0">). They must not, by themselves,
# trigger a phantom speed-schedule creation.
_PANEL_HIDDEN = {
    "sr_speed_down_kbps": "0",
    "sr_speed_up_kbps": "0",
    "sr_starts_at_time": "",
    "sr_ends_at_time": "",
    "sr_priority": "5",
}


def _gen(client, plan_id, *, extra=None):
    data = {
        "_csrf_token": "off-csrf",
        "plan_id": str(plan_id),
        "count": "2",
        "username_prefix": "rx",
        "password_length": "8",
        "device_count": "1",
    }
    data.update(_PANEL_HIDDEN)
    if extra:
        data.update(extra)
    return client.post(
        "/admin/radius/cards/generate", data=data, follow_redirects=False
    )


# ── BUG 1 ─────────────────────────────────────────────────────────────────


def test_validity_zero_succeeds_and_inherits_plan(app):
    """validity=0 + the panel's hidden speed defaults → batch created, expiry
    inherited from the plan's validity_days (NOT rejected with an HH:MM error)."""
    with app.app_context():
        plan_id = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _gen(client, plan_id, extra={"time_limit_minutes": "0"})
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:400]
    with app.app_context():
        batch = _latest_batch()
    assert batch is not None
    # جوهرُ هذا الاختبار أعلاه: التوليدُ **نجح** ولم يسقط على مُتحقّق HH:MM.
    #
    # أمّا شكلُ النافذة فتغيّر عمدًا بعد «#20» ووراثةِ زمن العرض، والسطورُ
    # القديمة هنا كانت تنتظر سلوكَ ما قبلَه فبقيت حمراءَ بلا عطبٍ حقيقيّ:
    #   • لا مدّةَ صريحةً من الشاشة ⇒ تُورَّث «مدّةُ الوقت» من العرض
    #     (‏duration_minutes = 60 ⇒ ساعةٌ واحدة) بدل أن تبقى الحزمةُ بلا نافذة.
    #   • والانتهاءُ **لا يُختم لحظةَ التوليد** بل عند أوّل دخول — وإلّا ماتت
    #     البطاقةُ وهي في الدرج. (‏validity_days للخطّة لم يَعُد يُختم هنا.)
    assert int(batch["time_value"]) == 1
    assert str(batch["time_unit"]) == "hours"
    assert not batch["expire_at"], (
        "خُتم انتهاءٌ لحظةَ التوليد — هذا يقتل البطاقةَ في الدرج")


def test_validity_blank_succeeds(app):
    with app.app_context():
        plan_id = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _gen(client, plan_id, extra={"time_limit_minutes": ""})
    assert res.status_code in (302, 303)
    with app.app_context():
        assert _latest_batch() is not None


def test_no_phantom_speed_schedule_created(app):
    """The hidden zero-default speed fields must NOT create a bandwidth
    schedule for the batch."""
    with app.app_context():
        plan_id = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _gen(client, plan_id, extra={"time_limit_minutes": "0"})
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services.operations import get_operations_service

        batch = _latest_batch()
        sched = get_operations_service().list_bandwidth_schedules(
            tenant_id=1, target_type="card_batch",
            card_batch_id=int(batch["id"]), limit=50,
        )
    assert sched == [] or len(sched) == 0


def test_explicit_bad_clock_time_still_errors(app):
    """An explicitly-entered malformed clock time must STILL be rejected — we
    only skipped the check for the blank/sentinel case, not for real input."""
    with app.app_context():
        plan_id = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _gen(client, plan_id, extra={
            "time_limit_minutes": "0",
            # An explicit (bad) clock time → the time validator must fire.
            "sr_starts_at_time": "notatime",
            "sr_ends_at_time": "08:00",
            "sr_speed_down_kbps": "5000",
        })
    # Re-rendered the form (200) with the HH:MM validation error.
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "HH:MM" in body


def test_valid_speed_schedule_still_created(app):
    """A genuinely-entered time window still creates the schedule (no regression
    to the legitimate speed-rule path)."""
    with app.app_context():
        plan_id = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _gen(client, plan_id, extra={
            "time_limit_minutes": "0",
            "sr_name": "مساء",
            "sr_starts_at_time": "20:00",
            "sr_ends_at_time": "23:00",
            "sr_speed_down_kbps": "5000",
            "sr_speed_up_kbps": "1000",
        })
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:400]
    with app.app_context():
        from app.radius.services.operations import get_operations_service

        batch = _latest_batch()
        sched = get_operations_service().list_bandwidth_schedules(
            tenant_id=1, target_type="card_batch",
            card_batch_id=int(batch["id"]), limit=50,
        )
    assert len(sched) == 1
    assert sched[0]["starts_at_time"] == "20:00"
    assert sched[0]["ends_at_time"] == "23:00"


# ── BUG 2 ─────────────────────────────────────────────────────────────────


def test_friendly_403_html_in_panel_chrome(app):
    """A non-super admin editing an admin (super-only write) gets a friendly
    in-panel 403, not the bare werkzeug page — status stays 403."""
    with app.app_context():
        mgr = _sub_admin("limited")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(
            f"/admin/radius/admins/{mgr}",
            data={"_csrf_token": "off-csrf", "username": "x"},
            follow_redirects=False,
        )
    assert res.status_code == 403
    assert "text/html" in (res.headers.get("Content-Type") or "")
    body = res.get_data(as_text=True)
    # Owner-approved wording (both lines).
    assert "ليس لديك صلاحية الوصول إلى هذه الصفحة" in body
    assert "إذا كنت تتوقع أن هذا خلل، راجع الإدارة." in body
    # Rendered inside panel chrome (the friendly forbidden page), not raw werkzeug.
    assert "data-mt-forbidden-page" in body
    assert "You don't have the permission" not in body


def test_friendly_403_json_for_ajax(app):
    with app.app_context():
        mgr = _sub_admin("limited")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(
            f"/admin/radius/admins/{mgr}",
            data={"_csrf_token": "off-csrf", "username": "x"},
            headers={"X-Requested-With": "fetch", "Accept": "application/json"},
        )
    assert res.status_code == 403
    assert "application/json" in (res.headers.get("Content-Type") or "")
    payload = res.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "ليس لديك صلاحية الوصول إلى هذه الصفحة"
