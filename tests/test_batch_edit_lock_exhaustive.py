"""EXHAUSTIVE coverage — editing an existing card-batch.

Proves the REAL server-side behaviour:
  * edit is owner-only (sub-manager 403 on GET and POST, even with the nav perm);
  * EVERY structural / card-build field is rejected when changed AND never
    persisted (parametrised over the whole STRUCTURAL_LOCKED_FIELDS set),
    with the already-generated card rows left untouched;
  * EVERY commercial / assignment field is genuinely editable by the owner
    (parametrised), persisting to the batch row;
  * the central service drop applies to any caller (not just the route).
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "batch_edit_exhaustive.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _plan_id(name="باقة") -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name=f"M {username}", is_super_admin=False)
    return int(adm.id)


def _make_batch(plan_id: int, *, count: int = 4):
    from app.radius.services.cards import get_cards_service

    batch, cards = get_cards_service().generate_batch(
        actor="test", plan_id=plan_id, count=count,
        username_length=8, password_length=6, password_charset="digits",
        password_generation_type="medium", price_per_card=2.0,
        time_value=1, time_unit="days",
    )
    return batch, cards


def _col(batch_id: int, col: str):
    return db().execute(f"SELECT {col} FROM card_batches WHERE id=?", (batch_id,)).fetchone()[col]


def _card_usernames(batch_id: int):
    return [r["username"] for r in db().execute(
        "SELECT username FROM cards WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()]


def _login(client, *, admin_id: int, is_super: bool, perms=("cards.view", "cards.edit_batch")):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = list(perms)


def _edit(client, batch_id, **fields):
    data = {"_csrf_token": "off-csrf"}
    data.update({k: str(v) for k, v in fields.items()})
    return client.post(f"/admin/radius/cards/batches/{batch_id}/edit", data=data,
                       follow_redirects=False)


# ═══ owner-only gate ════════════════════════════════════════════════════════
def test_sub_manager_get_edit_403(app):
    with app.app_context():
        plan = _plan_id(); b, _ = _make_batch(plan); bid = b.id
        mgr = _sub_admin("m1")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        assert c.get(f"/admin/radius/cards/batches/{bid}/edit").status_code == 403


def test_sub_manager_post_edit_403_and_no_change(app):
    with app.app_context():
        plan = _plan_id(); b, _ = _make_batch(plan); bid = b.id
        mgr = _sub_admin("m2")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        res = _edit(c, bid, plan_id=plan, count=b.count, price_per_card="9.99")
    assert res.status_code == 403
    with app.app_context():
        assert float(_col(bid, "price_per_card")) == 2.0


def test_owner_get_edit_200(app):
    with app.app_context():
        plan = _plan_id(); b, _ = _make_batch(plan); bid = b.id
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        assert c.get(f"/admin/radius/cards/batches/{bid}/edit").status_code == 200


def test_edit_button_hidden_in_list_for_sub_manager(app):
    with app.app_context():
        plan = _plan_id(); _make_batch(plan)
        mgr = _sub_admin("m3")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False, perms=("cards.view",))
        html = c.get("/admin/radius/cards/batches?status=all").get_data(as_text=True)
    assert "/edit" not in html
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        html = c.get("/admin/radius/cards/batches?status=all").get_data(as_text=True)
    assert "/edit" in html


# ═══ EVERY structural field: rejected on change, never persisted ════════════
# The stored value is generation-derived (e.g. password_charset follows the
# generation type), so we read it at runtime and post a guaranteed-different
# value rather than hard-coding it.
STRUCTURAL_FIELDS = [
    "count", "username_length", "password_length", "password_charset",
    "password_generation_type", "username_prefix", "username_suffix",
    "include_batch_number", "starts_with_or_ends_with", "prefix_or_suffix_value",
]


def _different_value(field, stored):
    s = "" if stored is None else str(stored)
    if field in {"count", "username_length", "password_length"}:
        return str(int(stored or 0) + 7)
    if field == "password_charset":
        return "letters" if s != "letters" else "digits"
    if field == "password_generation_type":
        return "strong" if s != "strong" else "digits"
    if field == "starts_with_or_ends_with":
        return "prefix" if s != "prefix" else "suffix"
    if field == "include_batch_number":
        return "0" if s in {"1", "True", "true"} else "1"
    return (s or "") + "ZZ"   # string prefix/suffix/value


@pytest.mark.parametrize("field", STRUCTURAL_FIELDS)
def test_structural_field_change_rejected(app, field):
    with app.app_context():
        plan = _plan_id(); b, _ = _make_batch(plan); bid = b.id
        stored = _col(bid, field)
        changed = _different_value(field, stored)
        before_cards = _card_usernames(bid)
    kw = {field: changed} if field == "count" else {"count": b.count, field: changed}
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        res = _edit(c, bid, plan_id=plan, **kw)
    # a changed structural value is rejected → re-render (200), not a redirect.
    assert res.status_code == 200
    with app.app_context():
        assert str(_col(bid, field)) == str(stored)        # unchanged
        assert _card_usernames(bid) == before_cards        # cards untouched


def test_all_structural_fields_covered(app):
    # guard: the parametrised set must cover every locked field that can carry a
    # distinct posted value (random_generation_enabled is forced True upstream).
    from app.radius.services.cards import STRUCTURAL_LOCKED_FIELDS

    covered = set(STRUCTURAL_FIELDS) | {"random_generation_enabled"}
    assert set(STRUCTURAL_LOCKED_FIELDS) == covered


# ═══ EVERY commercial / assignment field: genuinely editable ════════════════
def _commercial_cases(plan2, mgr):
    # (post_fields, column, expected)
    return [
        ({"plan_id": plan2}, "plan_id", plan2),
        ({"plan_id": None, "price_per_card": "7.50"}, "price_per_card", 7.5),
        ({"plan_id": None, "price_bulk": "3.25"}, "price_bulk", 3.25),
        ({"plan_id": None, "time_value": "5"}, "time_value", 5),
        ({"plan_id": None, "time_value": "5", "time_unit": "hours"}, "time_unit", "hours"),
        ({"plan_id": None, "count_by_seconds": "1", "validity_after_first_login_days": "3"},
         "count_by_seconds", 1),
        ({"plan_id": None, "validity_after_first_login_days": "9"},
         "validity_after_first_login_days", 9),
        ({"plan_id": None, "on_quota_exhaust": "block"}, "on_quota_exhaust", "block"),
        ({"plan_id": None, "manager_id": mgr}, "manager_id", mgr),
        ({"plan_id": None, "status": "revoked"}, "status", "revoked"),
        ({"plan_id": None, "service_name": "svc-x"}, "service_name", "svc-x"),
        ({"plan_id": None, "package_name": "pkg-x"}, "package_name", "pkg-x"),
        ({"plan_id": None, "notes": "note-x"}, "notes", "note-x"),
        ({"plan_id": None, "total_quota_mb": "2048"}, "total_quota_mb", 2048),
        ({"plan_id": None, "device_count": "3"}, "device_count", 3),
    ]


@pytest.mark.parametrize("idx", range(15))
def test_commercial_field_editable(app, idx):
    with app.app_context():
        plan = _plan_id(); plan2 = _plan_id("بديلة")
        b, _ = _make_batch(plan); bid = b.id
        mgr = _sub_admin(f"cm{idx}")
        post, col, expected = _commercial_cases(plan2, mgr)[idx]
        # default plan_id to the batch's own plan when the case doesn't change it.
        if post.get("plan_id") is None:
            post = {**post, "plan_id": plan}
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        res = _edit(c, bid, count=b.count, **post)
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:200]
    with app.app_context():
        val = _col(bid, col)
        if isinstance(expected, float):
            assert abs(float(val) - expected) < 0.001
        else:
            assert str(val) == str(expected)


def test_total_price_derived_from_count_times_price(app):
    with app.app_context():
        plan = _plan_id(); b, _ = _make_batch(plan, count=4); bid = b.id
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        res = _edit(c, bid, plan_id=plan, count=b.count, price_per_card="3.00")
    assert res.status_code in (302, 303)
    with app.app_context():
        # total = count(4) × price(3.00); never a manual input.
        assert abs(float(_col(bid, "total_price")) - 12.0) < 0.001


# ═══ central service drop — any caller, not just the route ══════════════════
@pytest.mark.parametrize("field", STRUCTURAL_FIELDS + ["random_generation_enabled"])
def test_service_update_batch_drops_structural(app, field):
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id(); b, _ = _make_batch(plan); bid = b.id
        stored = _col(bid, field)
        changed = _different_value(field, stored) if field != "random_generation_enabled" else 0
        # even a direct service call cannot mutate structure; commercial applies.
        get_cards_service().update_batch(actor="t", batch_id=bid,
                                         data={field: changed, "price_per_card": 5.0})
        assert str(_col(bid, field)) == str(stored)        # structural untouched
        assert abs(float(_col(bid, "price_per_card")) - 5.0) < 0.001  # commercial applied
