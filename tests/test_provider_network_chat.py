"""MT32 — شات المزوّد ↔ الشبكة: وظيفةٌ وعزل.

يُثبت أن:
  * الرسائل تُرسَل في الاتجاهين وتُقرأ في الخيط الصحيح.
  * خيط كل شبكة مستقلّ: لا تسريب محتوى ولا عدّادات بين الشبكتين.
  * مالك الشبكة (أ) لا يبلغ خيط (ب) لا بالمسار ولا بالمعرّف.
  * جانب المزوّد (كل المراسلات) ممنوع على مدراء الشبكات.
  * الشات لا يدخل في النسخة الاحتياطية للشبكة (ولا تمحوه الاستعادة).
"""
from __future__ import annotations

import os
import re

import pytest

PW = "netpass12345"


@pytest.fixture
def app_two(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "chat.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_OPEN_HOSTING", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.core.tenant import Tenant
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        from app.radius.middleware.tenant_path import invalidate_slug_cache
        from app.radius.services.tenants import get_tenants_service
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        svc = get_tenants_service()
        for slug in ("neta", "netb"):
            svc.create_trial(
                actor="provider",
                tenant=Tenant(id=None, slug=slug, name=slug.upper(),
                              display_name=slug.upper(), status="trial"),
                trial_days=30, operator_username=f"{slug}-own", operator_password=PW)
        invalidate_slug_cache()
        app.tids = {t.slug: t.id for t in tenants_repo.list_tenants()}  # type: ignore[attr-defined]
    return app


def _login(app, path_prefix, user, pw):
    c = app.test_client()
    html = c.get(f"{path_prefix}/admin/radius/login").get_data(as_text=True)
    tok = re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)
    c.post(f"{path_prefix}/admin/radius/login",
           data={"username": user, "password": pw, "_csrf_token": tok})
    return c


def _send(c, url, body):
    with c.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    return c.post(url, data={"body": body, "_csrf_token": tok}, follow_redirects=True)


def _owner(app):
    """المالك الرئيسي (bootstrap admin) — لوحة المزوّد."""
    from app.radius.db.connection import db
    with app.app_context():
        row = db().execute("SELECT username FROM admins ORDER BY id ASC LIMIT 1").fetchone()
        db().execute("UPDATE admins SET password_hash=? WHERE username=?",
                     (_hash("ownerpass123"), row["username"]))
        db().commit()
    return _login(app, "", row["username"], "ownerpass123")


def _hash(pw: str) -> str:
    from app.radius.db.repos.admins_repo import hash_password
    return hash_password(pw)


# ─────────────── وظيفة الشات ───────────────

def test_two_way_messaging(app_two):
    app = app_two
    a = app.tids["neta"]
    prov = _owner(app)
    net = _login(app, "/neta", "neta-own", PW)

    _send(prov, f"/admin/radius/provider/chat/{a}/send", "أهلًا، اشتراكك المجاني فُعّل.")
    _send(net, "/neta/admin/radius/support/send", "شكرًا، أحتاج رفع حدّ المشتركين.")

    body = net.get("/neta/admin/radius/support").get_data(as_text=True)
    assert "اشتراكك المجاني فُعّل" in body
    assert "أحتاج رفع حدّ المشتركين" in body
    body = prov.get(f"/admin/radius/provider/chat/{a}").get_data(as_text=True)
    assert "أحتاج رفع حدّ المشتركين" in body


def test_polling_returns_only_new(app_two):
    app = app_two
    a = app.tids["neta"]
    prov = _owner(app)
    _send(prov, f"/admin/radius/provider/chat/{a}/send", "رسالة ١")
    _send(prov, f"/admin/radius/provider/chat/{a}/send", "رسالة ٢")
    net = _login(app, "/neta", "neta-own", PW)
    data = net.get("/neta/admin/radius/support/messages?after_id=0").get_json()
    assert [m["body"] for m in data["messages"]] == ["رسالة ١", "رسالة ٢"]
    first = data["messages"][0]["id"]
    data2 = net.get(f"/neta/admin/radius/support/messages?after_id={first}").get_json()
    assert [m["body"] for m in data2["messages"]] == ["رسالة ٢"]


def test_unread_counters_per_side(app_two):
    app = app_two
    a = app.tids["neta"]
    with app.app_context():
        from app.radius.services import provider_chat
        provider_chat.post_message(tenant_id=a, sender="provider", body="من المزوّد")
        # المُرسِل قرأ رسالته؛ الطرف الآخر لا
        assert provider_chat.unread_count(tenant_id=a, side="provider") == 0
        assert provider_chat.unread_count(tenant_id=a, side="network") == 1
        provider_chat.mark_read(tenant_id=a, side="network")
        assert provider_chat.unread_count(tenant_id=a, side="network") == 0


