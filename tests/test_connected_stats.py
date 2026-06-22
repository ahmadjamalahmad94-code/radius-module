# -*- coding: utf-8 -*-
"""«إحصائيات المتصلين» — 3 أنماط من radacct + radpostauth.

يثبّت السيمانتيك الأساسي: «جلسات فريدة» = كل مشترك/بطاقة مرّة واحدة مهما أعاد
الاتصال (distinct username) ≠ «كل الجلسات» (كل الصفوف). + نمط الفاشلة يقرأ
radpostauth (Access-Reject)، الدونات حسب NAS، الذروة + متوسط المدّة، والفلترة
بالفترة. شغّل الملف وحده."""
from __future__ import annotations

import datetime as _dt
import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "cstats.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield application


TODAY = _dt.datetime.utcnow().strftime("%Y-%m-%d")
YDAY = (_dt.datetime.utcnow() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")


def _seed(app):
    """يبني سيناريو: بطاقة محل shop1 تعيد الاتصال 5 مرّات (الساعة 08) على
    برج-المركز، مشترك u2 مرّة (الساعة 09) على IP آخر؛ 3 محاولات فاشلة (10)
    ومحاولة ناجحة واحدة (تُتجاهَل)؛ + صفّ بالأمس يجب أن يُستبعد من «اليوم»."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,address,secret,vendor,created_at) "
                      "VALUES(1,'برج-المركز','10.0.0.1','s','mikrotik',?)", (TODAY + "T00:00:00Z",))
            for i in range(5):
                c.execute("INSERT INTO radacct(tenant_id,username,nasipaddress,acctstarttime,acctsessiontime) "
                          "VALUES(1,'shop1','10.0.0.1',?,600)", (TODAY + "T08:%02d:00Z" % (i * 2),))
            c.execute("INSERT INTO radacct(tenant_id,username,nasipaddress,acctstarttime,acctsessiontime) "
                      "VALUES(1,'u2','10.0.0.2',?,1200)", (TODAY + "T09:30:00Z",))
            # yesterday session — must be excluded from today's window
            c.execute("INSERT INTO radacct(tenant_id,username,nasipaddress,acctstarttime,acctsessiontime) "
                      "VALUES(1,'old','10.0.0.1',?,300)", (YDAY + "T08:00:00Z",))
            for i in range(3):
                c.execute("INSERT INTO radpostauth(tenant_id,username,reply,authdate,nas) "
                          "VALUES(1,'baduser','Access-Reject',?,'برج-المركز')", (TODAY + " 10:%02d:00" % i,))
            c.execute("INSERT INTO radpostauth(tenant_id,username,reply,authdate,nas) "
                      "VALUES(1,'good','Access-Accept',?,'برج-المركز')", (TODAY + " 10:30:00",))


def _stats(app, mode):
    with app.app_context():
        from app.radius.services import connected_stats as cs
        return cs.stats(1, mode=mode, date_from=TODAY, date_to=TODAY)


# ════════════ (1) السيمانتيك الأساسي: فريدة ≠ كل ════════════
def test_unique_counts_each_subscriber_once(app):
    _seed(app)
    s = _stats(app, "unique")
    # shop1 أعاد الاتصال 5×، u2 مرّة → فريدة = 2 (لا 6).
    assert s["session_count"] == 2
    assert s["count_label"] == "جلسة فريدة"


def test_all_counts_every_session(app):
    _seed(app)
    s = _stats(app, "all")
    assert s["session_count"] == 6          # 5 shop1 + 1 u2


def test_unique_vs_all_differ_on_reconnect(app):
    _seed(app)
    assert _stats(app, "unique")["session_count"] < _stats(app, "all")["session_count"]


# ════════════ (2) نمط الفاشلة يقرأ radpostauth ════════════
def test_failed_mode_reads_reject_log(app):
    _seed(app)
    s = _stats(app, "failed")
    assert s["session_count"] == 3          # 3 رفض، والقبول مُتجاهَل
    assert s["count_label"] == "محاولة فاشلة"
    assert s["avg_duration_sec"] == 0       # لا مدّة للمحاولات


# ════════════ (3) الدونات حسب NAS — «الاسم (IP)» ════════════
def test_donut_by_nas_unique_vs_all(app):
    _seed(app)
    du = {d["label"]: d["count"] for d in _stats(app, "unique")["donut"]}
    da = {d["label"]: d["count"] for d in _stats(app, "all")["donut"]}
    # 10.0.0.1 يطابق جهازًا → «الاسم (IP)»؛ 10.0.0.2 بلا جهاز → الـIP وحده.
    assert du.get("برج-المركز (10.0.0.1)") == 1 and du.get("10.0.0.2") == 1
    assert da.get("برج-المركز (10.0.0.1)") == 5 and da.get("10.0.0.2") == 1


def test_donut_label_name_then_ip_and_ip_only_fallback(app):
    """التسمية = «الاسم (IP)» للمطابق، والـIP وحده للذي بلا جهاز."""
    _seed(app)
    labels = [d["label"] for d in _stats(app, "all")["donut"]]
    assert "برج-المركز (10.0.0.1)" in labels   # الاسم أساسي، الـIP بين قوسين
    assert "10.0.0.2" in labels                # ارتداد IP-فقط (لا جهاز)
    # لا تسمية «اسم فقط» بلا IP لعنوان مطابق
    assert "برج-المركز" not in labels


def test_donut_failed_resolves_ip_nas_to_name(app):
    """نمط الفاشلة: nas نصّي موجود يبقى كما هو، وnas كـIP مطابق → «الاسم (IP)»."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,address,secret,vendor,created_at) "
                      "VALUES(1,'برج-المركز','10.0.0.1','s','mikrotik',?)", (TODAY + "T00:00:00Z",))
            # nas كـIP يطابق الجهاز → يجب أن يصبح «برج-المركز (10.0.0.1)»
            for i in range(2):
                c.execute("INSERT INTO radpostauth(tenant_id,username,reply,authdate,nas) "
                          "VALUES(1,'baduser','Access-Reject',?,'10.0.0.1')", (TODAY + " 10:0%d:00" % i,))
            # nas نصّي غير مطابق (اسم خام) → يبقى كما هو
            c.execute("INSERT INTO radpostauth(tenant_id,username,reply,authdate,nas) "
                      "VALUES(1,'x','Access-Reject',?,'hotspot-edge')", (TODAY + " 11:00:00",))
    d = {x["label"]: x["count"] for x in _stats(app, "failed")["donut"]}
    assert d.get("برج-المركز (10.0.0.1)") == 2   # IP مطابق → اسم+IP
    assert d.get("hotspot-edge") == 1            # اسم خام غير مطابق → كما هو


