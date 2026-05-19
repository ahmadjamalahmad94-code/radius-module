"""اختبارات Policy Engine — يُغطّي مسارات Accept/Reject الأساسية."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_app():
    """ينشئ app جديد بـ DB مؤقت كي لا تتداخل الاختبارات."""
    tmp = tempfile.mkdtemp(prefix="hr_test_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    # امسح module cache لإجبار إعادة قراءة env
    for k in list(sys.modules):
        if k.startswith("app."): del sys.modules[k]
    from app import create_app
    return create_app()


def test_user_not_found_returns_reject():
    app = _fresh_app()
    with app.app_context():
        from app.radius.services.policy_engine import AuthRequest, authorize
        d = authorize(AuthRequest(username="ghost", password="x", tenant_id=1))
        assert d.ok is False
        assert d.reason == "user_not_found"
        assert "غير موجود" in d.message


def test_wrong_password_returns_reject():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="rightpass", status="enabled",
        ))
        d = authorize(AuthRequest(username="ali", password="wrong", tenant_id=1))
        assert d.ok is False
        assert d.reason == "password_wrong"


def test_disabled_account_rejected():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="p", status="disabled",
        ))
        d = authorize(AuthRequest(username="ali", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "disabled"


def test_expired_account_rejected():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="p", status="enabled",
            expire_at=datetime.utcnow() - timedelta(days=1),
        ))
        d = authorize(AuthRequest(username="ali", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "expired"


def test_happy_path_accept_with_attrs():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Gold", plan_type="time",
            speed_down_kbps=10240, speed_up_kbps=2048,
            session_timeout_sec=3600, idle_timeout_sec=600,
            concurrent_sessions=2, enabled=True,
        ))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="p", status="enabled",
            plan_id=plan.id,
        ))
        d = authorize(AuthRequest(username="ali", password="p", tenant_id=1))
        assert d.ok is True
        assert "Mikrotik-Rate-Limit" in d.reply_attrs or "Session-Timeout" in d.reply_attrs


def test_mac_lock_mismatch_rejected():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="p", status="enabled",
            mac_lock="AA:BB:CC:DD:EE:FF",
        ))
        d = authorize(AuthRequest(
            username="ali", password="p", tenant_id=1,
            calling_station_id="11:22:33:44:55:66",
        ))
        assert d.ok is False
        assert d.reason == "mac_mismatch"
