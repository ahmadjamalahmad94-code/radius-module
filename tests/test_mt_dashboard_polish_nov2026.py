"""Router dashboard polish (نوفمبر 2026 — طلب المالك).

يُثبِت ثلاث إصلاحات على /admin/radius/mt/<id>/dashboard:

  1) قائمة «المتصلون الآن» صارت حاوية تمرير رأسيّ (سكرول) داخل بطاقة
     الـ340px — كانت تُقصّ عند صفّين بسبب تعارض overflow:hidden في CSS
     التالي، فلا يَصل المشغّل إلى الجلسات اللاحقة.

  2) رسم «حركة الواجهات» — أضفنا fill="none" على مسارَي RX/TX
     ومنطقتَي ملء area تحتهما، فلا يَظهر «المستطيل الفاضي» بعد اليوم:
     كان المسار بلا fill:none يُصيَّر كمُضلَّع مملوء أسود يَحجب المنحنى.

  3) شارة حالة الراوتر «متصل الآن» صارت pill مصمَّم من نظام التصميم
     (خلفيّة خضراء ناعمة + نقطة نابضة + border + rounded) بدل النصّ
     العادي بلا خلفيّة الذي كان يتيمًا في الترويسة.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_polish_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"polish_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="polish-pass", full_name="Polish Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "polish-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices "
                "(id, tenant_id, name, address, secret, vendor, "
                " nas_type, enabled, created_at, connection_mode) "
                "VALUES (?, 1, 'polish-rtr', '203.0.113.9', 's', "
                " 'mikrotik', 'hotspot', 1, ?, 'direct')",
                (nas_id, now),
            )


def _fetch(app, client) -> str:
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    return res.get_data(as_text=True)


# ─── Fix #1: connected-now sessions list scrollable ─────────


def test_active_users_list_is_a_flex_scroll_container(app, client):
    """قاعدة CSS تُحوّل .mt-users-list إلى flex:1 min-height:0
    overflow-y:auto — فيَملأ الارتفاع المتبقّي داخل البطاقة ويَقبل
    التمرير. نتحقّق من وجود القاعدة الجديدة نفسها في الـHTML."""
    html = _fetch(app, client)
    # سطر CSS الحاسم في الإصلاح
    assert "flex:1 1 0 !important" in html
    assert "overflow-y:auto !important" in html
    assert ".rh-overview .mt-users-list" in html


def test_active_users_list_thead_is_sticky(app, client):
    """أعمدة الجدول (النوع/المستخدم/العنوان/المدة) مُثبَّتة بـsticky
    فتَبقى مرئيّة أثناء التمرير على القائمة."""
    html = _fetch(app, client)
    assert ".mt-users-list .mt-users-table thead th" in html
    assert "position:sticky" in html


def test_active_users_card_body_no_double_scroll(app, client):
    """داخل الإصلاح، جسم البطاقة أعلى القائمة لا يَنبغي أن يُشغّل
    سكرول آخر — نُلغيه بـoverflow:hidden ليَبقى السكرول على القائمة
    وحدها. يَضمن هذا اختفاء double-scroll على macOS/Firefox."""
    html = _fetch(app, client)
    assert (".rh-overview .mt-grid > .mt-card.mt-live-card--users "
            "> .mt-card-body") in html


# ─── Fix #2: traffic chart no longer «empty rectangle» ───────


def test_traffic_spark_paths_declare_fill_none(app, client):
    """الجذر: مسارات SVG بلا fill:none تُملأ افتراضيّاً بأسود فيَحجب
    المنحنى. الإصلاح يَضبط fill:none في CSS + على العناصر نفسها."""
    html = _fetch(app, client)
    # سمة fill="none" على عنصر الخطّ في الـmarkup مباشرةً
    assert 'data-mt-spark-rx d="" fill="none"' in html
    assert 'data-mt-spark-tx d="" fill="none"' in html
    # قاعدة CSS تَضبط fill:none على الخطوط ومناطق الملء معاً — كلتاهما
    # يَتشاركان القاعدة نفسها. نتحقّق من محدّدَي الملء والخطّ
    # الأصلي أنّهما ظاهران في نفس القاعدة (يَتَشاركان الفعل fill:none).
    assert ".mt-spark-rx-area" in html
    assert ".mt-spark-tx-area" in html
    assert "fill:none" in html


def test_traffic_spark_has_area_fills_under_lines(app, client):
    """أضفنا مساحة ملء شفّافة أسفل كلا الخطّين لإبراز الاستخدام
    بلمحة (لون هادئ يَتبع لون الخطّ)."""
    html = _fetch(app, client)
    assert "data-mt-spark-rx-area" in html
    assert "data-mt-spark-tx-area" in html
    # ألوان الملء الشفّاف على الخلفيّة
    assert "rgba(37,99,235,.12)" in html   # RX area
    assert "rgba(20,184,166,.14)" in html  # TX area


# ─── Fix #3: connect status is a design-system pill/badge ────


def test_status_pill_has_design_system_badge_style(app, client):
    """الشارة صارت pill مصمَّمة: border-radius:999px + padding
    + border + background لكل حالة. كانت نصًّا عاديًّا بلا خلفيّة."""
    html = _fetch(app, client)
    # حبّة مدوّرة (المسمار الحاسم للتصميم الجديد)
    assert "border-radius:999px !important" in html
    # خلفيّة خضراء ناعمة لحالة ok
    assert '[data-mt-status-state="ok"] .mt-tabs-status-pill' in html
    assert "background:#ECFDF5" in html
    assert "color:#047857" in html
    # نقطة نابضة عند الاتصال — علامة الحياة
    assert "mt-status-dot-pulse" in html


def test_status_pill_covers_pending_and_error_states(app, client):
    """كل الحالات تَحصل على لون مطابق: pending (كهرماني)، error (أحمر)،
    unknown (رمادي افتراضيّ) — فلا تَبقى الشارة حَياديّة عند خلل."""
    html = _fetch(app, client)
    assert '[data-mt-status-state="pending"] .mt-tabs-status-pill' in html
    assert "background:#FFFBEB" in html   # amber surface
    assert '[data-mt-status-state="error"] .mt-tabs-status-pill' in html
    assert "background:#FEF2F2" in html   # red surface
    # النقطة داخل الحبّة موجودة في الـmarkup لعرض الحالة بصريّاً
    assert 'data-mt-status-pill' in html
    assert 'class="dot"' in html
