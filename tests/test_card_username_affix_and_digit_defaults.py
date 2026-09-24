# -*- coding: utf-8 -*-
"""ملاحظات «شبكة المحترف» على سهولة الاستخدام (2026-09-24):

1. **كلمة المرور الرقميّة افتراضيًّا** — شاشة إنشاء الحزمة (الكاملة والسريعة)
   تفتح على «أرقام فقط»، وبقيّة الأنماط باقية.
2. **بادئة/لاحقة اسم المستخدم** — حقلان صريحان («— اختياري») بدل «قبل/بعد +
   نصّ» و«بادئة قديمة»، ومعاينةٌ تطابق البطاقات المولَّدة حرفيًّا:
   البادئة «25» + الجزء المولَّد «123456» + اللاحقة «99» ⇒ «2512345699».
3. **أرقامٌ لاتينيّة في الطباعة** — نصوص البطاقة الوصفيّة (سعر/مدّة/عنوان)
   المحفوظة بلوحة مفاتيح عربيّة («٤ ساعات») تُطبع «4 ساعات»، والاعتماد
   (يوزر/باس) يُطبع كما خُزِّن حرفيًّا.
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


def preview(prefix: str, suffix: str, total_len: int) -> tuple[str, int]:
    """منفذُ بايثون لمعاينة cards_generate.html (renderUn) حرفًا بحرف."""
    east = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    pre = "".join(prefix.translate(east).split()).lower()
    suf = "".join(suffix.translate(east).split()).lower()
    gen_len = max(1, total_len - len(pre) - len(suf))
    gen = ("1234567890" * 3)[:gen_len]
    return pre + gen + suf, gen_len


def _pattern(prefix: str, suffix: str, total_len: int) -> re.Pattern:
    _shown, gen_len = preview(prefix, suffix, total_len)
    east = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    p = "".join(prefix.translate(east).split()).lower()
    s = "".join(suffix.translate(east).split()).lower()
    return re.compile("^%s[0-9]{%d}%s$" % (re.escape(p), gen_len, re.escape(s)))


# ── 1. «أرقام فقط» افتراضيًّا ────────────────────────────────────────


def test_full_generate_form_opens_on_digits(app):
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
    sel = html[html.index('name="password_generation_type"'):]
    sel = sel[: sel.index("</select>")]
    assert re.search(r'<option value="digits"\s+selected', sel), sel
    after_digits = sel.split('value="digits"', 1)[1].split("</option>", 1)[1]
    assert "selected" not in after_digits                # لا اختيارَ آخر
    for other in ("medium", "strong", "weak"):          # بقيّة الأنماط باقية
        assert 'value="%s"' % other in sel


def test_quick_create_modal_opens_on_digits(app):
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/cards/batches").get_data(as_text=True)
    sel = html[html.index('name="password_generation_type"'):]
    sel = sel[: sel.index("</select>")]
    assert '<option value="digits" selected>' in sel
    assert '<option value="medium">' in sel


def test_digits_form_default_generates_numeric_passwords(app):
    with app.app_context():
        pid = _plan_id()
    with app.test_client() as c:
        _login(c)
        res = c.post("/admin/radius/cards/generate", data={
            "_csrf_token": "affix-csrf", "plan_id": str(pid), "count": "6",
            "batch_type": "printed", "password_generation_type": "digits",
            "password_length": "6", "username_length": "8",
        })
    assert res.status_code in (302, 303)
    with app.app_context():
        pw = [r["password"] for r in _cards_of_latest_batch()]
    assert len(pw) == 6 and all(re.fullmatch(r"[0-9]{6}", p) for p in pw), pw


# ── 2. البادئة/اللاحقة + معاينةٌ تطابق التوليد ───────────────────────


def test_generate_form_has_two_explicit_optional_fields_and_preview(app):
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
    assert "بادئة اسم المستخدم — اختياري" in html
    assert "لاحقة اسم المستخدم — اختياري" in html
    assert 'name="username_prefix"' in html and 'name="username_suffix"' in html
    assert "2512345699" in html                           # المثال في الشرح
    assert "data-unprev-pre" in html and "data-unprev-gen" in html
    # الحقلان القديمان المحيِّران غابا عن نموذج الإنشاء
    assert 'name="prefix_or_suffix_value"' not in html
    assert 'name="starts_with_or_ends_with"' not in html


def test_quick_modal_has_the_same_two_fields(app):
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/cards/batches").get_data(as_text=True)
    assert "بادئة اسم المستخدم — اختياري" in html
    assert "لاحقة اسم المستخدم — اختياري" in html
    assert "data-qun-pv-gen" in html
    assert 'name="prefix_or_suffix_value"' not in html


def test_customer_example_prefix_25_suffix_99(app):
    """المثال الحرفيّ: 25 + ستّة أرقام + 99 (الطول الكلّيّ 10)."""
    shown, gen_len = preview("25", "99", 10)
    assert (shown, gen_len) == ("2512345699", 6)
    with app.app_context():
        pid = _plan_id()
    with app.test_client() as c:
        _login(c)
        res = c.post("/admin/radius/cards/generate", data={
            "_csrf_token": "affix-csrf", "plan_id": str(pid), "count": "15",
            "batch_type": "printed", "username_length": "10",
            "username_prefix": "25", "username_suffix": "99",
            "password_generation_type": "digits",
        })
    assert res.status_code in (302, 303)
    with app.app_context():
        names = [r["username"] for r in _cards_of_latest_batch()]
    assert len(names) == 15
    assert all(re.fullmatch(r"25[0-9]{6}99", n) for n in names), names
    assert all(len(n) == len(shown) for n in names)


def test_empty_prefix_and_suffix_generate_as_usual(app):
    with app.app_context():
        pid = _plan_id()
    with app.test_client() as c:
        _login(c)
        c.post("/admin/radius/cards/generate", data={
            "_csrf_token": "affix-csrf", "plan_id": str(pid), "count": "5",
            "batch_type": "printed", "username_length": "8",
            "username_prefix": "", "username_suffix": "",
        })
    with app.app_context():
        names = [r["username"] for r in _cards_of_latest_batch()]
    assert len(names) == 5 and all(re.fullmatch(r"[0-9]{8}", n) for n in names), names


@pytest.mark.parametrize("prefix,suffix,total", [
    ("25", "99", 10),
    ("", "99", 8),
    ("GZA-", "", 9),          # حروفٌ تُحفظ صغيرة
    (" ٢٥ ", "٩ ٩", 10),      # لوحة مفاتيح عربيّة + مسافات ⇒ 25…99
    ("123456", "789", 8),     # الثابت يملأ الطول ⇒ رقمٌ مولَّدٌ واحد
])
def test_preview_matches_generated_usernames(app, prefix, suffix, total):
    from app.radius.services.cards import get_cards_service
    shown, gen_len = preview(prefix, suffix, total)
    count = 8 if gen_len > 1 else 1
    with app.app_context():
        batch, _ = get_cards_service().generate_batch(
            actor="admin", plan_id=_plan_id(), count=count,
            username_prefix=prefix, username_suffix=suffix,
            username_length=total, package_name="معاينة")
        names = [r["username"] for r in _db().execute(
            "SELECT username FROM cards WHERE batch_id = ?", (batch.id,))]
    pat = _pattern(prefix, suffix, total)
    assert len(names) == count
    assert all(pat.fullmatch(n) for n in names), (pat.pattern, names)
    assert all(len(n) == len(shown) for n in names)


# ── 3. أرقامٌ لاتينيّة في الطباعة ────────────────────────────────────


def test_printed_descriptive_text_uses_latin_digits():
    from app.radius.services.card_renderer import build_card_render_model
    model = build_card_render_model(
        {"layout_json": "{}"},
        {"username": "25123456", "password": "4321", "id": 7},
        overrides={"price_text": "٥ ₪", "validity_text": "٤ ساعات",
                   "card_title": "بطاقة ١٠ ساعات"})
    texts = " ".join(str(el.get("text") or "") + " " + str(el.get("label") or "")
                     for el in model["elements"])
    assert not re.search(r"[٠-٩۰-۹]", texts), texts
    assert "4 ساعات" in texts and "بطاقة 10 ساعات" in texts


def test_printed_credentials_are_never_rewritten():
    """اليوزر/الباس يُطبعان كما خُزِّنا — وإلّا لم يطابق ما يكتبه الزبون."""
    from app.radius.services.card_renderer import build_card_render_model
    model = build_card_render_model(
        {"layout_json": "{}"}, {"username": "١٢٣٤", "password": "٥٦", "id": 1})
    assert model["username"] == "١٢٣٤" and model["password"] == "٥٦"
