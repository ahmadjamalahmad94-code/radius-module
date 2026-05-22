"""Q4 — Unprogram / rollback for hoberadius-comment objects.

Service-layer + route-layer coverage with a fake wire client that
records every call. The fake client returns synthetic /print rows
so the unprogram walker has something to chew on.
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
    tmp = tempfile.mkdtemp(prefix="hr_q4_")
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
    u = f"q4_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="q4-pass", full_name="Q4 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "q4-pass"},
        follow_redirects=False,
    )
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
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, 'q4-rtr', '203.0.113.17', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


class _FakeRouter:
    """Stub MikrotikClient that returns canned /print results and
    records every call. `print_rows[path]` defines the rows the
    router has for that resource."""

    def __init__(self, print_rows: dict[str, list[dict]],
                 *, raise_on_print: set[str] | None = None,
                 raise_on_remove: set[str] | None = None):
        self.print_rows = print_rows
        self.raise_on_print = raise_on_print or set()
        self.raise_on_remove = raise_on_remove or set()
        self.calls: list[tuple[str, dict]] = []

    def connect(self): pass
    def close(self): pass

    def run(self, path, attrs=None):
        self.calls.append((path, dict(attrs or {})))
        if path.endswith("/print"):
            base = path[: -len("/print")]
            if base in self.raise_on_print:
                raise RuntimeError(f"print failed on {base}")
            return list(self.print_rows.get(base, []))
        if path.endswith("/remove"):
            base = path[: -len("/remove")]
            if base in self.raise_on_remove:
                raise RuntimeError(f"remove rejected on {base}")
            return []
        return []


# ─── Service-layer ────────────────────────────────────────────


def test_unprogram_removes_only_hoberadius_hotspot_rows():
    from app.radius.services.mt_programming import (
        unprogram, HOTSPOT_COMMENT,
    )
    rows = {
        "/ip/pool": [
            {".id": "*1", "comment": HOTSPOT_COMMENT, "name": "hs-pool"},
            {".id": "*2", "comment": "manually-added", "name": "other"},
        ],
        "/ip/address": [
            {".id": "*3", "comment": HOTSPOT_COMMENT,
             "address": "192.168.10.1/24"},
            {".id": "*4", "comment": "", "address": "10.0.0.1/24"},
        ],
        "/ip/hotspot": [
            {".id": "*5", "comment": HOTSPOT_COMMENT, "name": "hs"},
        ],
    }
    client = _FakeRouter(rows)
    res = unprogram(client, "hotspot")
    assert res.ok is True
    removed_ids = {s.id for s in res.steps if s.ok}
    assert removed_ids == {"*1", "*3", "*5"}
    # The non-hoberadius rows must NOT be removed — that's the
    # whole safety story of this feature.
    assert "*2" not in removed_ids
    assert "*4" not in removed_ids


def test_unprogram_pppoe_isolates_its_comment():
    """If both kinds of objects exist on the router, unprogram
    with kind=pppoe must remove only the PPPOE_COMMENT rows.
    Cross-deleting would break a working hotspot."""
    from app.radius.services.mt_programming import (
        unprogram, HOTSPOT_COMMENT, PPPOE_COMMENT,
    )
    rows = {
        "/ip/pool": [
            {".id": "*1", "comment": HOTSPOT_COMMENT, "name": "hs-pool"},
            {".id": "*2", "comment": PPPOE_COMMENT, "name": "ppp-pool"},
        ],
        "/ppp/profile": [
            {".id": "*3", "comment": PPPOE_COMMENT, "name": "ppp-prof"},
        ],
        "/interface/pppoe-server/server": [
            {".id": "*4", "comment": PPPOE_COMMENT, "service-name": "s"},
        ],
    }
    client = _FakeRouter(rows)
    res = unprogram(client, "pppoe")
    assert res.ok is True
    removed = {s.id for s in res.steps if s.ok}
    assert removed == {"*2", "*3", "*4"}


def test_unprogram_dependency_order_is_correct():
    """RouterOS rejects /ip/address removal while /ip/dhcp-server
    is still bound to the interface, etc. Ensure unprogram walks
    leaf → root: hotspot/walled-garden ... → /ip/address →
    /ip/pool comes last."""
    from app.radius.services.mt_programming import unprogram
    HOTSPOT_COMMENT = "hoberadius:hs"
    rows = {
        "/ip/hotspot/walled-garden/ip": [
            {".id": "*A", "comment": HOTSPOT_COMMENT}],
        "/ip/hotspot": [
            {".id": "*B", "comment": HOTSPOT_COMMENT}],
        "/ip/hotspot/profile": [
            {".id": "*C", "comment": HOTSPOT_COMMENT}],
        "/ip/dhcp-server": [
            {".id": "*D", "comment": HOTSPOT_COMMENT}],
        "/ip/dhcp-server/network": [
            {".id": "*E", "comment": HOTSPOT_COMMENT}],
        "/ip/address": [
            {".id": "*F", "comment": HOTSPOT_COMMENT}],
        "/ip/pool": [
            {".id": "*G", "comment": HOTSPOT_COMMENT}],
    }
    client = _FakeRouter(rows)
    res = unprogram(client, "hotspot")
    assert res.ok
    # Each step's path appears in the steps list in order; pool
    # must come AFTER address must come AFTER dhcp-server.
    order = [s.path for s in res.steps]
    assert order.index("/ip/address") > order.index("/ip/dhcp-server")
    assert order.index("/ip/pool") > order.index("/ip/address")
    assert order.index("/ip/hotspot") > order.index("/ip/hotspot/walled-garden/ip")


def test_unprogram_skips_resources_router_does_not_have():
    """A CHR build without the hotspot package will reject
    `/ip/hotspot/print`. That MUST be non-fatal — the rest of the
    cleanup should still run."""
    from app.radius.services.mt_programming import (
        unprogram, HOTSPOT_COMMENT,
    )
    rows = {
        "/ip/pool": [
            {".id": "*1", "comment": HOTSPOT_COMMENT, "name": "p"}],
    }
    client = _FakeRouter(rows, raise_on_print={"/ip/hotspot"})
    res = unprogram(client, "hotspot")
    assert res.ok is True
    assert "/ip/hotspot" in res.skipped_paths
    assert any(s.id == "*1" for s in res.steps)


def test_unprogram_returns_failed_when_remove_rejected():
    from app.radius.services.mt_programming import (
        unprogram, HOTSPOT_COMMENT,
    )
    rows = {
        "/ip/pool": [
            {".id": "*1", "comment": HOTSPOT_COMMENT, "name": "p"}],
    }
    client = _FakeRouter(rows, raise_on_remove={"/ip/pool"})
    res = unprogram(client, "hotspot")
    assert res.ok is False
    assert res.summary()["failed"] == 1


def test_unprogram_rejects_unknown_kind():
    from app.radius.services.mt_programming import unprogram
    client = _FakeRouter({})
    res = unprogram(client, "wlan-mesh-x")
    assert res.ok is False
    assert "unknown kind" in res.error


# ─── Route layer ───────────────────────────────────────────────


def _post_unprogram(client, *, nas_id, kind="hotspot",
                    confirm=True):
    token = _csrf(client)
    data = {"_csrf_token": token, "kind": kind}
    if confirm:
        data["confirm"] = "1"
    return client.post(
        f"/admin/radius/mt/{nas_id}/program/unprogram",
        data=data,
    )


def test_unprogram_route_refuses_without_confirm(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    fake = _FakeRouter({})
    from app.radius.routes import mt_programming as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: fake)
    res = _post_unprogram(client, nas_id=1, confirm=False)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "يجب تأكيد عملية الإزالة" in html
    assert fake.calls == [], "no router calls when refused"


def test_unprogram_route_rejects_unknown_kind(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    fake = _FakeRouter({})
    from app.radius.routes import mt_programming as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: fake)
    res = _post_unprogram(client, nas_id=1, kind="invalid")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "نوع البرمجة غير معروف" in html
    assert fake.calls == []


def test_unprogram_route_runs_and_renders_result(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    from app.radius.services.mt_programming import HOTSPOT_COMMENT
    fake = _FakeRouter({
        "/ip/pool": [{".id": "*99", "comment": HOTSPOT_COMMENT}],
    })
    from app.radius.routes import mt_programming as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: fake)
    res = _post_unprogram(client, nas_id=1, kind="hotspot")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-unprogram-result" in html
    assert "data-mt-unprogram-removed" in html
    # The result chip should show ≥1 removed (the seed row).
    assert "*99" in html
