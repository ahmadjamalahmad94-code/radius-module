"""«تقسيم السرعة على الأجهزة» — منطق القسمة في bandwidth_rate.

يختبر الدالّة الصافية ``_apply_device_split`` (والغلاف ``effective_rate_kbps``)
بحقن عدد الأجهزة الحيّة، دون الحاجة لقاعدة بيانات أو RADIUS. يغطّي:
  * جهاز واحد → لا قسمة (السلوك الافتراضيّ سليم).
  * جهازان/ثلاثة → قسمة صحيحة على الاتّجاه المُفعَّل فقط.
  * الحدّ الأدنى للحصّة (SPLIT_MIN_KBPS).
  * التعطيل (الافتراضيّ) → لا تغيير مطلقًا.
"""
import types

from app.radius.services import bandwidth_rate as br


def _sub(*, down=False, up=False):
    """كائن مشترك مبسّط يحمل علمَي التوزيع فقط."""
    return types.SimpleNamespace(equal_share_download=down, equal_share_upload=up)


def _patch_count(monkeypatch, n):
    monkeypatch.setattr(br, "_live_device_count", lambda tid, u: n)


def test_disabled_is_noop(monkeypatch):
    _patch_count(monkeypatch, 5)
    # التقسيم معطّل (الافتراضيّ) → القيم كما هي مهما كان عدد الأجهزة.
    assert br._apply_device_split(1, "u", _sub(), 10000, 8000) == (10000, 8000)


def test_single_device_no_split(monkeypatch):
    _patch_count(monkeypatch, 1)
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 10000, 8000) == (10000, 8000)


def test_two_devices_half(monkeypatch):
    _patch_count(monkeypatch, 2)
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 10000, 8000) == (5000, 4000)


def test_three_devices_third(monkeypatch):
    _patch_count(monkeypatch, 3)
    # 10000//3 = 3333 ؛ 9000//3 = 3000
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 10000, 9000) == (3333, 3000)


def test_download_only(monkeypatch):
    _patch_count(monkeypatch, 2)
    # قسمة التنزيل فقط؛ الرفع يبقى كاملًا.
    assert br._apply_device_split(1, "u", _sub(down=True, up=False), 10000, 8000) == (5000, 8000)


def test_upload_only(monkeypatch):
    _patch_count(monkeypatch, 4)
    assert br._apply_device_split(1, "u", _sub(down=False, up=True), 10000, 8000) == (10000, 2000)


def test_min_floor(monkeypatch):
    _patch_count(monkeypatch, 100)
    # 1000//100 = 10 kbps < SPLIT_MIN_KBPS → يُثبَّت عند الحدّ الأدنى.
    down, up = br._apply_device_split(1, "u", _sub(down=True, up=True), 1000, 1000)
    assert down == br.SPLIT_MIN_KBPS
    assert up == br.SPLIT_MIN_KBPS


def test_zero_rate_stays_zero(monkeypatch):
    _patch_count(monkeypatch, 3)
    # سرعة غير محدّدة (0 = غير محدود) لا تُقسَّم إلى حدّ أدنى مصطنع.
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 0, 0) == (0, 0)


# ═══ اختبارات حقيقيّة بقاعدة بيانات (تمسك بق «العدّ=1 دائمًا») ═══
# عزل: قاعدة الاختبار ملفّ دائم بين التشغيلات — الأسماء ومعرّفات الجلسات فريدة
# لكلّ تشغيل (uuid) كي لا تتراكم صفوف radacct مفتوحة من تشغيلات سابقة.
import pytest
from uuid import uuid4


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        tenants_repo.ensure_default_tenant()
        with transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO access_plans(id,tenant_id,name,code,plan_type,"
                "service_type,duration_minutes,validity_days,speed_down_kbps,"
                "speed_up_kbps,price,currency,enabled,created_at) VALUES"
                "(9911,1,'Split Plan','SPL','time','Hotspot',1440,30,10000,8000,5,'JOD',1,?)",
                (now_iso(),),
            )
    return application


def _mk_sub(username, *, down=True, up=False):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="x",
        user_type="subscriber", plan_id=9911,
        equal_share_download=down, equal_share_upload=up,
    ))


