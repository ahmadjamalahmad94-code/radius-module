# -*- coding: utf-8 -*-
"""تنبيه «تعديل بيانات مشترك» يَعرض الفرق الحقيقيّ بدل «غُيّر: —» الثابت.

العلّة: `UsersServiceImpl.update()` كان يُمرّر `changed="—"` ثابتًا، فالتنبيه
يَظهر دائمًا «غُيّر: —» مهما عُدِّل. الإصلاح: نَجلب الصفّ القديم قبل الحفظ
ونَحسب فرقًا عربيًّا مقروءًا (القديم → الجديد) للحقول ذات المعنى (الاسم/الجوال/
الباقة/الحالة/السرعة/الكوتا + «كلمة المرور: تم التغيير» دون طباعتها).

يُغطّي: diff الباقة+الجوال (وحدة، بأسماء باقات حقيقيّة)، تعديل حقيقيّ عبر
الخدمة (تكامل)، لا تغييرات → «لا تغييرات جوهرية»، فشل جلب القديم → «—» دون
كسر التحديث، وكلمة المرور لا تُطبَع.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_subdiff_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _sub(**kw):
    from app.radius.core.types import Subscriber
    base = dict(id=None, tenant_id=1, username="u1", password="pw-123456",
                user_type="subscriber", full_name="", mobile="", status="enabled")
    base.update(kw)
    return Subscriber(**base)


def _seed(username="u1", **kw):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(_sub(username=username, **kw))


def _capture_dispatch(monkeypatch):
    """يلتقط استدعاءات admin_alerts.dispatch (يُغذّيها _notify_alert)."""
    from app.radius.services import admin_alerts
    calls = []
    monkeypatch.setattr(admin_alerts, "dispatch",
                        lambda tid, key, ctx, **kw: calls.append((key, ctx)))
    return calls


def _edited_ctx(calls):
    edited = [ctx for key, ctx in calls if key == "subscriber_edited"]
    return edited[-1] if edited else None


# ════════════════════════════════════════════════════════════════════════
# (1) وحدة الفرق — _describe_subscriber_changes
# ════════════════════════════════════════════════════════════════════════
def test_diff_plan_and_mobile_shows_both_old_and_new(app, monkeypatch):
    with app.app_context():
        from app.radius.services import users as U
        # حلّ تسمية الباقة من المعرّف (لا نطبع المعرّف الخام).
        monkeypatch.setattr(U, "_plan_label",
                            lambda tid, pid: {1: "باقة ١٠م", 2: "باقة ٢٠م"}.get(pid, "—"))
        old = _sub(plan_id=1, mobile="0599111111", full_name="أحمد")
        new = _sub(plan_id=2, mobile="0599222222", full_name="أحمد")
        changed = U._describe_subscriber_changes(old, new)
        # الباقة: القديمة والجديدة بالاسم لا المعرّف.
        assert "باقة ١٠م" in changed and "باقة ٢٠م" in changed
        # الجوال: القديم والجديد.
        assert "0599111111" in changed and "0599222222" in changed
        # صيغة «القديم → الجديد».
        assert "→" in changed
        # الاسم لم يتغيّر فلا يُذكَر.
        assert "الاسم" not in changed


def test_diff_status_mapped_to_arabic(app):
    with app.app_context():
        from app.radius.services import users as U
        changed = U._describe_subscriber_changes(
            _sub(status="enabled"), _sub(status="disabled"))
        assert "الحالة" in changed and "مفعّل" in changed and "معطّل" in changed


def test_diff_no_changes_returns_sentinel(app):
    with app.app_context():
        from app.radius.services import users as U
        same = _sub(full_name="أحمد", mobile="0599", status="enabled")
        assert U._describe_subscriber_changes(same, same) == "لا تغييرات جوهرية"


def test_diff_old_none_falls_back_safely(app):
    with app.app_context():
        from app.radius.services import users as U
        assert U._describe_subscriber_changes(None, _sub()) == "—"


def test_diff_password_change_marked_not_printed(app):
    with app.app_context():
        from app.radius.services import users as U
        old = _sub(password="old-secret")
        new = _sub(password="new-secret")
        changed = U._describe_subscriber_changes(old, new)
        assert "كلمة المرور: تم التغيير" in changed
        # لا تُطبَع القيمة إطلاقًا.
        assert "old-secret" not in changed and "new-secret" not in changed


def test_diff_speed_and_quota(app):
    with app.app_context():
        from app.radius.services import users as U
        old = _sub(download_speed_kbps=0, upload_speed_kbps=0, combined_quota_mb=0)
        new = _sub(download_speed_kbps=20480, upload_speed_kbps=10240,
                   combined_quota_mb=10240)
        changed = U._describe_subscriber_changes(old, new)
        assert "السرعة" in changed and "20480/10240" in changed
        assert "الكوتا" in changed and "10240" in changed


# ════════════════════════════════════════════════════════════════════════
# (2) تكامل — مسار الخدمة الحقيقيّ يُغذّي التنبيه بالفرق الحقيقيّ
# ════════════════════════════════════════════════════════════════════════
def test_update_dispatches_real_diff(app, monkeypatch):
    with app.app_context():
        from app.radius.services.users import get_users_service
        from app.radius.db.repos import subscribers_repo
        _seed("alice", full_name="Alice", mobile="0599000000")
        cur = subscribers_repo.get_subscriber(1, "alice")

        calls = _capture_dispatch(monkeypatch)
        # عدّل الاسم + الجوال (نُبقي كلمة المرور كما هي).
        get_users_service().update(
            actor="op",
            sub=replace(cur, full_name="Alice Updated", mobile="0599999999"))
        ctx = _edited_ctx(calls)
        assert ctx is not None
        assert ctx["changed"] != "—"
        assert "0599000000" in ctx["changed"] and "0599999999" in ctx["changed"]
        assert "Alice" in ctx["changed"] and "Alice Updated" in ctx["changed"]


def test_update_no_change_yields_sentinel(app, monkeypatch):
    with app.app_context():
        from app.radius.services.users import get_users_service
        from app.radius.db.repos import subscribers_repo
        _seed("bob", full_name="Bob", mobile="0591111111")
        cur = subscribers_repo.get_subscriber(1, "bob")

        calls = _capture_dispatch(monkeypatch)
        get_users_service().update(actor="op", sub=cur)  # نفس البيانات
        ctx = _edited_ctx(calls)
        assert ctx is not None and ctx["changed"] == "لا تغييرات جوهرية"


def test_update_existing_fetch_failure_falls_back(app, monkeypatch):
    with app.app_context():
        from app.radius.services.users import get_users_service
        _seed("carol", full_name="Carol", mobile="0592222222")

        svc = get_users_service()

        def _boom(*a, **k):
            raise RuntimeError("lookup down")
        # نُعطّل جلب القديم فقط (الحفظ يبقى سليمًا).
        monkeypatch.setattr(svc._adapter, "get_account", _boom)

        calls = _capture_dispatch(monkeypatch)
        # نُمرّر كلمة مرور كي لا يَلمس فرع حفظ كلمة المرور get_account ثانيةً.
        svc.update(actor="op", sub=_sub(username="carol", password="pw-123456",
                                        full_name="Carol X", mobile="0593333333"))
        ctx = _edited_ctx(calls)
        assert ctx is not None and ctx["changed"] == "—"
