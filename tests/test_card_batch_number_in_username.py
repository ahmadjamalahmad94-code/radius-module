# -*- coding: utf-8 -*-
"""«تضمين رقم الحزمة» (include_batch_number) — كان يُحفظ ولا يُطبَّق (2026-09-24).

قرار المالك: مفتاحٌ اختياريّ **معطَّل افتراضيًّا**؛ عند تفعيله يُضاف رقم الحزمة
**أرقامًا فقط** (بلا «B» ولا فاصل) **بعد البادئة وقبل الأرقام العشوائيّة**:
البادئة «15» + الحزمة 42 + عشوائيّ + اللاحقة ⇒ «1542xxxx…». يُحتسب ضمن
username_length الكلّيّ (MT80، العشوائيّ ≥ 1)، والمعاينة تُظهره، والتفرّد
محفوظ، والبطاقات الموجودة لا تُمسّ.
"""
from __future__ import annotations

import itertools
import os
import re

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "affix_digits.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    return flask_app


_seq = itertools.count(1)


def _db():
    from app.radius.db.connection import db
    return db()


def _plan_id() -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,?,60,1,1.0,'ILS',2048,2048,0,"
        "datetime('now'),datetime('now'))", ("باقة-%d" % next(_seq),))
    return int(cur.lastrowid)


def _login(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "owner_root"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "affix-csrf"


def _cards_of_latest_batch():
    row = _db().execute("SELECT id FROM card_batches ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None, "لم تُنشأ حزمة"
    return [dict(r) for r in _db().execute(
        "SELECT username, password FROM cards WHERE batch_id = ?", (row["id"],))]


def _gen(**kw):
    from app.radius.services.cards import get_cards_service
    kw.setdefault("actor", "admin")
    kw.setdefault("package_name", "رقم-الحزمة")
    batch, _ = get_cards_service().generate_batch(plan_id=_plan_id(), **kw)
    names = [r["username"] for r in _db().execute(
        "SELECT username FROM cards WHERE batch_id = ?", (batch.id,))]
    return batch, names


def test_off_by_default_no_batch_number(app):
    with app.app_context():
        batch, names = _gen(count=6, username_prefix="15", username_length=8)
    assert batch.include_batch_number is False
    assert all(re.fullmatch(r"15[0-9]{6}", n) for n in names), names


def test_on_puts_digits_only_batch_id_after_prefix_before_random(app):
    with app.app_context():
        _gen(count=1, username_length=8)          # الحزمة الأولى ⇒ الثانية id=2+
        batch, names = _gen(count=10, username_prefix="15", username_suffix="99",
                            username_length=12, include_batch_number=True)
    bn = str(batch.id)
    assert batch.include_batch_number is True
    rand = 12 - len("15") - len(bn) - len("99")
    pat = re.compile(r"^15%s[0-9]{%d}99$" % (bn, rand))
    assert all(pat.fullmatch(n) for n in names), (pat.pattern, names)
    assert all("b" not in n and "-" not in n for n in names)
    assert len(set(names)) == len(names) == 10


def test_batch_number_counts_toward_total_length_min_one_random(app):
    with app.app_context():
        batch, names = _gen(count=1, username_prefix="12345", username_length=4,
                            include_batch_number=True)
    assert names == [n for n in names if re.fullmatch(r"12345%s[0-9]" % batch.id, n)], names


def test_unique_across_batches_and_existing_cards_untouched(app):
    with app.app_context():
        old, old_names = _gen(count=5, username_length=8)
        new, new_names = _gen(count=30, username_length=8, include_batch_number=True)
        still = sorted(r["username"] for r in _db().execute(
            "SELECT username FROM cards WHERE batch_id = ?", (old.id,)))
    assert still == sorted(old_names)                       # القديمة كما هي
    assert all(re.fullmatch(r"[0-9]{8}", n) for n in old_names)
    assert all(n.startswith(str(new.id)) for n in new_names)
    assert len(set(new_names) | set(old_names)) == 35


def test_form_post_toggle_applies_and_preview_shows_expected_number(app):
    from app.radius.db.repos import cards_repo
    with app.app_context():
        pid = _plan_id()
        expected = cards_repo.next_batch_id_estimate()
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
        # المفتاح معطَّل افتراضيًّا، بتسمية «أرقام فقط» لا «B{id}-»
        tag = re.search(r'<input type="checkbox"[^>]*name="include_batch_number"[^>]*>', html, re.S).group(0)
        assert "checked" not in tag
        assert "B{id}" not in html
        assert "data-unprev-bn" in html
        assert 'var nextBn = "%d";' % expected in html
        res = c.post("/admin/radius/cards/generate", data={
            "_csrf_token": "affix-csrf", "plan_id": str(pid), "count": "5",
            "batch_type": "printed", "username_length": "9",
            "username_prefix": "7", "include_batch_number": "1",
        })
    assert res.status_code in (302, 303)
    with app.app_context():
        row = _db().execute(
            "SELECT id, include_batch_number FROM card_batches ORDER BY id DESC LIMIT 1").fetchone()
        names = [r["username"] for r in _db().execute(
            "SELECT username FROM cards WHERE batch_id = ?", (row["id"],))]
    assert row["id"] == expected and row["include_batch_number"] == 1
    rand = 9 - 1 - len(str(expected))
    assert all(re.fullmatch(r"7%d[0-9]{%d}" % (expected, rand), n) for n in names), names
