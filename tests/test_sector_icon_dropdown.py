"""مصمّم صفحة الدخول: «الرَمز القِطاعيّ» صار قائمة منسدلة بعَيّنات أيقونات.

كان حقلًا نصّيًّا حُرًّا (يَكتب المستخدم coffee/wifi/none/…). صار قائمة
مُخصّصة تُظهر عَيّنة كل أيقونة بِجانب تَسميتها العَربيّة، مع خيار «لا شيء».
القيمة الكَنونيّة المحفوظة لم تَتغيّر (نفس مفتاح MOTIF_ICON يُرسَل ويُطبَّق).

يُغطّي:
  • ثابت MOTIF_ICON_CHOICES كَنونيّ (كل مفتاح صالح، wifi الافتراضي + none).
  • الصفحة تُصيّر القائمة (لا حقل نصّي) بكل الخيارات: أيقونة + تَسمية + مفتاح.
  • خيار «none» موجود؛ الافتراضي مُحدَّد.
  • الحفظ يُبقي نفس المفتاح (coffee، none) — اختيار يُكتب في نفس الحقل المخفي.

شغّل الملف وحده (عزل لكل ملف)."""
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
    tmp = tempfile.mkdtemp(prefix="hr_sector_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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
    u = f"sector_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="sector-pass", full_name="Sector Tester",
        is_super_admin=True,
    )
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "sector-pass"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, 'sector-rtr', '203.0.113.40', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _page(client) -> str:
    res = client.get("/admin/radius/mt/1/login-designer")
    assert res.status_code == 200
    return res.get_data(as_text=True)


# ─── (1) الثابت الكَنونيّ ────────────────────────────────────────


def test_choices_constant_is_canonical():
    from app.radius.services import hotspot_templates as ht
    choices = ht.MOTIF_ICON_CHOICES
    assert len(choices) >= 13
    keys = [c[0] for c in choices]
    # لا تكرار.
    assert len(keys) == len(set(keys))
    # كل مفتاح يَمرّ بفحص الحفظ (_MOTIF_KEY_RE) — وإلّا يُرفَض الحفظ.
    for key, label, glyph in choices:
        assert ht._MOTIF_KEY_RE.match(key), key
        assert label.strip()
        assert glyph.strip()
    # «none» للإيقاف + الافتراضي wifi ضِمن الخيارات.
    assert "none" in keys
    assert "wifi" in keys
    assert ht.VARIABLES_BY_SLUG["MOTIF_ICON"].default in keys


# ─── (2) تصيير القائمة بدل الحقل النصّي ──────────────────────────


def test_page_renders_custom_dropdown_not_freetext(app, client):
    _seed(app)
    _login(client)
    html = _page(client)
    # حاوية القائمة المُخصّصة + الحقل المخفي (مصدر القيمة للمعاينة/الحفظ).
    assert "data-mtld-sector" in html
    assert "data-mtld-sector-value" in html
    assert 'data-mt-designer-var="MOTIF_ICON"' in html
    # لا حقل نصّي حُرّ لـ MOTIF_ICON بعد اليوم.
    assert not re.search(
        r'<input[^>]*type="text"[^>]*data-mt-designer-var="MOTIF_ICON"', html)
    assert not re.search(
        r'<input[^>]*data-mt-designer-var="MOTIF_ICON"[^>]*type="text"', html)


def test_page_renders_all_canonical_options_with_icons_and_labels(app, client):
    from app.radius.services import hotspot_templates as ht
    _seed(app)
    _login(client)
    html = _page(client)
    # عدد خيارات القائمة == عدد الخيارات الكَنونيّة. (نَعدّ role="option"
    # في الـmarkup — لا data-mtld-sector-opt الذي يَرد أيضًا كنصّ مُحدِّد
    # في الـ<script>.)
    assert html.count('role="option"') == len(ht.MOTIF_ICON_CHOICES)
    # كل خيار: المفتاح + أيقونة العَيّنة + التَسمية العَربيّة.
    for key, label, glyph in ht.MOTIF_ICON_CHOICES:
        assert f'data-value="{key}"' in html, key
        assert f"fa-{glyph}" in html, glyph
        assert label in html, label


def test_none_option_present(app, client):
    _seed(app)
    _login(client)
    html = _page(client)
    assert 'data-value="none"' in html
    assert "mtld-sector-opt is-none" in html or "is-none" in html


def test_default_value_is_selected(app, client):
    from app.radius.services import hotspot_templates as ht
    _seed(app)
    _login(client)
    html = _page(client)
    default = ht.VARIABLES_BY_SLUG["MOTIF_ICON"].default  # wifi
    # الحقل المخفي يَحمل القيمة الافتراضيّة.
    assert re.search(
        r'data-mtld-sector-value[^>]*value="%s"' % re.escape(default), html) \
        or re.search(r'value="%s"[^>]*data-mtld-sector-value' % re.escape(default), html)
    # خيار القِطاع الافتراضيّ (wifi) مُحدَّد.
    assert re.search(r'data-value="%s"[^>]*aria-selected="true"' % re.escape(default), html)
    # وخيار واحد بالضبط مُحدَّد داخل قائمة القِطاع (نَعزل منطقة القائمة كي
    # لا نَخلط مع aria-selected في تبويبات التخصيص أعلى الصفحة).
    menu = html.split("data-mtld-sector-menu", 1)[1].split("</ul>", 1)[0]
    assert menu.count('aria-selected="true"') == 1


# ─── (3) الحفظ يُبقي المفتاح الكَنونيّ ───────────────────────────


def _save(client, motif_value: str):
    token = _csrf(client)
    return client.post("/admin/radius/mt/1/login-designer/save", data={
        "_csrf_token": token,
        "template_slug": "classic",
        "TENANT_NAME": "شبكة",
        "ACCENT_COLOR": "#16A34A",
        "BG_COLOR": "#F8FAFC",
        "MOTIF_ICON": motif_value,
    })


def test_save_persists_selected_sector_key(app, client):
    _seed(app)
    _login(client)
    res = _save(client, "coffee")
    assert res.status_code == 200
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        row = r.get_design(1, 1)
        assert row["variables"]["MOTIF_ICON"] == "coffee"


def test_save_none_disables_sector(app, client):
    _seed(app)
    _login(client)
    res = _save(client, "none")
    assert res.status_code == 200
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        row = r.get_design(1, 1)
        assert row["variables"]["MOTIF_ICON"] == "none"