# ─────────────── العزل ───────────────

def test_threads_are_isolated_between_networks(app_two):
    app = app_two
    a, b = app.tids["neta"], app.tids["netb"]
    prov = _owner(app)
    _send(prov, f"/admin/radius/provider/chat/{a}/send", "سرٌّ للشبكة أ")
    _send(prov, f"/admin/radius/provider/chat/{b}/send", "سرٌّ للشبكة ب")

    na = _login(app, "/neta", "neta-own", PW)
    nb = _login(app, "/netb", "netb-own", PW)
    ba = na.get("/neta/admin/radius/support").get_data(as_text=True)
    bb = nb.get("/netb/admin/radius/support").get_data(as_text=True)
    assert "سرٌّ للشبكة أ" in ba and "سرٌّ للشبكة ب" not in ba
    assert "سرٌّ للشبكة ب" in bb and "سرٌّ للشبكة أ" not in bb
    # ولا عبر الـpolling
    ja = na.get("/neta/admin/radius/support/messages?after_id=0").get_json()
    assert [m["body"] for m in ja["messages"]] == ["سرٌّ للشبكة أ"]


def test_network_cannot_reach_other_network_thread(app_two):
    """لا بالمسار (شبكة أخرى) ولا بجانب المزوّد (كل المراسلات)."""
    app = app_two
    b = app.tids["netb"]
    na = _login(app, "/neta", "neta-own", PW)
    assert na.get("/netb/admin/radius/support").status_code == 403
    assert na.get("/netb/admin/radius/support/messages").status_code == 403
    # جانب المزوّد ممنوع على مالك الشبكة حتى تحت مسار شبكته
    for p in (f"/neta/admin/radius/provider/chat",
              f"/neta/admin/radius/provider/chat/{b}",
              f"/neta/admin/radius/provider/chat/{b}/messages"):
        assert na.get(p).status_code == 403, f"{p}: مالك شبكة بلغ مراسلات المزوّد!"


def test_network_cannot_post_into_another_thread(app_two):
    """لا مسار يقبل tenant_id من الطلب في جانب الشبكة — الجهة من g وحدها."""
    app = app_two
    b = app.tids["netb"]
    na = _login(app, "/neta", "neta-own", PW)
    # محاولة حقن الجهة عبر النموذج والاستعلام معًا
    with na.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    na.post("/neta/admin/radius/support/send",
            data={"body": "اختراق", "tenant_id": str(b), "_csrf_token": tok},
            query_string={"tenant_id": str(b)}, follow_redirects=True)
    with app.app_context():
        from app.radius.services import provider_chat
        assert not [m for m in provider_chat.list_messages(tenant_id=b)
                    if m["body"] == "اختراق"], "رسالة حطّت في خيط شبكة أخرى!"
        assert [m for m in provider_chat.list_messages(tenant_id=app.tids["neta"])
                if m["body"] == "اختراق"], "الرسالة لم تُسجَّل في خيط صاحبها"


def test_chat_excluded_from_tenant_backup(app_two):
    """مراسلات المزوّد ليست بيانات تشغيل: لا تُنسخ ولا تُمحى بالاستعادة."""
    app = app_two
    a = app.tids["neta"]
    with app.app_context():
        import gzip
        import json
        from app.radius.services import provider_chat, tenant_backup
        provider_chat.post_message(tenant_id=a, sender="provider", body="مراسلة قديمة")
        assert "provider_chat_messages" not in tenant_backup.tenant_tables()
        tenant_backup.export_tenant(a, actor="t")
        name = tenant_backup.list_tenant_backups(a)[0]["name"]
        blob = gzip.decompress(tenant_backup.read_backup_bytes(a, name)).decode("utf-8")
        assert "مراسلة قديمة" not in blob
        # رسالة جديدة بعد النسخة تنجو من الاستعادة
        provider_chat.post_message(tenant_id=a, sender="network", body="مراسلة جديدة")
        tenant_backup.restore_tenant(a, name, actor="t")
        bodies = [m["body"] for m in provider_chat.list_messages(tenant_id=a)]
        assert "مراسلة جديدة" in bodies and "مراسلة قديمة" in bodies


def test_empty_message_rejected(app_two):
    app = app_two
    with app.app_context():
        from app.radius.services import provider_chat
        with pytest.raises(provider_chat.ProviderChatError):
            provider_chat.post_message(tenant_id=app.tids["neta"], sender="network", body="   ")
        with pytest.raises(provider_chat.ProviderChatError):
            provider_chat.post_message(tenant_id=app.tids["neta"], sender="hacker", body="x")
