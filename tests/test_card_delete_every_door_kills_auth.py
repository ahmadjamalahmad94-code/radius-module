"""كلُّ بابٍ يحذف بطاقةً يقطع دخولَها — ولا بابٌ جديدٌ يستطيع أن ينسى.

**الخلفيّة:** البطاقةُ تعيش في مكانَين — صفُّها في ``cards`` (ما تراه اللوحة)
ومرآتُها في ``subscribers`` (‏``user_type='card'``، تُنشأ لحظةَ التوليد) **وهي ما
يقرأه المُصادِق**. حذفُ الأوّلِ وحدَه يُخفي البطاقةَ من اللوحةِ ويُبقيها تدخل.

أُصلح هذا في ``purge_batch`` يومَ 2026-09-21، لكنّ المنطقَ كان **داخلَه وحدَه**،
فنسيه بابٌ ثانٍ (``delete_card_permanently``: 60/60 قُبلت بعد الحذف في الجولة
السادسة). فصار للحذفِ **مكانٌ واحد**: ``cards_repo.erase_card_auth_trail``.

وكشفَ توحيدُه عطبًا ثالثًا مختلفًا: تعويضُ الشراءِ الفاشلِ في المتجر كان يحذف
**الحزمةَ المشتركةَ للعرضِ كاملةً** ⇒ بطاقاتُ كلِّ مَن اشترى قبله تفقد مدّتَها
فتصير **بلا Session-Timeout**.

شغّلْ هذا الملفَّ وحدَه (``pytest tests/<هذا> -p no:cacheprovider``)."""
from __future__ import annotations

import ast
import os
import pathlib
from datetime import datetime, timedelta

import pytest

MAC = "AA:BB:CC:DD:EE:31"
PW = "4455"


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "everydoor.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
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
        yield flask_app


def db():
    from app.radius.db.connection import db as live
    return live()


def _plan() -> int:
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,"
        " price,currency,created_at,updated_at) VALUES(1,'ساعة',60,1,1,'ILS',"
        "datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def _batch(plan_id: int, code: str, *, hours: int = 8) -> int:
    cur = db().execute(
        "INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,"
        " generated,used,time_value,time_unit,count_from_first_connect,"
        " count_by_seconds,status,created_at)"
        " VALUES(1,?,?,?,0,0,0,?,'hours',1,0,'active',datetime('now'))",
        (code, code, plan_id, hours))
    return int(cur.lastrowid)


def _card(batch_id: int, plan_id: int, username: str, *, mirror: bool) -> int:
    """بطاقةٌ في حزمة. ``mirror=True`` = بطاقةٌ مولَّدةٌ عاديّة (لها مرآةٌ وradcheck)؛
    ``False`` = بطاقةُ متجرٍ فوريّة (تُصادَق من ``cards`` مباشرةً)."""
    cur = db().execute(
        "INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,used,"
        " created_at) VALUES(1,?,?,?,?,0,datetime('now'))",
        (batch_id, username, PW, plan_id))
    card_id = int(cur.lastrowid)
    if mirror:
        db().execute(
            "INSERT INTO subscribers(tenant_id,username,password,plan_id,status,"
            " user_type,card_batch_id,created_at)"
            " VALUES(1,?,?,?,'enabled','card',?,datetime('now'))",
            (username, PW, plan_id, batch_id))
        db().execute(
            "INSERT INTO radcheck(tenant_id,username,attribute,op,value)"
            " VALUES(1,?,'Cleartext-Password',':=',?)", (username, PW))
    return card_id


def _auth(username: str):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password=PW, tenant_id=1,
                                 calling_station_id=MAC, nas_ip="10.0.0.1"))


def _trail_gone(username: str) -> bool:
    """لا بطاقةَ ولا مرآةَ ولا radcheck بهذا الاسم."""
    return all(db().execute(sql, (username,)).fetchone() is None for sql in (
        "SELECT 1 FROM cards WHERE username=?",
        "SELECT 1 FROM subscribers WHERE username=? AND user_type='card'",
        "SELECT 1 FROM radcheck WHERE username=?",
    ))


# ═══════════════════ الباب 1: حذفُ الحزمةِ كاملةً نهائيًّا ═══════════════════
def test_door1_purge_batch_kills_every_card(app_ctx):
    plan = _plan()
    b = _batch(plan, "B-DOOR1")
    _card(b, plan, "81000001", mirror=True)
    _card(b, plan, "81000002", mirror=True)
    db().commit()
    assert _auth("81000001").ok and _auth("81000002").ok      # ضابط

    from app.radius.db.repos import cards_repo
    summary = cards_repo.purge_batch(1, b)

    assert summary["cards"] == 2 and summary["subscribers"] == 2
    for u in ("81000001", "81000002"):
        assert _trail_gone(u), u
        assert not _auth(u).ok, f"{u} ما زالت تدخل بعد حذفِ حزمتِها نهائيًّا"


# ═══════════════════ الباب 2: حذفُ بطاقةٍ واحدةٍ نهائيًّا ═══════════════════
def test_door2_delete_one_card_kills_it_and_spares_its_sibling(app_ctx):
    plan = _plan()
    b = _batch(plan, "B-DOOR2")
    gone = _card(b, plan, "82000001", mirror=True)
    _card(b, plan, "82000002", mirror=True)
    db().commit()
    assert _auth("82000001").ok

    from app.radius.db.repos import cards_repo
    assert cards_repo.delete_card_permanently(1, gone) is True

    assert _trail_gone("82000001")
    assert not _auth("82000001").ok, "البطاقةُ المحذوفةُ نهائيًّا ما زالت تدخل (C1)"
    assert _auth("82000002").ok, "حذفُ بطاقةٍ أصاب أختَها في الحزمةِ نفسِها"


