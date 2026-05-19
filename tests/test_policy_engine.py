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


def _build_chap(password: str, *, chap_id: int = 1, challenge: bytes = b"\x00" * 16) -> tuple[str, str]:
    """Build (CHAP-Password hex, CHAP-Challenge hex) per RFC 1994."""
    import hashlib
    chap_id_b = bytes([chap_id])
    digest = hashlib.md5(chap_id_b + password.encode("utf-8") + challenge).digest()
    return (chap_id_b + digest).hex(), challenge.hex()


def test_chap_password_correct_accepted():
    """MikroTik hotspot default = HTTP-CHAP. Verify we accept when the
    CHAP-Password digest matches the stored cleartext password."""
    app = _fresh_app()
    with app.app_context():
        import os
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="secret", status="enabled",
        ))
        challenge = os.urandom(16)
        chap_pw, chap_ch = _build_chap("secret", chap_id=42, challenge=challenge)
        d = authorize(AuthRequest(
            username="ali",
            chap_password=chap_pw,
            chap_challenge=chap_ch,
            tenant_id=1,
        ))
        assert d.ok is True, f"expected Accept, got {d.reason}: {d.message}"


def test_chap_password_correct_with_0x_prefix():
    """rlm_rest may serialize octets with `0x` prefix — verify we strip it."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="p@ss", status="enabled",
        ))
        chap_pw, chap_ch = _build_chap("p@ss", chap_id=7, challenge=b"\xAB" * 16)
        d = authorize(AuthRequest(
            username="ali",
            chap_password="0x" + chap_pw,
            chap_challenge="0x" + chap_ch,
            tenant_id=1,
        ))
        assert d.ok is True


def test_chap_password_wrong_rejected():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="real", status="enabled",
        ))
        # نحسب الـ digest على كلمة سرّ مختلفة
        chap_pw, chap_ch = _build_chap("wrong", chap_id=1, challenge=b"\x01" * 16)
        d = authorize(AuthRequest(
            username="ali",
            chap_password=chap_pw,
            chap_challenge=chap_ch,
            tenant_id=1,
        ))
        assert d.ok is False
        assert d.reason == "password_wrong"


def test_no_pap_no_chap_rejected():
    """طلب بدون User-Password ولا CHAP-Password → reject مع log تحذيري."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ali", password="p", status="enabled",
        ))
        d = authorize(AuthRequest(username="ali", tenant_id=1))
        assert d.ok is False
        assert d.reason == "password_wrong"


def test_card_lookup_fallback_accepts_pap():
    """لو لم يُوجَد subscriber بالاسم، يبحث في جدول cards."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, CardBatch
        from app.radius.db.repos import cards_repo, plans_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Voucher", plan_type="time",
            session_timeout_sec=3600, enabled=True,
        ))
        batch = cards_repo.create_batch(CardBatch(
            id=None, tenant_id=1, batch_code="B-TEST-0001", plan_id=plan.id, count=1,
        ))
        cards = cards_repo.generate_cards(
            tenant_id=1, batch_id=batch.id, plan_id=plan.id, count=1,
            username_prefix="card", username_length=10,
            password_length=4, password_charset="digits",
        )
        c = cards[0]
        d = authorize(AuthRequest(
            username=c.username, password=c.password, tenant_id=1,
        ))
        assert d.ok is True, f"expected card auth Accept, got {d.reason}"


def test_card_lookup_fallback_rejects_wrong_password():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, CardBatch
        from app.radius.db.repos import cards_repo, plans_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Voucher", plan_type="time", enabled=True,
        ))
        batch = cards_repo.create_batch(CardBatch(
            id=None, tenant_id=1, batch_code="B-TEST-0002", plan_id=plan.id, count=1,
        ))
        cards = cards_repo.generate_cards(
            tenant_id=1, batch_id=batch.id, plan_id=plan.id, count=1,
        )
        d = authorize(AuthRequest(
            username=cards[0].username, password="bogus", tenant_id=1,
        ))
        assert d.ok is False
        assert d.reason == "password_wrong"
