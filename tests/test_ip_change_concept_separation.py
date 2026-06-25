"""فصل مفهومَي «تغيير IP» (طلب المالك، يونيو 2026).

يُثبت أنّ السطحين منفصلان وواضحان ولا يُخلَطان:

  (A) «تغيير IP الجلسة الداخلية (CoA)» — مجّانيّ، داخليّ/LAN. يُغيّر
      Framed-IP-Address لجلسة حيّة عبر CoA. يَعيش في صفحة «المتصلون»
      وبطاقة-اختصار مستقلّة على لوحة الراوتر.

  (B) «تغيير عنوان التصفح العام (Public)» — مدفوع، إنترنت. يُغيّر عنوان
      الخروج العامّ لكامل الراوتر. يَعيش في صفحة /ip-change وبطاقة
      public-ip المدفوعة + الكتالوج.

القاعدة: لا بطاقة/تسمية واحدة تَجمع المفهومَين، ولا يُشير زرّ عنوانه
«عام/مدفوع» إلى تدفّق CoA الداخليّ المجّانيّ (الخلط الأصليّ).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ipsep_")
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
    username = f"ipsep_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username, password="pw",
        full_name="IP Sep Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "pw"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_router(app, *, nas_id: int, address: str = "203.0.113.90") -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                VALUES (?, 1, 'sep-gw', ?, 's', 'mikrotik', 'pppoe', 1,
                        ?, 'direct')
                """,
                (nas_id, address, now),
            )


# ── (1) لوحة الراوتر: بطاقتان متمايزتان، لا واحدة مختلطة ──────────
def test_router_dashboard_has_two_distinct_cards(app, client):
    _seed_router(app, nas_id=90, address="203.0.113.90")
    _login(client)
    html = client.get("/admin/radius/mt/90/dashboard").get_data(as_text=True)

    # كلا البطاقتين حاضرتان بتسميتيهما الواضحتين.
    assert "تغيير عنوان التصفح العام (Public)" in html       # (B)
    assert "تغيير IP الجلسة الداخلية (CoA)" in html          # (A)


def test_public_card_does_not_carry_coa_deeplink(app, client):
    """الخلط الأصليّ: بطاقة «عام/مدفوع» زرّها الأساسيّ = تدفّق CoA
    الداخليّ. بعد الفصل: داخل بطاقة public-ip لا يوجد data-rh-coa-setip
    ولا «تطبيق IP حيّ» — فعلها الوحيد «طلب تفعيل» المدفوع."""
    _seed_router(app, nas_id=91, address="203.0.113.91")
    _login(client)
    html = client.get("/admin/radius/mt/91/dashboard").get_data(as_text=True)

    idx = html.find('data-rh-svc-card="public-ip"')
    assert idx >= 0
    card_open = html.rfind("<div", 0, idx)
    card_close = html.find("</div>", idx)
    card_html = html[card_open:card_close]

    # الفعل المدفوع موجود، وتدفّق CoA الداخليّ غائب من هذه البطاقة.
    assert 'data-svc-type="public-ip"' in card_html
    assert "data-rh-coa-setip" not in card_html, \
        "public card must NOT carry the internal CoA deep-link"
    assert "تطبيق IP حيّ" not in card_html


def test_internal_card_carries_coa_deeplink_and_is_free(app, client):
    """بطاقة (A) المستقلّة هي حاملة الـCoA deep-link، بلا شارة «مدفوعة»."""
    _seed_router(app, nas_id=92, address="203.0.113.92")
    _login(client)
    html = client.get("/admin/radius/mt/92/dashboard").get_data(as_text=True)

    # العثور على وسم <a> الذي يَحمل deep-link الـCoA + التحقّق من الوجهة.
    m = re.search(r'<a[^>]*\bdata-rh-coa-setip\b[^>]*>', html)
    assert m, "internal-session card must carry the CoA deep-link <a>"
    tag = m.group(0)
    href = re.search(r'href="([^"]+)"', tag).group(1).replace("&amp;", "&")
    assert "/admin/radius/online" in href
    assert "hint=coa_setip" in href
    assert "nas=203.0.113.92" in href
    # نطاق البطاقة الداخليّة (من وسم <a> حتى إغلاقه) — لا شارة «مدفوعة».
    a_close = html.find("</a>", m.end())
    inner = html[m.start():a_close]
    assert "مدفوعة" not in inner
    assert "تغيير IP الجلسة الداخلية (CoA)" in inner


# ── (2) صفحة /ip-change = الخدمة العامّة المدفوعة (B) ──────────────
def test_public_ip_page_is_clearly_public(app, client):
    _seed_router(app, nas_id=93, address="203.0.113.93")
    _login(client)
    html = client.get("/admin/radius/ip-change").get_data(as_text=True)
    assert "تغيير عنوان التصفح العام (Public)" in html
    # تُميّز نفسها صراحةً عن تغيير IP الجلسة الداخلية.
    assert "الجلسة الداخلية" in html


# ── (3) صفحة «المتصلون» = تغيير IP الجلسة الداخلية (A) ─────────────
def test_sessions_modal_is_clearly_internal(app, client):
    _seed_router(app, nas_id=94, address="203.0.113.94")
    _login(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    # عنوان النافذة الداخليّ + ملاحظة التمييز عن العامّ.
    assert "تغيير IP الجلسة الداخلية (LAN) عبر CoA" in html
    assert "وليس عنوان التصفّح العامّ" in html


# ── (4) التسميات الكنونيّة موحّدة على «العامّ» ─────────────────────
def test_service_labels_are_public_named():
    from app.radius.services.service_specs import service_label
    assert service_label("ip_change") == "تغيير عنوان التصفح العام (Public)"
    assert service_label("public-ip") == "تغيير عنوان التصفح العام (Public)"
