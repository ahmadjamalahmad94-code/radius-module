"""اختبارات وحدات لـapp.radius.services.audit_format — لا تحتاج Flask.

تُغطّي:
  • action_label: خريطة دقيقة، مُركِّب verb+noun، عدم الوقوع في
    «عملية على راوتر» الغامضة لمفاتيح معروفة الـnoun.
  • target_label_for + resolve_target_names: حلّ الراوتر إلى اسمه
    الفعلي عبر nas_devices (دفعة واحدة).
  • resolve_router_names: نفس الفكرة لعمود «الراوتر».
  • format_payload: جملة عربية مُوجزة لأنماط الحمولة الشائعة، بلا
    تسريب للسرّيات/المفاتيح الفنّية.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.radius.services import audit_format as af


# ─── action_label ──────────────────────────────────────────────


def test_action_label_uses_exact_map_first():
    """الخريطة الدقيقة تفوز على المُركّب — مفاتيح Hotspot/PPP/نسخ
    احتياطية لها ترجمات أكثر دقّة من المُركّب التلقائي."""
    assert af.action_label("mt.programming.hotspot.apply") == "تطبيق إعدادات Hotspot"
    assert af.action_label("mt.backup.create") == "إنشاء نسخة احتياطية"
    assert af.action_label("mt.port_services.bt_wifi_block.apply") == \
        "تفعيل منع مشاركة البلوتوث/الواي فاي"
    assert af.action_label("mt.port_services.loop_detect.loop_check") == \
        "فحص اللوب الحيّ"


def test_action_label_composer_verb_plus_noun():
    """مفتاح غير معرّف لكن مع verb+noun معروفَين → «verb noun»."""
    # mt.connection.test: verb=test (اختبار), noun=connection (اتصال)
    assert af.action_label("mt.connection.test") == "اختبار اتصال المايكروتيك"
    # subscriber.set_speed → verb=set, noun=speed
    assert af.action_label("subscriber.set_speed") == "ضبط سرعة المشترك"


def test_action_label_never_returns_vague_amaliya_ala():
    """تصحيح المالك: لا يجب أن يَخرج «عملية على راوتر» الغامضة لمفاتيح
    لها noun معروف. القاعدة الجديدة تستبدله بـ«إجراء {noun}» الأوضح،
    أو تأنيس الذيل حين لا نعرف شيئًا. لا يحوي الناتج «عملية على»."""
    samples = [
        "mt.unknown_thing",   # noun=mt
        "subscriber.foo",     # noun=subscriber
        "card.something_new", # verb=new + noun=card
        "session.weird_x",    # noun=session
    ]
    for s in samples:
        out = af.action_label(s)
        assert "عملية على" not in out, f"{s} → {out}"
        assert out != s, f"{s} should not echo the raw key"
        assert out.strip(), f"{s} produced empty label"


def test_action_label_humanizes_tail_when_no_match():
    """لا verb ولا noun معروف → تأنيس آخر مقطع. لا فراغ."""
    out = af.action_label("totally.unknown_event_x")
    # المقطع الأخير «unknown_event_x» يصبح «unknown event x»
    assert "unknown" in out
    assert "عملية على" not in out


def test_action_label_empty_or_none():
    assert af.action_label("") == "عملية"
    assert af.action_label(None) == "عملية"


# ─── resolve_router_names + resolve_target_names ─────────────


@pytest.fixture
def conn():
    """قاعدة بيانات سطحية بـ schema مصغّر للأعمدة التي نحتاجها فقط —
    لا حاجة لتطبيق Flask كامل ولا للترحيلات."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE nas_devices(
            id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE card_users(
            id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL,
            display_name TEXT NOT NULL
        );
        CREATE TABLE admins(
            id INTEGER PRIMARY KEY, full_name TEXT, username TEXT
        );
    """)
    c.execute("INSERT INTO nas_devices VALUES (17, 1, 'MT-HQ-Core')")
    c.execute("INSERT INTO nas_devices VALUES (13, 1, 'MT-Branch-A')")
    c.execute("INSERT INTO nas_devices VALUES (99, 2, 'OTHER-TENANT')")
    c.execute("INSERT INTO card_users VALUES (7, 1, 'أحمد علي')")
    c.execute("INSERT INTO admins VALUES (3, 'مدير الشبكة', 'netadmin')")
    c.execute("INSERT INTO admins VALUES (4, '', 'ops-user')")  # fallback to username
    c.commit()
    yield c
    c.close()


def test_resolve_router_names_batches_and_scopes_to_tenant(conn):
    rows = [{"router_id": 17}, {"router_id": 13},
            {"router_id": 99},  # tenant 2 — must NOT leak
            {"router_id": None}, {"router_id": ""}]
    out = af.resolve_router_names(rows, tenant_id=1, db_conn=conn)
    assert out == {17: "MT-HQ-Core", 13: "MT-Branch-A"}


def test_resolve_target_names_router_subscriber_admin(conn):
    rows = [
        # هدف راوتر — تصحيح المالك الرئيسي.
        {"target_type": "mikrotik_nas", "target_id": "17"},
        # هدف admin — اسم كامل.
        {"target_type": "admin", "target_id": "3"},
        # هدف admin بدون full_name — fallback إلى username.
        {"target_type": "admin", "target_id": "4"},
        # هدف card_user.
        {"target_type": "card_user", "target_id": "7"},
        # نوع غير محلول — يبقى بلا اسم.
        {"target_type": "subscriber", "target_id": "user-1034"},
    ]
    out = af.resolve_target_names(rows, tenant_id=1, db_conn=conn)
    assert out.get(("mikrotik_nas", "17")) == "MT-HQ-Core"
    assert out.get(("admin", "3")) == "مدير الشبكة"
    assert out.get(("admin", "4")) == "ops-user"
    assert out.get(("card_user", "7")) == "أحمد علي"
    # subscriber لم يُحلَّل (المعرّف نصيّ غير رقمي)
    assert ("subscriber", "user-1034") not in out


def test_target_label_for_uses_resolved_name(conn):
    """هذا هو السلوك الذي طلبه المالك: «هدف 17» يصبح «MT-HQ-Core»."""
    rows = [{"target_type": "mikrotik_nas", "target_id": "17"}]
    names = af.resolve_target_names(rows, tenant_id=1, db_conn=conn)
    assert af.target_label_for("mikrotik_nas", "17", names) == "MT-HQ-Core"


def test_target_label_for_falls_back_to_ar_type_plus_id():
    """بلا names أو بلا تطابق: «المايكروتيك #17» وليس «هدف (17)»."""
    assert af.target_label_for("mikrotik_nas", "17") == "المايكروتيك #17"
    assert af.target_label_for("router", 42) == "المايكروتيك #42"
    assert af.target_label_for("card_user", "5") == "مستخدم بطاقة #5"
    # نوع غير معروف يبقى كما هو + المعرّف
    assert af.target_label_for("weird_type", "9") == "weird_type #9"