def test_door2_never_touches_a_regular_subscriber_sharing_the_name(app_ctx):
    plan = _plan()
    b = _batch(plan, "B-DOOR2B")
    cid = _card(b, plan, "82500001", mirror=True)
    db().execute("UPDATE subscribers SET user_type='subscriber', card_batch_id=NULL"
                 " WHERE username='82500001'")
    db().commit()
    from app.radius.db.repos import cards_repo
    cards_repo.delete_card_permanently(1, cid)
    assert db().execute("SELECT 1 FROM subscribers WHERE username='82500001'").fetchone()


# ═══════════ الباب 3: تعويضُ شراءٍ فوريٍّ فشل في المتجر الإلكترونيّ ═══════════
def test_door3_failed_purchase_kills_only_its_own_card(app_ctx):
    """الحزمةُ **مشتركةٌ لكلِّ العرض**: شراءٌ فاشلٌ يمحو بطاقتَه **وحدَها**.

    🔴 قبل الإصلاح كان يحذف الحزمةَ كاملةً ⇒ بطاقاتُ المشترين السابقين تفقد
    مدّتَها (محفوظةٌ في الحزمة) ⇒ تُقبَل **بلا Session-Timeout**."""
    plan = _plan()
    shared = _batch(plan, "STORE-OFFER-8H", hours=8)
    _card(shared, plan, "83000001", mirror=False)      # مشترٍ سابقٌ دفع
    failed = _card(shared, plan, "83000002", mirror=False)  # الشراءُ الذي فشل
    db().commit()

    before = _auth("83000001")
    assert before.ok and "Session-Timeout" in before.reply_attrs  # ضابط: مدّةُ 8 ساعات

    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    CardUsersMarketplaceService(tenant_id=1)._discard_minted_card(
        {"id": failed, "username": "83000002", "batch_id": shared})

    # البطاقةُ الفاشلةُ ماتت …
    assert _trail_gone("83000002")
    assert not _auth("83000002").ok
    # … والحزمةُ المشتركةُ باقية …
    assert db().execute("SELECT 1 FROM card_batches WHERE id=?", (shared,)).fetchone(), \
        "شراءٌ واحدٌ فاشلٌ محا الحزمةَ المشتركةَ لكلِّ مشتري العرض"
    # … ومشتري العرضِ السابقُ يحتفظ بمدّته بالضبط.
    after = _auth("83000001")
    assert after.ok
    # السقفُ يتناقص مع الزمن (العدُّ من أوّلِ دخول)، فنقارن بهامشِ ثوانٍ. المهمّ
    # أنّه **موجود** ويساوي الـ8 ساعات تقريبًا — لا غائبٌ (بطاقةٌ مفتوحة).
    assert "Session-Timeout" in after.reply_attrs, \
        "بطاقةُ المشتري السابق فقدت سقفَ مدّتها — صارت مفتوحةً بلا حدّ"
    drift = int(before.reply_attrs["Session-Timeout"]) - int(after.reply_attrs["Session-Timeout"])
    assert 0 <= drift <= 5, f"سقفُ المدّة تغيّر بأكثرَ من مرورِ الزمن: {drift}s"


# ═══════ الحارس: أيُّ بابٍ جديدٍ يحذف من cards يجب أن يمرّ من المكانِ الوحيد ═══════
ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"


def _functions_deleting_cards():
    """كلُّ دالّةٍ في ``app/`` نصُّها يحذف من جدول ``cards`` نفسِه."""
    hits = []
    for path in ROOT.rglob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        if "DELETE FROM cards" not in src:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(src, node) or ""
            # «DELETE FROM cards » أو «DELETE FROM cards\n»… لا cards_print/card_batches
            for marker in ("DELETE FROM cards ", "DELETE FROM cards\n", 'DELETE FROM cards"'):
                if marker in body:
                    hits.append((path.relative_to(ROOT.parent).as_posix(), node.name, body))
                    break
    return hits


def test_every_card_deletion_goes_through_the_single_eraser():
    """🔴 لا بابَ يحذف من ``cards`` دون ``erase_card_auth_trail``.

    إن فشل هذا الاختبار فقد كتبتَ بابًا جديدًا لحذفِ البطاقات: استدعِ
    ``cards_repo.erase_card_auth_trail(conn, tenant_id, usernames=[...])`` في
    المعاملةِ نفسِها قبل الحذف — وإلّا اختفت البطاقةُ من اللوحةِ وبقيت تدخل."""
    found = _functions_deleting_cards()
    assert found, "لم يُعثر على أيِّ حذفٍ للبطاقات — تغيّر الكودُ، حدّثْ هذا الحارس"
    offenders = [f"{p}::{name}" for p, name, body in found
                 if "erase_card_auth_trail" not in body]
    assert not offenders, (
        "أبوابٌ تحذف بطاقاتٍ دون المرورِ من المكانِ الوحيد "
        f"(البطاقةُ تختفي من اللوحةِ وتبقى تدخل): {offenders}")
