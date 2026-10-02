"""BUG3 regression — operator permission toggles must show ARABIC labels.

The business-operator profile page (/admin/radius/business-operators/<type>/<id>)
rendered each permission toggle with its raw key (can_create_subscriber …) as
the visible on/off label. This proves the central `permission_label` map turns
every key into Arabic, and that no raw `can_*` key is used as a visible label
on the rendered page (the form-wiring `name="can_*"` attribute is unaffected).
"""
from __future__ import annotations

from uuid import uuid4

import pytest


_OWNER_USER = "perm_owner"
_OWNER_PASS = "perm-web-pass"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "dev-token-please-change")
    # d13fb302: create_app() guarantees a bootstrap super-admin on a clean DB
    # (admin id #1 = the primary owner, the only RBAC-bypass principal).
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", _OWNER_USER)
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", _OWNER_PASS)
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _tenant(app):
    from app.radius.db.connection import transaction

    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tenants(id, name, slug, created_at) "
            "VALUES (1, 'Default Tenant', 'default', '2026-01-01T00:00:00Z')"
        )


def _web_login(client) -> None:
    # The owner-only-bypass decision: a NEW admin carrying is_super_admin is
    # not the bypass principal (it would 403 on the operator profile). Log in
    # as the real primary owner = the bootstrap admin (d13fb302).
    from app.radius.db.repos import admins_repo

    owner = admins_repo.get_by_username(_OWNER_USER)
    assert owner is not None and admins_repo.is_primary_owner(owner.id)
    res = client.post(
        "/admin/radius/login",
        data={"username": _OWNER_USER, "password": _OWNER_PASS},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


# ───────────────────────── pure label map ─────────────────────────

def test_label_map_covers_all_default_permissions():
    from app.radius.services.manager_distributor_ops import DEFAULT_PERMISSIONS
    from app.radius.services.permission_labels import permission_label

    for key in DEFAULT_PERMISSIONS:
        label = permission_label(key)
        assert label and "can_" not in label and "_" not in label, key
        # Arabic — contains at least one Arabic letter.
        assert any("؀" <= ch <= "ۿ" for ch in label), (key, label)


def test_known_keys_exact_arabic():
    from app.radius.services.permission_labels import permission_label

    assert permission_label("can_create_subscriber") == "إنشاء مشترك"
    assert permission_label("can_create_batch") == "إنشاء دفعة بطاقات"
    assert permission_label("can_give_free_days") == "منح أيام مجانية"
    assert permission_label("can_activate_subscriber") == "تفعيل مشترك"
    assert permission_label("can_give_loan") == "منح سلفة"
    assert permission_label("can_give_trial_days") == "منح أيام تجريبية"


def test_unknown_key_never_leaks_raw():
    from app.radius.services.permission_labels import permission_label

    # composer path (verb+noun)
    assert permission_label("can_create_subscriber") == "إنشاء مشترك"
    # totally unknown → humanized, but never a raw can_* token
    out = permission_label("can_frobnicate_widget")
    assert "can_" not in out and out.strip()


# ───────────────────────── rendered page ─────────────────────────

def test_operator_profile_page_shows_arabic_labels(client):
    from app.radius.db.repos import admins_repo

    mgr = admins_repo.create_admin(
        username=f"perm_mgr_{uuid4().hex[:8]}", password="perm-mgr-pass",
        full_name="Managed Manager")
    _web_login(client)
    res = client.get(f"/admin/radius/business-operators/manager/{mgr.id}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Arabic labels are present on the page.
    assert "إنشاء مشترك" in html
    assert "منح سلفة" in html
    assert "منح أيام مجانية" in html

    # No raw can_* key is used as a VISIBLE toggle label (data-on/data-off).
    assert 'data-on="can_' not in html
    assert 'data-off="can_' not in html
    # …while the form wiring is intact. ce16093d replaced the per-flag
    # checkboxes (name="can_*") with the tri-state «حسب الدور/مسموح/ممنوع»
    # rows keyed by ACTION (manager_grants.tri_input_name); the
    # can_create_subscriber flag is the `subscriber.create` action.
    assert 'name="tri_subscriber.create"' in html
    assert 'name="can_create_subscriber"' not in html
