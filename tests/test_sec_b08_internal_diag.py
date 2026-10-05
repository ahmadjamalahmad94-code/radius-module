"""B-08 (docs/security/BYPASS_PATHS.md): ``POST /api/v1/internal/_diag``.

With ``HOBERADIUS_DIAG_ENABLED=1`` the endpoint took ``username``,
``password`` and ``tenant_id`` from the JSON body and — with NO
authentication — returned whether the user exists in that tenant
(``found_in``) and whether the password is accepted (``decision.ok``): a
cross-tenant password/existence oracle for anyone who reaches the port.

Fix: when enabled it now also requires the internal secret
(``X-Internal-Secret`` header or ``_internal_secret`` body field, the same
secret FreeRADIUS uses), and a secret must actually be configured — no dev
"accept without secret" fallback for this endpoint. Disabled (the default) is
unchanged: 403.
"""
from __future__ import annotations

import pytest

from sec_tenant_helpers import app, client, tenants  # noqa: F401

SECRET = "test-internal-secret-xyz"
URL = "/api/v1/internal/_diag"


def _sub(app, tid, username, password):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    with app.app_context():
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username=username, password=password, tenant_id=tid,
            status="enabled", full_name=username))


@pytest.fixture
def two_tenants(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_DIAG_ENABLED", "1")
    monkeypatch.setenv("HOBERADIUS_INTERNAL_SECRET", SECRET)
    _a, b = tenants(app)
    _sub(app, 1, "ahmad", "pass-in-A")
    _sub(app, b.id, "ahmad", "pass-in-B")
    return b


def _body(tid, pw):
    return {"username": "ahmad", "password": pw, "tenant_id": tid}


# ───────────────────────── reproducers ─────────────────────────

def test_diag_without_secret_is_refused(app, client, two_tenants):
    b = two_tenants
    for tid, pw in ((1, "pass-in-A"), (b.id, "pass-in-B"), (b.id, "guess")):
        r = client.post(URL, json=_body(tid, pw))
        assert r.status_code in (401, 403), (tid, r.status_code, r.get_json())
        data = r.get_json() or {}
        assert "decision" not in data and "found_in" not in data


def test_diag_with_wrong_secret_is_refused(app, client, two_tenants):
    b = two_tenants
    r = client.post(URL, json=_body(b.id, "pass-in-B"),
                    headers={"X-Internal-Secret": "wrong"})
    assert r.status_code in (401, 403)
    r = client.post(URL, json={**_body(b.id, "pass-in-B"), "_internal_secret": "wrong"})
    assert r.status_code in (401, 403)


def test_diag_requires_a_configured_secret_even_outside_production(
        app, client, monkeypatch):
    """The generic internal check accepts everything in dev when no secret is
    set; the diagnostic oracle must not."""
    monkeypatch.setenv("HOBERADIUS_DIAG_ENABLED", "1")
    monkeypatch.delenv("HOBERADIUS_INTERNAL_SECRET", raising=False)
    tenants(app)
    _sub(app, 1, "ahmad", "pass-in-A")
    r = client.post(URL, json=_body(1, "pass-in-A"))
    assert r.status_code in (401, 403), r.get_json()


# ─────────── with the secret: works, and stays per tenant ───────────

def test_diag_with_secret_answers_per_tenant(app, client, two_tenants):
    b = two_tenants
    h = {"X-Internal-Secret": SECRET}
    ra = client.post(URL, json=_body(1, "pass-in-A"), headers=h).get_json()
    rb = client.post(URL, json=_body(b.id, "pass-in-B"), headers=h).get_json()
    assert ra["found_in"] == "subscribers" and ra["tenant_id"] == 1
    assert rb["found_in"] == "subscribers" and rb["tenant_id"] == b.id
    # A's password does not authenticate the same username in B and vice versa
    x = client.post(URL, json=_body(b.id, "pass-in-A"), headers=h).get_json()
    y = client.post(URL, json=_body(1, "pass-in-B"), headers=h).get_json()
    assert x["decision"]["ok"] is False and y["decision"]["ok"] is False
    # body-field secret (FreeRADIUS 3.2 style) is accepted too
    r = client.post(URL, json={**_body(1, "pass-in-A"), "_internal_secret": SECRET})
    assert r.status_code == 200


def test_diag_disabled_by_default(app, client, monkeypatch):
    monkeypatch.delenv("HOBERADIUS_DIAG_ENABLED", raising=False)
    monkeypatch.setenv("HOBERADIUS_INTERNAL_SECRET", SECRET)
    r = client.post(URL, json=_body(1, "x"), headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 403
