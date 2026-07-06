"""Regression for the live «enabling the bridge toggle reverts» bug.

Owner symptom (يونيو 2026):
  1. License key already saved (from a previous flow).
  2. Operator clicks «تفعيل الجسر مع لوحة التراخيص» → ON.
  3. Submits the form (license-key field left BLANK, since key is stored).
  4. Page returns showing توست «لا توجد تغييرات في بيانات الربط» AND the
     toggle reverts to «معطّل». The bridge never actually enables.

Root behaviour we lock in:
  • A toggle change MUST be persisted independently of whether the
    credential field was edited.
  • When license_key is blank but a key IS already stored, the stored
    key must survive untouched while the toggle (and other non-credential
    settings) get committed.
  • «لا توجد تغييرات» must fire ONLY when NOTHING actually changed in DB
    (including the enabled flag) — not «no credential change».
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_bridge_save_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    # Critical: env override on the enabled flag would short-circuit the
    # DB-derived display. Clear all bridge-related envs so the DB is the
    # only source of truth (matches a real deployment without env tweaks).
    for k in (
        "HOBERADIUS_ADMIN_BRIDGE_ENABLED",
        "HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC",
        "HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED",
        "HOBERADIUS_ADMIN_IDENTITY_SYNC_ON_LOGIN",
        "HOBERADIUS_ADMIN_BRIDGE_WORKER",
        "HOBERADIUS_ADMIN_BASE_URL",
        "HOBERADIUS_LICENSE_KEY",
        "INSTANCE_LICENSE_KEY",
        "HOBERADIUS_ADMIN_SHARED_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)
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
    u = f"bsv_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="bsv-pass", full_name="Bridge Save Tester",
        is_super_admin=True,
    )
    # Saving the bridge config is owner-only (SEC C2); session-super = owner
    # (admins_repo.admin_is_owner), not the bare is_super_admin flag.
    admins_repo.set_designated_owners([u])
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "bsv-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed_existing_link(app, *, key="lic_already_stored",
                        base_url="https://panel.example",
                        enabled="0") -> None:
    """Simulate the operator's pre-state: a license key was saved at some
    earlier point; the bridge is currently disabled."""
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        tenants_repo.set_setting(1, "license_admin_bridge.base_url", base_url, by=0)
        tenants_repo.set_setting(1, "license_admin_bridge.license_key", key, by=0)
        tenants_repo.set_setting(1, "license_admin_bridge.enabled", enabled, by=0)


def _stored(app, key: str) -> str:
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        return tenants_repo.get_setting(1, key, "_MISSING_")


# ═════════════════════════════════════════════════════════════════════
# Reproducer: toggle ON, blank key, key already stored.
# ═════════════════════════════════════════════════════════════════════


def test_enable_toggle_with_blank_key_persists_and_keeps_stored_key(
    app, client,
):
    """The exact owner scenario: key pre-stored, toggle goes ON, key field
    left blank. Must save enabled=1 AND retain the existing key."""
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="0",
    )
    _login(client)
    token = _csrf(client)

    # Pre-conditions.
    assert _stored(app, "license_admin_bridge.enabled") == "0"
    assert _stored(app, "license_admin_bridge.license_key") == "lic_already_stored"

    res = client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",   # readonly value resubmitted
            "license_key": "",                      # ← blank (already saved)
            "enabled": "1",                          # ← toggling ON
            # nothing else
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}, res.get_data(as_text=True)

    # The toggle MUST persist.
    assert _stored(app, "license_admin_bridge.enabled") == "1", (
        "the enabled flag must be saved when the toggle goes ON, even if "
        "the license-key field was left blank because the key is stored"
    )
    # The previously-stored key MUST be preserved.
    assert _stored(app, "license_admin_bridge.license_key") == "lic_already_stored", (
        "blank license-key field on the form must not clobber the stored key"
    )

    # The success flash must fire — NOT «لا توجد تغييرات».
    with client.session_transaction() as sess:
        flashes = list(sess.get("_flashes", []))
    msgs = [m for _, m in flashes]
    assert any("تم حفظ" in m for m in msgs), (
        f"expected success flash; got {msgs!r}"
    )
    assert not any("لا توجد تغييرات" in m for m in msgs), (
        f"the no-changes message must not fire after enabling the toggle; "
        f"got {msgs!r}"
    )


# ═════════════════════════════════════════════════════════════════════
# Symmetric negative test: truly-unchanged save still shows «لا توجد تغييرات».
# ═════════════════════════════════════════════════════════════════════


def test_truly_no_change_still_shows_no_changes_message(app, client):
    """Resubmit the form with the exact stored values — the no-changes
    message must still fire. This pins that the fix doesn't over-rotate
    into "always flash success" behaviour."""
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="1",   # already enabled
    )
    _login(client)
    token = _csrf(client)

    res = client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "",
            "enabled": "1",   # same as stored
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}

    # Still enabled, still has key.
    assert _stored(app, "license_admin_bridge.enabled") == "1"
    assert _stored(app, "license_admin_bridge.license_key") == "lic_already_stored"

    with client.session_transaction() as sess:
        flashes = list(sess.get("_flashes", []))
    msgs = [m for _, m in flashes]
    assert any("لا توجد تغييرات" in m for m in msgs), (
        f"truly-unchanged resubmit must show the no-changes message; "
        f"got {msgs!r}"
    )


# ═════════════════════════════════════════════════════════════════════
# Disabling the bridge (the inverse): toggle OFF must persist too.
# ═════════════════════════════════════════════════════════════════════


def test_disable_toggle_with_blank_key_persists(app, client):
    """Symmetric to the enable case: an already-enabled bridge must be
    disable-able by un-checking the toggle, with a blank key field."""
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="1",
    )
    _login(client)
    token = _csrf(client)

    res = client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "",
            # toggle OFF → field omitted entirely (standard checkbox behaviour)
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert _stored(app, "license_admin_bridge.enabled") == "0", (
        "un-checking the toggle must persist as enabled=0 even with blank key"
    )
    assert _stored(app, "license_admin_bridge.license_key") == "lic_already_stored"


# ═════════════════════════════════════════════════════════════════════
# Env-override diagnostic — owner reported toggle reverts to OFF after
# save because HOBERADIUS_ADMIN_BRIDGE_ENABLED env was forcing it. The
# DB save was correct all along; the env override on display was lying.
# Surface that truthfully via a flash so the operator can fix it.
# ═════════════════════════════════════════════════════════════════════


def test_env_override_warning_fires_when_env_forces_off(app, client, monkeypatch):
    """If the operator submits enabled=ON but the env var forces it
    off, a clear Arabic warning must be flashed so they don't think the
    save silently failed."""
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "0")
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="0",
    )
    _login(client)
    token = _csrf(client)

    res = client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "",
            "enabled": "1",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}

    # The DB still got the new value (the save handler doesn't suppress
    # writes when env overrides — DB is the persistent source of truth
    # for when the env override is later removed).
    assert _stored(app, "license_admin_bridge.enabled") == "1"

    with client.session_transaction() as sess:
        flashes = list(sess.get("_flashes", []))
    msgs = [m for _, m in flashes]
    assert any("HOBERADIUS_ADMIN_BRIDGE_ENABLED" in m and "يَفرض إيقاف الجسر" in m
               for m in msgs), f"env-override diagnostic missing; got {msgs!r}"


def test_no_env_override_warning_when_env_unset(app, client):
    """The warning must NOT fire when the env is unset (the default
    production scenario)."""
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="0",
    )
    _login(client)
    token = _csrf(client)

    client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "",
            "enabled": "1",
        },
        follow_redirects=False,
    )
    with client.session_transaction() as sess:
        flashes = list(sess.get("_flashes", []))
    msgs = [m for _, m in flashes]
    assert not any("HOBERADIUS_ADMIN_BRIDGE_ENABLED" in m for m in msgs), (
        f"unexpected env-override warning: {msgs!r}"
    )


def test_no_env_override_warning_when_env_is_truthy(app, client, monkeypatch):
    """Env explicitly set to ``1``/``true`` is NOT an override conflict —
    no warning should fire."""
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "1")
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="0",
    )
    _login(client)
    token = _csrf(client)

    client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "",
            "enabled": "1",
        },
        follow_redirects=False,
    )
    with client.session_transaction() as sess:
        flashes = list(sess.get("_flashes", []))
    msgs = [m for _, m in flashes]
    assert not any("HOBERADIUS_ADMIN_BRIDGE_ENABLED" in m for m in msgs)


# ═════════════════════════════════════════════════════════════════════
# Worker auto-start — owner's mental model: «one switch» turns on the
# bridge AND the sync worker. enabled=1 must start the worker even when
# worker_enabled isn't explicitly toggled (simple flow doesn't expose it).
# ═════════════════════════════════════════════════════════════════════


def test_worker_starts_when_bridge_enabled_via_simple_flow(app, client, monkeypatch):
    """Enabling the bridge in the simple form must start the sync
    worker even when ``worker_enabled`` is NOT in the submitted form."""
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="0",
    )
    _login(client)
    token = _csrf(client)

    # Patch the worker entry-point to detect that it gets called.
    calls = {"count": 0}

    def _fake_start():
        calls["count"] += 1

    import app.workers.admin_bridge_sync_worker as _wkr
    monkeypatch.setattr(_wkr, "start_admin_bridge_sync_worker", _fake_start)

    client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "",
            "enabled": "1",  # ONLY the main toggle — no worker_enabled
        },
        follow_redirects=False,
    )
    assert calls["count"] == 1, (
        "worker_start must be called when bridge is enabled from the "
        "simple flow, even without an explicit worker_enabled field"
    )


def test_worker_does_not_start_when_bridge_disabled(app, client, monkeypatch):
    """Inverse: disabling the bridge must NOT start the worker."""
    _seed_existing_link(
        app, key="lic_already_stored",
        base_url="https://panel.example", enabled="1",
    )
    _login(client)
    token = _csrf(client)

    calls = {"count": 0}

    def _fake_start():
        calls["count"] += 1

    import app.workers.admin_bridge_sync_worker as _wkr
    monkeypatch.setattr(_wkr, "start_admin_bridge_sync_worker", _fake_start)

    client.post(
        "/admin/radius/license-file/config",
        data={
            "_csrf_token": token,
            "base_url": "https://panel.example",
            "license_key": "",
            # no enabled, no worker_enabled → both off
        },
        follow_redirects=False,
    )
    assert calls["count"] == 0
