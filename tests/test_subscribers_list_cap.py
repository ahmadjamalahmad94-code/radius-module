"""ترقيم صفحة المشتركين الخادميّ (يوليو 2026).

سابقًا: الجدول client-side كان يُصيّر **كلّ** الصفوف (limit=10000) ثمّ يُخفيها
بـJS ويُظهر 25/50 — ثقل تحميل حقيقيّ عند 1500+ مشترك. الآن: ترقيم خادميّ —
الصفحة تجلب وتُصيّر **صفحة واحدة فقط** (page_size)، والعدّ/الفرز/الفلاتر في
SQL، مع مُرقِّم روابط GET أسفل الجدول.

يزرع 1005 مشتركين ويثبت: (1) الصفحة تُصيّر ≤ page_size لا 1005؛ (2) الإجماليّ
الصحيح يظهر؛ (3) الصفحات متكاملة تغطّي الجميع بلا تكرار.

شغّل وحده (عزل لكل ملف) — راجع memory test-isolation-per-file.
"""
from __future__ import annotations

import os
import re

import pytest


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "subs_cap.db")
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
    return flask_app


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "cap_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "cap-csrf"


PREFIX = "cap1k_u"


def _seed(app, n=1005):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        for i in range(n):
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, tenant_id=1, username=f"{PREFIX}{i:04d}",
                password="pw1234", status="enabled", user_type="subscriber"))


def _seen(html):
    return set(re.findall(re.escape(PREFIX) + r"\d{4}", html))


def test_first_page_renders_only_one_page_not_all(app):
    _seed(app)
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/subscribers")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    seen = _seen(html)
    # ترقيم خادميّ: صفحة واحدة فقط (page_size الافتراضيّ 50) — لا 1005 صفًّا.
    assert 0 < len(seen) <= 50, f"يُفترض ≤50، ظهر {len(seen)} (عاد رسم-الكلّ؟)"
    # الإجماليّ الصحيح يظهر في المُرقِّم («من 1005»).
    assert "1005" in html


def test_pages_are_disjoint_and_cover_everyone(app):
    _seed(app)
    all_seen = set()
    with app.test_client() as client:
        _auth_session(client)
        for p in (1, 2, 3):  # 500×2 + 5 = 1005 → 3 صفحات
            res = client.get(f"/admin/radius/subscribers?page_size=500&page={p}")
            assert res.status_code == 200
            page_seen = _seen(res.get_data(as_text=True))
            assert not (page_seen & all_seen), "تكرار مشتركين بين الصفحات"
            all_seen |= page_seen
    assert len(all_seen) == 1005, f"لم تُغطَّ الصفحات الجميع: {len(all_seen)}/1005"


def test_all_is_capped_above_limit(app):
    """«الكل» فوق الحدّ (500) لا يُصيّر آلاف الصفوف — يَسقط لترقيم منظّم + تنبيه.
    كان يُنتج >100k عنصر DOM يُجمّد المتصفّح (فحص أداء يوليو 2026)."""
    _seed(app)  # 1005
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/subscribers?page_size=all")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    seen = _seen(html)
    assert len(seen) <= 500, f"«الكل» رسم {len(seen)} صفًّا — الحدّ غير مطبَّق!"
    assert "srv-capped" in html                  # تنبيه الحدّ ظاهر
    assert "1005" in html                         # الإجماليّ الصحيح


def test_all_under_cap_renders_everything(app):
    """«الكل» ضمن الحدّ يبقى صفحةً واحدة تسع الجميع (بلا تنبيه)."""
    _seed(app, n=20)
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/subscribers?page_size=all")
    html = res.get_data(as_text=True)
    assert len(_seen(html)) == 20
    assert "srv-capped" not in html