def test_target_label_for_text_id_passes_through():
    """معرّف نصيّ (username) يُعرض حرفيًّا بين قوسين — لا «#username»."""
    assert af.target_label_for("subscriber", "user1034") == "مشترك (user1034)"


# ─── format_payload ──────────────────────────────────────────


def test_format_payload_port_services():
    """نمط خدمات المنافذ: المنافذ + النتيجة، بترتيب مقروء بالعربية."""
    p = {"ports": ["ether2", "ether3", "ether4"], "ok": True,
         "slug": "loop_detect"}
    out = af.format_payload("mt.port_services.loop_detect.apply", p)
    assert "المنافذ: ether2, ether3, ether4" in out
    assert "الخدمة: loop_detect" in out
    assert "النتيجة: نعم" in out


def test_format_payload_amount_currency():
    p = {"amount": 50, "currency": "ILS", "reason": "تجديد"}
    out = af.format_payload("subscriber.payment", p)
    assert "المبلغ: 50" in out
    assert "العملة: ILS" in out
    assert "السبب: تجديد" in out


def test_format_payload_skips_secrets_and_technical_keys():
    """نتأكّد أنّ أيّ مفتاح يحتوي «password» / «secret» / target_*/
    router_* / api_* لا يَخرج في الجملة (السرّيات مُعمّاة من الـrepo
    أصلًا، لكن نحذف المفاتيح حتى لا تُلوّث الجملة بـ ***)."""
    p = {
        "router_id": 17, "target_id": "x", "csrf": "tok",
        "api_password": "***", "api_user": "admin",
        "what": "ok",
    }
    out = af.format_payload("mt.x", p)
    assert "router_id" not in out
    assert "target_id" not in out
    assert "password" not in out
    assert "api_user" not in out
    assert "csrf" not in out
    assert "what: ok" in out


def test_format_payload_empty_returns_empty_string():
    assert af.format_payload("mt.x", None) == ""
    assert af.format_payload("mt.x", {}) == ""


def test_format_payload_truncates_long_strings():
    p = {"error": "x" * 500}
    out = af.format_payload("mt.x", p)
    # العنوان يتقلّص إلى 120 محرفًا تقريبًا + «…»
    assert "…" in out
    assert len(out) < 200