def _open_session(conn, username, sid):
    conn.execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username,"
        " nasipaddress, acctstarttime, acctupdatetime)"
        " VALUES (1, ?, ?, ?, '10.50.0.3', datetime('now'), datetime('now'))",
        (sid, sid + "-u", username))


def test_live_device_count_counts_open_radacct_sessions(app):
    """البق المرتدّ: count_real_sessions([u]) كان يرجع 1 دائمًا → لا قسمة أبدًا.
    العدّ الصحيح = جلسات radacct المفتوحة (نفس مصدر أهداف CoA)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import bandwidth_rate as br
        u = f"split_c_{uuid4().hex[:8]}"
        _mk_sub(u)
        with transaction() as conn:
            _open_session(conn, u, u + "-s1")
            _open_session(conn, u, u + "-s2")
        assert br._live_device_count(1, u) == 2


def test_effective_rate_limit_divides_with_two_open_sessions(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import bandwidth_rate as br
        u = f"split_d_{uuid4().hex[:8]}"
        _mk_sub(u, down=True, up=False)
        with transaction() as conn:
            _open_session(conn, u, u + "-t1")
            _open_session(conn, u, u + "-t2")
        # 10000k تنزيل ÷ 2 = 5000k؛ الرفع (التقسيم مُطفأ) يبقى 8000k.
        assert br.effective_rate_limit(1, u) == "8000k/5000k"


def test_effective_rate_limit_full_when_one_session(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import bandwidth_rate as br
        u = f"split_f_{uuid4().hex[:8]}"
        _mk_sub(u, down=True, up=True)
        with transaction() as conn:
            _open_session(conn, u, u + "-w1")
        assert br.effective_rate_limit(1, u) == "8000k/10000k"


# ═══ عامل إعادة التوزيع (speed_split_worker) — transition-based ═══

def test_worker_pushes_on_count_change_only(app, monkeypatch):
    """أوّل رؤية → دفعة idempotent؛ لا تغيّر → لا دفع؛ فصل → دفعة؛ اتصال → دفعة."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.workers import speed_split_worker as w
        from app.radius.services import bandwidth_apply

        pushes = []
        monkeypatch.setattr(bandwidth_apply, "apply_users_effective",
                            lambda tid, names, **kw: (pushes.append(list(names)) or
                                                      {"applied": len(names)}))
        w.reset_state_for_tests()
        u = f"split_w_{uuid4().hex[:8]}"
        _mk_sub(u, down=True, up=True)
        with transaction() as conn:
            _open_session(conn, u, u + "-k1")
            _open_session(conn, u, u + "-k2")

        # tick 1: أوّل رؤية (n=2) → دفعة واحدة لهذا المستخدم.
        w.tick_once()
        assert pushes and u in pushes[-1]
        n_after_first = len(pushes)
        # tick 2: لا تغيّر → لا دفع جديد لهذا المستخدم.
        w.tick_once()
        assert len(pushes) == n_after_first
        # فصل جهاز (إغلاق جلسة) → tick يدفع ثانيةً (n=1 → السرعة الكاملة).
        with transaction() as conn:
            conn.execute("UPDATE radacct SET acctstoptime=datetime('now') "
                         "WHERE acctsessionid=?", (u + "-k2",))
        w.tick_once()
        assert len(pushes) == n_after_first + 1 and u in pushes[-1]
        # اتصال جهاز جديد → دفعة (n=2).
        with transaction() as conn:
            _open_session(conn, u, u + "-k3")
        w.tick_once()
        assert len(pushes) == n_after_first + 2 and u in pushes[-1]


def test_worker_ignores_non_split_users(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.workers import speed_split_worker as w
        from app.radius.services import bandwidth_apply

        pushes = []
        monkeypatch.setattr(bandwidth_apply, "apply_users_effective",
                            lambda tid, names, **kw: (pushes.append(list(names)) or
                                                      {"applied": len(names)}))
        w.reset_state_for_tests()
        u = f"nosplit_{uuid4().hex[:8]}"
        _mk_sub(u, down=False, up=False)
        with transaction() as conn:
            _open_session(conn, u, u + "-n1")
            _open_session(conn, u, u + "-n2")
        w.tick_once()
        assert not any(u in p for p in pushes)