def test_donut_failed_by_nas_name(app):
    _seed(app)
    d = {x["label"]: x["count"] for x in _stats(app, "failed")["donut"]}
    # nas في البذرة اسم نصّي «برج-المركز» (لا IP) غير موجود بالخريطة → كما هو.
    assert d.get("برج-المركز") == 3


# ════════════ (4) الذروة + متوسط المدّة ════════════
def test_busiest_hour_and_avg_duration(app):
    _seed(app)
    s = _stats(app, "all")
    assert s["busiest_hour"] == 8           # 5 جلسات في الساعة 08
    assert s["avg_duration_sec"] == 700     # (600*5 + 1200) / 6
    assert _stats(app, "failed")["busiest_hour"] == 10


def test_hourly_buckets_24(app):
    _seed(app)
    h = _stats(app, "all")["hourly"]
    assert len(h) == 24 and h[8] == 5 and h[9] == 1 and sum(h) == 6


# ════════════ (5) الفلترة بالفترة تستبعد خارجها ════════════
def test_period_excludes_other_days(app):
    _seed(app)
    # اليوم: لا يحتسب جلسة الأمس (old) → all = 6 وليس 7.
    assert _stats(app, "all")["session_count"] == 6
    with app.app_context():
        from app.radius.services import connected_stats as cs
        wide = cs.stats(1, mode="all", date_from=YDAY, date_to=TODAY)
        assert wide["session_count"] == 7   # يشمل الأمس الآن


def test_empty_state_no_data(app):
    s = _stats(app, "unique")               # بلا بذور
    assert s["empty"] is True and s["session_count"] == 0
    assert s["busiest_hour"] is None and s["avg_duration_sec"] == 0


