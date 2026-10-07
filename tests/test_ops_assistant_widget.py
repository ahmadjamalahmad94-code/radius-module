"""Operations assistant — the floating chat bubble on every panel page.

* the bubble renders only when the assistant is usable for this admin (tenant
  flag ON + password gate open — the same gate as the page and its sidebar
  entry), never on the full page itself, and the chat code is NOT inlined
  (lazy: css/js/panel are fetched on the first click);
* ``/ops-assistant/widget`` (the panel fragment) and ``/ops-assistant/history``
  (text-only resume) are behind the same gate; history is the admin's OWN
  conversation only and never replays cards / tool / system rows.
"""
from __future__ import annotations

import json

import pytest

from ops_exec_helpers import (  # noqa: F401
    OWNER_PASS, OWNER_USER, PW, app, client, enable, manager, owner, q,
)

PAGE = "/admin/radius/ops-assistant"
OTHER = "/admin/radius/users"


@pytest.fixture(autouse=True)
def _state(app):
    from app.radius.routes.ops_assistant import reset_nav_cache
    from app.radius.services.ops_assistant import gate
    enable(app, 1, True)
    gate.reset_cache()
    reset_nav_cache()
    yield
    reset_nav_cache()


def login(client, user=OWNER_USER, pw=OWNER_PASS):
    res = client.post("/admin/radius/login", data={"username": user, "password": pw})
    assert res.status_code in (302, 303), res.status_code


def html(client, path=OTHER):
    res = client.get(path)
    assert res.status_code == 200, (path, res.status_code)
    return res.get_data(as_text=True)


def test_bubble_on_panel_pages_when_allowed_and_lazy(client, app):
    login(client)
    page = html(client)
    assert 'id="ops-fab"' in page
    assert 'data-panel="/admin/radius/ops-assistant/widget"' in page
    # lazy: no chat config / chat script / chat css inlined on ordinary pages
    assert 'id="ops-config"' not in page and 'data-ops="config"' not in page
    assert '<script src="/static/js/ops_assistant.js' not in page
    assert 'href="/static/css/ops_assistant.css' not in page
    assert 'data-js="/static/js/ops_assistant.js' in page


def test_no_bubble_on_the_full_page(client, app):
    login(client)
    page = html(client, PAGE)
    assert 'id="ops-config"' in page and 'id="ops-fab"' not in page


def test_no_bubble_when_flag_off(client, app):
    enable(app, 1, False)
    login(client)
    assert 'id="ops-fab"' not in html(client)
    r = client.get(PAGE + "/widget")
    assert r.status_code == 403 and r.get_json()["reason"] == "disabled"
    r = client.get(PAGE + "/history?conversation_id=x")
    assert r.status_code == 403


def test_no_bubble_when_password_gate_closed(client, app):
    adm = manager(app, ["users.view"])
    q(app, "UPDATE admins SET must_change_password=1 WHERE id=?", (adm.id,))
    try:
        login(client)
        assert 'id="ops-fab"' not in html(client)
        r = client.get(PAGE + "/widget")
        assert r.status_code == 403 and r.get_json()["reason"] == "weak_admin_passwords"
    finally:
        q(app, "UPDATE admins SET must_change_password=0, enabled=0 WHERE id=?", (adm.id,))


def test_no_bubble_and_no_panel_when_logged_out(client, app):
    r = client.get(PAGE + "/widget")
    assert r.status_code in (302, 303) and "/login" in r.headers["Location"]
    r = client.get(PAGE + "/history?conversation_id=x")
    assert r.status_code in (302, 303)


def test_manager_sees_bubble_with_own_key(client, app):
    adm = manager(app, ["users.view"])
    try:
        login(client, adm.username, PW)
        page = html(client)
        assert 'id="ops-fab"' in page and f'data-key="t1a{adm.id}"' in page
    finally:
        q(app, "UPDATE admins SET enabled=0 WHERE id=?", (adm.id,))


def test_widget_fragment_reuses_the_page_chat(client, app):
    login(client)
    r = client.get(PAGE + "/widget")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    frag = r.get_data(as_text=True)
    assert "<html" not in frag and "ops-fab-panel" in frag
    assert 'data-ops="log"' in frag and 'data-ops="form"' in frag and 'data-ops="input"' in frag
    assert 'id="ops-w-secret-modal"' in frag and 'data-ops="secret-list"' in frag
    assert 'href="/admin/radius/ops-assistant"' in frag            # «فتح بصفحة كاملة»
    assert 'data-ops-w="min"' in frag and 'data-ops-w="close"' in frag
    cfg_raw = frag.split('<script type="application/json" data-ops="config">', 1)[1]
    cfg = json.loads(cfg_raw.split("</script>", 1)[0])
    assert cfg["urls"]["message"] == PAGE + "/message"
    assert cfg["urls"]["pick"] == PAGE + "/pick" and cfg["urls"]["history"] == PAGE + "/history"
    assert cfg["t"]["suggest_title"] and cfg["t"]["resumed"]


def _conv(app, admin_id, rows):
    from app.radius.services.ops_assistant import conversation, store
    with app.app_context():
        cid = store.new_conversation(1, admin_id, None)
        for role, content in rows:
            conversation.append(cid, 1, role, content)
    return cid


def test_history_is_text_only_and_own_conversation(client, app):
    from app.radius.services.ops_assistant import model_client
    me = owner(app)
    cid = _conv(app, me.id, [
        ("system", "CONTEXT {}"),
        ("user", "جدّد لأحمد شهر"),
        ("assistant", json.dumps({"action": "choose", "fields": {}, "missing": [],
                                  "message": "أبحث عن أحمد."}, ensure_ascii=False)),
        ("tool", "CHOICES {\"items\": []}"),
        ("assistant", json.dumps({"action": "ask", "fields": {}, "missing": []})),
        ("user", model_client.CANCEL_TEXT),
    ])
    login(client)
    r = client.get(PAGE + "/history?conversation_id=" + cid)
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True, body
    assert r.headers["Cache-Control"] == "no-store"
    assert body["items"] == [{"role": "user", "text": "جدّد لأحمد شهر"},
                             {"role": "bot", "text": "أبحث عن أحمد."},
                             {"role": "user", "text": "إلغاء"}]
    assert "CHOICES" not in r.get_data(as_text=True) and "CONTEXT" not in r.get_data(as_text=True)

    # another admin's conversation id is simply not found
    adm = manager(app, ["users.view"])
    try:
        other = _conv(app, adm.id, [("user", "سرّي")])
        r = client.get(PAGE + "/history?conversation_id=" + other)
        assert r.status_code == 404 and r.get_json()["code"] == "not_found"
        r = client.get(PAGE + "/history")
        assert r.status_code == 404
    finally:
        q(app, "UPDATE admins SET enabled=0 WHERE id=?", (adm.id,))
