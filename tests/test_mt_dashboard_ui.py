"""K9 — MikroTik dashboard UI smoke tests.

The dashboard must:
- Be login-guarded (the global guard installed by the radius
  blueprint redirects anon visitors to the login page).
- Render the shell without requiring a live MikroTik connection.
- Carry every stable `data-mt-*` marker the JS + future tests rely
  on.
- 404 when the nas_id doesn't exist.
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
    tmp = tempfile.mkdtemp(prefix="hr_mt_dash_")
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

    username = f"mt_dash_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username,
        password="dash-pass",
        full_name="Dashboard Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "dash-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    """Surfaces the session CSRF token — required for JSON POSTs in tests."""
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed_router(app, *, nas_id: int, name: str = "rtr-test",
                 address: str = "203.0.113.50") -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot', 1,
                        ?, 'direct')
                """,
                (nas_id, name, address, now),
            )


def test_dashboard_route_is_login_guarded(client):
    res = client.get("/admin/radius/mt/1/dashboard", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_dashboard_renders_shell_and_markers(app, client):
    _seed_router(app, nas_id=1, name="main-gw", address="203.0.113.10")
    _login(client)

    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Stable markers — every later K9.x commit + every external
    # automated test depends on these strings being literally
    # present.
    assert "data-mt-dashboard" in html
    assert 'data-mt-router-id="1"' in html
    assert 'data-mt-api-base="/api/v1"' in html
    # Token comes from the dev env; non-empty in test mode.
    assert "data-mt-api-token=" in html

    assert "data-mt-kpi-strip" in html
    assert "data-mt-status" in html

    # KPI cards each carry their kind. JS fills them later from
    # /system/overview — the page itself just renders the shell.
    for kind in ("uptime", "cpu", "memory", "temperature",
                 "version", "dialed"):
        assert f'data-mt-kpi="{kind}"' in html

    # K9.2 panels — markers must be in place from this commit on.
    assert "data-mt-live-traffic" in html
    assert "data-mt-interface-select" in html
    assert "data-mt-traffic-rx" in html
    assert "data-mt-traffic-tx" in html
    assert "data-mt-spark" in html
    assert "data-mt-active-users" in html
    assert "data-mt-hotspot-count" in html
    assert "data-mt-ppp-count" in html
    assert "data-mt-active-users-rows" in html

    # K9.3 quick-actions: every required marker is live.
    assert "data-mt-quick-actions" in html
    assert "data-mt-action-backup" in html
    assert "data-mt-action-reboot" in html
    assert "data-mt-action-ping" in html
    assert "data-mt-action-identity" in html
    assert "data-mt-action-result" in html
    assert "data-mt-action-form" in html
    assert "data-rh-loop-tile" in html
    assert "تتبّع اللوب" in html
    assert "كشف اللوب عبر مجس DHCP على منافذ الزبائن" in html

    # The router name lands in the title + meta strip.
    assert "main-gw" in html
    assert "203.0.113.10" in html


def test_dashboard_loop_tile_stays_renderable_with_probe(app, client):
    _seed_router(app, nas_id=3, name="loop-rtr", address="203.0.113.30")
    with app.app_context():
        from app.radius.db.repos import router_loop_probes_repo
        router_loop_probes_repo.upsert_reading(
            tenant_id=1,
            router_id=3,
            interface="ether2",
            status="bound",
            lease_ip="10.0.0.8/24",
            server_ip="10.0.0.1",
        )
    _login(client)

    res = client.get("/admin/radius/mt/3/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-rh-loop-tile" in html
    assert "مفعّل" in html


def test_dashboard_returns_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/dashboard")
    assert res.status_code == 404


def test_dashboard_does_NOT_require_live_mikrotik_to_render(app, client):
    """The page shell must come up even when the wire client is
    completely unable to reach the router — JS is what does the
    fetch, and a failed fetch only paints an in-page error chip."""
    _seed_router(app, nas_id=2, name="offline-rtr", address="10.0.0.1")
    _login(client)

    # Note: we do NOT monkeypatch any pool here. The page render
    # path must not touch the router at all.
    res = client.get("/admin/radius/mt/2/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "offline-rtr" in html
    # JS will report the error to the operator; the shell still
    # shows the pending status pill.
    assert "جارٍ الاتصال" in html


# ──────────────────────────────────────────────────────────────
# يونيو 2026 — قاعدة المالك: حالة الخدمات تنبع من قراءة الراوتر
# الحيّة فقط، لا من DB، ولا من «آخر apply سابق». هذه الاختبارات
# تُثبّت السلوك:
#   (A) النقطة الخضراء «فعّال» تتطلّب probe ناجحًا من /inventory
#       — وإلا تبقى is-unknown (رماديّة).
#   (B) كل الخدمات تظهر دائمًا كبطاقات: bt_wifi_block, loop_detect,
#       public-ip (المدفوعة) — لا واحدة منها يختفي عند تعطّل الراوتر.
#   (C) بطاقة الخدمة المدفوعة (public-ip) تحمل data-pss-paid
#       وعنوان «مدفوعة» وتفتح نافذة طلب التفعيل (paid-req).
# ──────────────────────────────────────────────────────────────


def test_dashboard_status_dots_start_unknown_not_active(app, client):
    """قاعدة (A): حتى لو حُفِظت حالة «مفعّلة» سابقًا في tenant_settings
    أو وُجدت probes للوب، البطاقات تُرسَم بنقطة is-unknown أوّلاً —
    لا يُسمح بـis-active بلا تأكيد حيّ من الراوتر عبر /inventory."""
    _seed_router(app, nas_id=11, name="offline-rtr", address="10.0.0.11")
    with app.app_context():
        from app.radius.db.repos import (
            router_loop_probes_repo, tenants_repo,
        )
        # حالة «مفعّلة» مزيّفة لو اعتمدنا DB فقط (قاعدة المالك ترفض).
        router_loop_probes_repo.upsert_reading(
            tenant_id=1, router_id=11, interface="ether2",
            status="bound", lease_ip="10.0.0.8/24", server_ip="10.0.0.1",
        )
        tenants_repo.set_setting(1, "pss.11.bt_wifi_block.enabled", "1", by=0)
        tenants_repo.set_setting(1, "pss.11.bt_wifi_block.ports", "ether2", by=0)
        tenants_repo.set_setting(1, "pss.11.loop_detect.enabled", "1", by=0)
    _login(client)

    res = client.get("/admin/radius/mt/11/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # كل بطاقات الخدمات بـdata-rh-svc-card تبدأ بنقطة is-unknown، لا is-active.
    for slug in ("bt_wifi_block", "loop_detect", "public-ip",
                 "hotspot", "broadband"):
        assert f'data-rh-svc-card="{slug}"' in html, (
            f"بطاقة الخدمة {slug} مفقودة من شبكة «خدماتي»"
        )
    # baseline ينبغي أن يكون is-unknown — لا is-active قبل تأكيد حيّ.
    # نفتّش حول بطاقة bt_wifi_block ولوب لنتحقّق.
    btw_idx = html.find('data-rh-svc-card="bt_wifi_block"')
    btw_block = html[btw_idx:btw_idx + 1400]
    assert "is-unknown" in btw_block
    assert "np-svc-status-dot is-active" not in btw_block
    loop_idx = html.find('data-rh-svc-card="loop_detect"')
    loop_block = html[loop_idx:loop_idx + 1400]
    assert "is-unknown" in loop_block
    assert "np-svc-status-dot is-active" not in loop_block


def test_dashboard_shows_all_services_including_paid_public_ip(app, client):
    """قاعدة (B): الخدمات الثلاث «منع البث» + «تتبّع اللوب» +
    «تغيير IP الخروج» (المدفوعة) تظهر كلّها كبطاقات دائمًا — حتى لو
    الراوتر مفصول (لا اتصال أصلًا)."""
    _seed_router(app, nas_id=12, name="all-svc", address="203.0.113.12")
    _login(client)

    res = client.get("/admin/radius/mt/12/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # «منع البث (بلوتوث/واي فاي)» — بطاقة bt_wifi_block ظاهرة.
    assert 'data-rh-svc-card="bt_wifi_block"' in html
    assert "منع البث" in html
    # «تتبّع اللوب» — بطاقة loop_detect ظاهرة.
    assert 'data-rh-svc-card="loop_detect"' in html
    assert "تتبّع اللوب" in html
    # «تغيير IP الخروج» — بطاقة مدفوعة جديدة (public-ip).
    assert 'data-rh-svc-card="public-ip"' in html
    # تحديث (يونيو 2026): البطاقة المدفوعة هاجرت إلى نافذة المواصفات
    # الموحّدة — لا data-pss-paid بعد الآن، بل data-svc-spec-modal-open
    # data-svc-type="public-ip" data-svc-action="activate".
    assert 'data-svc-type="public-ip"' in html
    assert 'data-svc-action="activate"' in html
    assert "تغيير IP الخروج" in html
    assert "مدفوعة" in html
    # نافذة المواصفات الموحّدة مضمَّنة في الصفحة (data-ssm-modal).
    assert 'data-ssm-modal' in html
    assert 'data-ssm-spec' in html


def test_service_request_route_persists_and_audits(app, client):
    """قاعدة (C): POST /admin/radius/mt/<id>/service-request يحفظ
    الطلب في tenant_settings + يسجّل حدث mt.service_request.create
    لمراجعة المالك."""
    _seed_router(app, nas_id=13, name="paid-target", address="203.0.113.13")
    _login(client)
    token = _csrf(client)

    res = client.post(
        "/admin/radius/mt/13/service-request",
        json={"slug": "public-ip", "mb": 2048, "message": "أحتاج هذه الخدمة"},
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()
    assert data and data.get("ok") is True
    assert data.get("service_label") == "تغيير IP الخروج"

    with app.app_context():
        from app.radius.db.repos import audit_repo, tenants_repo
        # الإعداد المُخزَّن: مفتاح يبدأ بـpss.request.public-ip.13.
        prefix = "pss.request.public-ip.13."
        # ابحث في tenant_settings عبر استعلام مباشر.
        from app.radius.db.connection import db
        rows = db().execute(
            "SELECT key, value FROM tenant_settings "
            "WHERE tenant_id=1 AND key LIKE ?",
            (prefix + "%",),
        ).fetchall()
        assert rows, "طلب التفعيل لم يُخزَّن في tenant_settings"
        import json as _json
        payload = _json.loads(dict(rows[0])["value"])
        assert payload["slug"] == "public-ip"
        assert payload["mb"] == 2048
        assert payload["status"] == "pending"
        assert payload["nas_id"] == 13
        # حدث في audit_log يحمل الإجراء الصحيح.
        events = audit_repo.recent(1, limit=20)
        actions = [r["action"] for r in events]
        assert "mt.service_request.create" in actions


def test_service_request_rejects_bad_inputs(app, client):
    """تحقّق المدخلات: slug غير صالح، أو mb<=0، أو راوتر مفقود."""
    _seed_router(app, nas_id=14, name="paid-target", address="203.0.113.14")
    _login(client)
    token = _csrf(client)
    hdrs = {"X-CSRFToken": token}

    # mb=0 ⇒ 400
    r1 = client.post("/admin/radius/mt/14/service-request",
                     json={"slug": "public-ip", "mb": 0}, headers=hdrs)
    assert r1.status_code == 400

    # slug فارغ ⇒ 400
    r2 = client.post("/admin/radius/mt/14/service-request",
                     json={"slug": "", "mb": 1024}, headers=hdrs)
    assert r2.status_code == 400

    # راوتر غير موجود ⇒ 404
    r3 = client.post("/admin/radius/mt/9999/service-request",
                     json={"slug": "public-ip", "mb": 1024}, headers=hdrs)
    assert r3.status_code == 404


# ──────────────────────────────────────────────────────────────
# يونيو 2026 — إعادة تصميم «خدماتي» (fix/my-services-redesign).
# يُثبّت هذا القسم القرارات الثلاثة التي يُصدّرها التصميم الجديد:
#   (1) شبكة بطاقات الخدمات 3 أعمدة على الشاشات الواسعة.
#   (2) لا شارة «ترقية» على hotspot ولا broadband (خدمات مفتوحة).
#   (3) قسم «الخدمات المُضافة» يَستخدم تصميم بطاقات (.rh-grp) لا
#       تدفّقًا مُسطَّحًا، والـJS يَبني .rh-grp/.rh-grp-head/.rh-grp-body.
# ──────────────────────────────────────────────────────────────


def test_redesign_card_grid_is_three_columns_wide(app, client):
    """شبكة البطاقات: 3 أعمدة على سطح المكتب الواسع."""
    _seed_router(app, nas_id=41, name="grid-rtr", address="203.0.113.41")
    _login(client)
    res = client.get("/admin/radius/mt/41/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # قاعدة الـCSS الأساسيّة (3 أعمدة) لا بدّ من حضورها.
    assert "grid-template-columns:repeat(3, minmax(0, 1fr))" in html
    # خطوة استجابة متوسّطة (≤1080px) تَنزل لعمودَين.
    assert "max-width: 1080px" in html
    # خطوة الجوال (≤640px) تَنزل لعمود واحد.
    assert "max-width: 640px" in html


def test_redesign_hotspot_and_broadband_have_no_upgrade_pill(app, client):
    """قاعدة (2): الـhotspot والـbroadband خدمات مفتوحة — لا «ترقية»."""
    _seed_router(app, nas_id=42, name="open-rtr", address="203.0.113.42")
    _login(client)
    res = client.get("/admin/radius/mt/42/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # حدّد بطاقة hotspot كاملةً ثم تأكّد أنها لا تَحوي pill.
    h_idx = html.find('data-rh-svc-card="hotspot"')
    assert h_idx >= 0
    hotspot_card = html[h_idx: html.find("</a>", h_idx)]
    assert 'data-svc-type="hotspot"' not in hotspot_card
    assert 'ssm-upgrade-pill' not in hotspot_card

    # وكذلك broadband.
    b_idx = html.find('data-rh-svc-card="broadband"')
    assert b_idx >= 0
    broadband_card = html[b_idx: html.find("</a>", b_idx)]
    assert 'data-svc-type="broadband"' not in broadband_card
    assert 'ssm-upgrade-pill' not in broadband_card


def test_redesign_always_on_cards_drop_pills_paid_keeps_pill(app, client):
    """يونيو 2026 (طلب المالك): bt_wifi_block وloop_detect صارتا «دائمتي
    الإتاحة» — بطاقتاهما رابط <a> مباشر للصفحة المخصّصة بلا شارة «تفعيل/
    ترقية» (لا data-svc-type على البطاقة). الخدمة المدفوعة (public-ip)
    تَحتفظ بشارتها data-svc-type. (كانت الخدمتان تَحملان شارات قبل
    تحويلهما لنموذج «دائم الإتاحة».)"""
    _seed_router(app, nas_id=43, name="other-rtr", address="203.0.113.43")
    _login(client)
    res = client.get("/admin/radius/mt/43/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    for slug in ("bt_wifi_block", "loop_detect"):
        idx = html.find(f'data-rh-svc-card="{slug}"')
        assert idx >= 0, f"missing {slug} card"
        opening = html[html.rfind("<", 0, idx): html.find(">", idx)]
        card_html = html[idx: html.find("</a>", idx)]
        # بطاقة رابط مباشر لصفحة الخدمة المخصّصة (لا زرّ فعل عامّ).
        assert opening.lstrip().startswith("<a"), f"{slug}: not a direct <a> link"
        assert "port-services" in opening and f"slug={slug}" in opening, \
            f"{slug}: card not linked to its dedicated page"
        # لا شارتا تفعيل/ترقية ولا data-svc-type على البطاقة دائمة الإتاحة.
        assert "ssm-pill--activate" not in card_html, f"{slug}: stale activate pill"
        assert "ssm-pill--upgrade" not in card_html, f"{slug}: stale upgrade pill"
        assert f'data-svc-type="{slug}"' not in card_html, f"{slug}: stale data-svc-type"
    # المدفوعة (public-ip) ما زالت تَحمل شارتها — التصميم لم يَلمسها.
    assert 'data-svc-type="public-ip"' in html


def test_redesign_added_services_uses_new_grp_classes(app, client):
    """قاعدة (3): قسم «الخدمات المُضافة» — الـCSS الجديد للـ.rh-grp
    حاضر، والـJS يَبني سكشن .rh-grp / .rh-grp-head / .rh-grp-body."""
    _seed_router(app, nas_id=44, name="grp-rtr", address="203.0.113.44")
    _login(client)
    res = client.get("/admin/radius/mt/44/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # CSS الجديد للحاوية الجماعيّة + الترويسة + الجسم.
    assert ".rh-grp{" in html
    assert ".rh-grp-head{" in html
    assert ".rh-grp-name{" in html
    assert ".rh-grp-count{" in html
    assert ".rh-grp-body{" in html

    # الـJS يَبني سكشن <section class="rh-grp"> ووصفه.
    assert '<section class="rh-grp"' in html
    assert '<header class="rh-grp-head">' in html
    assert "rh-grp-ico" in html
    assert '<div class="rh-grp-body">' in html


# ──────────────────────────────────────────────────────────────
# يونيو 2026 — round 2 على fix/my-services-redesign:
#   (4) تَناسُق حالة البطاقة الواحدة. لا يَجوز لبطاقة أن تَعرض
#       «مفعّلة» + «مدفوعة» + «طلب التفعيل» في آنٍ معًا.
#   (5) ارتفاع موحَّد لبطاقات «الخدمات المُضافة»: grid-auto-rows:1fr
#       + min-height + height:100% على .rh-grp.
# ──────────────────────────────────────────────────────────────


def test_redesign_paid_card_is_not_button_modal_trigger(app, client):
    """قاعدة الـcoherence: بطاقة «تغيير IP الخروج» (المدفوعة) لا تَعمل
    كزرّ يَفتح المودال. التَّفاعل محصور في pill «طلب التفعيل»."""
    _seed_router(app, nas_id=51, name="paid-rtr", address="203.0.113.51")
    _login(client)
    res = client.get("/admin/radius/mt/51/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # حدّد البطاقة المدفوعة كاملةً.
    idx = html.find('data-rh-svc-card="public-ip"')
    assert idx >= 0
    # نَبحث للوراء عن وسم الفتح، وللأمام عن الإغلاق الموافق.
    open_lt = html.rfind("<", 0, idx)
    # البطاقة الآن div (لا button) — لا تَفتح المودال على نَقرة عامّة.
    assert html[open_lt:idx].startswith("<div"), \
        "paid card should be a <div>, not <button>/<a>"

    # البطاقة نفسها لا تَحمل data-svc-spec-modal-open.
    close_div = html.find("</div>", idx)
    card_html = html[idx:close_div]
    # تَحمل pill activate داخلها (الزرّ الوحيد المسؤول عن المودال).
    assert 'data-svc-action="activate"' in card_html
    assert 'class="ssm-upgrade-pill ssm-pill--activate"' in card_html
    # البطاقة الأمّ بنفسها لا تَحمل data-svc-spec-modal-open على
    # العنصر الجذري — تَحمله الـpill داخلها. نتأكّد بأنّ النَّسق المُسبَق
    # `<div class="np-svc-card np-svc-card--paid is-unknown"
    #        data-rh-svc-card="public-ip"` لا يَتضمَّن السمة.
    card_open_to_first_gt = html[open_lt:html.find(">", idx)+1]
    assert "data-svc-spec-modal-open" not in card_open_to_first_gt, \
        "the paid card root should NOT itself open the modal"


def test_redesign_always_on_port_script_cards_are_direct_links_no_pills(app, client):
    """يونيو 2026 (طلب المالك): bt_wifi_block وloop_detect دائمتا الإتاحة،
    فالبطاقة نفسها رابط <a> مباشر لصفحة الخدمة (اختيار المداخل هناك) بلا
    شارتَي «تفعيل»/«ترقية». (كانتا تَملكان pill-ين قبل هذا التحويل —
    أُزيلتا لأنّ الخدمتين تَنطبقان دومًا.)"""
    _seed_router(app, nas_id=52, name="ps-rtr", address="203.0.113.52")
    _login(client)
    res = client.get("/admin/radius/mt/52/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    for slug in ("bt_wifi_block", "loop_detect"):
        idx = html.find(f'data-rh-svc-card="{slug}"')
        assert idx >= 0, f"missing {slug} card"
        opening = html[html.rfind("<", 0, idx): html.find(">", idx)]
        end = html.find("</a>", idx)
        assert end >= 0, f"{slug}: card is not an <a> (no closing </a>)"
        card_html = html[idx:end]
        # البطاقة رابط مباشر لصفحة الخدمة المخصّصة.
        assert opening.lstrip().startswith("<a"), f"{slug}: not a direct <a> link"
        assert "port-services" in opening and f"slug={slug}" in opening, \
            f"{slug}: card not linked to its dedicated page"
        # لا شارتا «تفعيل»/«ترقية» بعد الآن (نموذج «دائم الإتاحة»).
        assert "ssm-pill--activate" not in card_html, f"{slug}: activate pill should be gone"
        assert "ssm-pill--upgrade" not in card_html, f"{slug}: upgrade pill should be gone"
        assert 'data-svc-action="activate"' not in card_html
        assert 'data-svc-action="upgrade"' not in card_html


def test_redesign_css_gates_pills_by_state(app, client):
    """الـCSS يُخفي pill «تفعيل» على البطاقة المفعّلة، وpill «ترقية»
    على غير المفعّلة. هذا هو ما يَضمن عَدم التَّناقُض البصري."""
    _seed_router(app, nas_id=53, name="css-rtr", address="203.0.113.53")
    _login(client)
    res = client.get("/admin/radius/mt/53/dashboard")
    html = res.get_data(as_text=True)

    # الإخفاء الافتراضي للـpill-ين، ثم القاعدتان الإستثنائيّتان حسب الحالة.
    assert ".ssm-pill--activate,\n.rh-inventory .ssm-pill--upgrade{display:none}" in html \
        or ".ssm-pill--activate" in html and "display:none" in html
    # «تفعيل» على .is-inactive وعلى .is-unknown.
    assert ".np-svc-card.is-inactive .ssm-pill--activate" in html
    assert ".np-svc-card.is-unknown  .ssm-pill--activate" in html or \
           ".np-svc-card.is-unknown .ssm-pill--activate" in html
    # «ترقية» على .is-active فقط.
    assert ".np-svc-card.is-active .ssm-pill--upgrade" in html


def test_redesign_initial_card_state_classes(app, client):
    """البطاقات تَحمل صنف حالة مبدئيّ في server-render:
       hotspot/broadband/block-sites/open-sites → is-inactive
       bt_wifi_block/loop_detect/public-ip      → is-unknown
    حتى يَعمل الـCSS فور تحميل الصفحة قبل وصول الـprobe."""
    _seed_router(app, nas_id=54, name="state-rtr", address="203.0.113.54")
    _login(client)
    res = client.get("/admin/radius/mt/54/dashboard")
    html = res.get_data(as_text=True)
    # الخدمات المفتوحة + سياسات المواقع تَنطلق inactive.
    for slug in ("hotspot", "broadband", "block-sites", "open-sites"):
        idx = html.find(f'data-rh-svc-card="{slug}"')
        assert idx >= 0
        open_lt = html.rfind("<", 0, idx)
        opener = html[open_lt:idx]
        assert 'is-inactive' in opener, f"{slug} missing initial is-inactive"
    # خدمات سكربت المنافذ + الـpublic-ip تَنطلق unknown.
    for slug in ("bt_wifi_block", "loop_detect", "public-ip"):
        idx = html.find(f'data-rh-svc-card="{slug}"')
        assert idx >= 0
        open_lt = html.rfind("<", 0, idx)
        opener = html[open_lt:idx]
        assert 'is-unknown' in opener, f"{slug} missing initial is-unknown"


def test_redesign_setDot_propagates_to_card_class(app, client):
    """الـJS setDot يَضيف صنف الحالة على البطاقة الأمّ (لا الـdot فقط).
    هذا هو الجسر بين الـprobe والـCSS الذي يُغلِّق الـpills."""
    _seed_router(app, nas_id=55, name="js-rtr", address="203.0.113.55")
    _login(client)
    res = client.get("/admin/radius/mt/55/dashboard")
    html = res.get_data(as_text=True)
    # السطر الذي يَنعَكس فيه الحال على البطاقة الأمّ.
    assert 'card.classList.remove("is-active", "is-inactive", "is-unknown")' in html
    assert 'card.classList.add(state)' in html


def test_redesign_grp_card_uniform_heights(app, client):
    """ارتفاع موحَّد لبطاقات «الخدمات المُضافة»: grid-auto-rows:1fr +
    height:100% + min-height على .rh-grp."""
    _seed_router(app, nas_id=56, name="grp-rtr", address="203.0.113.56")
    _login(client)
    res = client.get("/admin/radius/mt/56/dashboard")
    html = res.get_data(as_text=True)
    # الشبكة تَستخدم grid-auto-rows:1fr لكي تَتطابق صفوف البطاقات.
    assert "grid-auto-rows:1fr" in html
    # .rh-grp بـmin-height محدَّد + height:100% فيَملأ خليّته.
    assert "min-height:200px" in html
    # .rh-grp-body بـflex:1 1 auto + align-content:start.
    assert "align-content:start" in html