# ════════════ (6) الصفحة + الشريط الجانبي ════════════
def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def test_page_renders_all_modes(app):
    _seed(app)
    c = _client(app)
    for mode in ("unique", "all", "failed"):
        r = c.get(f"/admin/radius/connected-stats?mode={mode}")
        assert r.status_code == 200, mode
        html = r.get_data(as_text=True)
        assert "إحصائيات المتصلين" in html
        assert "جلسات فريدة" in html and "كل المحاولات الفاشلة" in html  # mode pills


def test_default_mode_is_unique(app):
    with app.app_context():
        from app.radius.services import connected_stats as cs
        assert cs.normalize_mode(None) == "unique"
        assert cs.stats(1)["mode"] == "unique"


def test_sidebar_link_present_in_subscribers_group(app):
    import os.path as p
    here = p.dirname(p.dirname(__file__))
    sb = open(p.join(here, "app", "templates", "admin", "_sidebar.html"), encoding="utf-8").read()
    # الرابط موجود + ضمن قائمة صلاحيات مجموعة المشتركين (_eps_subs).
    assert "radius.connected_stats" in sb
    assert "'connected_stats'" in sb
    subs_line = next(ln for ln in sb.splitlines() if "_eps_subs" in ln)
    assert "connected_stats" in subs_line


def test_endpoint_registered(app):
    assert "radius.connected_stats" in app.view_functions
    assert "radius.connected_stats_json" in app.view_functions


# ════════════ (7) أداة التسمية المشتركة nas_names ════════════
def test_nas_label_name_and_ip(app):
    with app.app_context():
        from app.radius.services.nas_names import nas_label
        nm = {"10.0.0.1": "برج-المركز"}
        # IP مطابق → «الاسم (IP)»
        assert nas_label("10.0.0.1", nm) == "برج-المركز (10.0.0.1)"
        # IP بلا مطابقة → الـIP وحده
        assert nas_label("10.0.0.9", nm) == "10.0.0.9"
        # قيمة فارغة → الارتداد
        assert nas_label("", nm) == "غير معروف"
        # اسم نصّي غير موجود بالخريطة → كما هو بلا تكرار
        assert nas_label("hotspot-edge", nm) == "hotspot-edge"


def test_nas_name_map_matches_address_and_vpn_peer(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.nas_names import nas_name_map
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,address,vpn_peer_address,"
                      "secret,vendor,created_at) VALUES(1,'برج النفق','10.50.0.1','192.168.1.9','s','mt',?)",
                      (TODAY + "T00:00:00Z",))
        m = nas_name_map(1)
        # يطابق العنوان المباشر وعنوان النفق على نفس الاسم
        assert m.get("10.50.0.1") == "برج النفق"
        assert m.get("192.168.1.9") == "برج النفق"


# ════════════ (8) إصلاح التباعد: أقسام الصفحة داخل uds-stack ════════════
def test_page_sections_wrapped_in_uds_stack():
    import os.path as p
    here = p.dirname(p.dirname(__file__))
    tpl = open(p.join(here, "app", "templates", "radius", "connected_stats.html"),
               encoding="utf-8").read()
    # الأقسام العليا ملفوفة بأداة التباعد المشتركة (فجوة رأسيّة موحّدة).
    assert 'class="uds-stack"' in tpl
    # يسبق الهيرو ويغلق بعد شبكة الدونات/الساعة.
    open_idx = tpl.index('class="uds-stack"')
    hero_idx = tpl.index("hub.megahero")
    grid_idx = tpl.index('class="cs-grid2"')
    assert open_idx < hero_idx < grid_idx


def test_uds_stack_provides_block_gap():
    """أداة uds-stack تعرّف فجوة رأسيّة (gap) ≥16px فلا تلتصق الأقسام."""
    import os.path as p
    here = p.dirname(p.dirname(__file__))
    css = open(p.join(here, "app", "static", "css", "unified_design.css"),
               encoding="utf-8").read()
    assert ".uds-stack{" in css.replace(" ", "")
    # الفجوة من متغيّر الكتلة (--uds-block-gap: 20px ≥ 16px) ويصفّر هوامش الأبناء.
    assert "--uds-block-gap:20px" in css.replace(" ", "")
