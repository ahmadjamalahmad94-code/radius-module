"""RouterOS reachability circuit-breaker + short connect timeout.

Regression guard for the 504-cascade fix: a dead router (e.g. ccr3 offline
17h) must NOT cost a connect timeout on every page load. Once observed
unreachable it is short-circuited for a TTL, and a healthy/auth-failing
router is never wrongly tripped.
"""
from __future__ import annotations

import time

import pytest

from app.radius.integration.mikrotik import reachability
from app.radius.integration.mikrotik import pool
from app.radius.integration.mikrotik.client import (
    MikrotikClient,
    _DEFAULT_CONNECT_TIMEOUT,
)
from app.radius.integration.mikrotik.errors import (
    AuthError,
    ConnectError,
    MikrotikError,
)


@pytest.fixture(autouse=True)
def _clean_breaker():
    reachability.reset()
    yield
    reachability.reset()


def _cfg(rid=9001, host="10.255.255.1", timeout_sec=3):
    return {
        "id": rid, "host": host, "port": 8728,
        "username": "u", "password": "p",
        "use_tls": False, "verify_tls": False,
        "timeout_sec": timeout_sec,
    }


# ── 1) breaker state machine ─────────────────────────────────────────
def test_failure_opens_success_closes():
    assert reachability.is_unreachable(7) is False
    reachability.record_failure(7)
    assert reachability.is_unreachable(7) is True
    reachability.record_success(7)
    assert reachability.is_unreachable(7) is False


def test_ttl_expiry_is_half_open(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MT_UNREACHABLE_TTL_SEC", "30")
    reachability.record_failure(8, now=1000.0)
    # within TTL → still open
    assert reachability.is_unreachable(8, now=1010.0) is True
    # past TTL → half-open: returns False (one probe allowed) and clears
    assert reachability.is_unreachable(8, now=1031.0) is False
    # entry cleared — stays closed until a new failure
    assert reachability.is_unreachable(8, now=1032.0) is False


def test_state_snapshot(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MT_UNREACHABLE_TTL_SEC", "45")
    reachability.record_failure(5, now=100.0)
    st = reachability.state(5, now=110.0)
    assert st["unreachable"] is True
    assert 34.0 <= st["retry_in_sec"] <= 35.0
    assert st["failures"] == 1


def test_falsy_router_id_is_noop():
    # id 0 / None must never trip or be considered unreachable
    assert reachability.is_unreachable(0) is False
    reachability.record_failure(None)
    assert reachability.is_unreachable(None) is False


# ── 2) client: connect timeout decoupled from read timeout ───────────
def test_connect_timeout_capped_short_by_default():
    # Long read timeout (backup_save style) must NOT slow the connect.
    c = MikrotikClient(host="h", username="u", password="p", timeout=60.0)
    assert c.timeout == 60.0
    assert c.connect_timeout == _DEFAULT_CONNECT_TIMEOUT  # 3s, not 60s


def test_connect_timeout_follows_short_read_timeout():
    # A deliberately short read timeout still bounds connect.
    c = MikrotikClient(host="h", username="u", password="p", timeout=2.0)
    assert c.connect_timeout == 2.0


def test_connect_timeout_env_override(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MT_CONNECT_TIMEOUT_SEC", "1.5")
    c = MikrotikClient(host="h", username="u", password="p", timeout=60.0)
    assert c.connect_timeout == 1.5


def test_dead_host_fails_fast():
    """A non-routable address must fail well under the read timeout."""
    # 10.255.255.1 is in a black-hole-prone RFC1918 range; with a 0.5s
    # connect timeout the dial must give up quickly, not hang.
    c = MikrotikClient(host="10.255.255.1", port=8728, username="u",
                       password="p", timeout=20.0, connect_timeout=0.5)
    started = time.monotonic()
    with pytest.raises(ConnectError):
        c.connect()
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"dead-host dial took {elapsed:.1f}s — connect timeout not applied"


# ── 3) pool: breaker short-circuits a dead router ────────────────────
def test_pool_dead_router_short_circuits_after_first_failure(monkeypatch):
    """The key fix: a dead router is dialed ONCE, then the breaker makes
    every subsequent acquire fail INSTANTLY without a socket attempt."""
    attempts = {"n": 0}

    class _Dead:
        def __init__(self, **kw):
            pass
        def connect(self):
            attempts["n"] += 1
            raise ConnectError("no route to host")
        def close(self):
            pass

    monkeypatch.setattr(pool, "MikrotikClient", _Dead)
    monkeypatch.setattr(pool, "resolve_connection_address", lambda cfg: cfg["host"])
    cfg = _cfg(rid=9101)

    # 1st dial: real attempt, fails, arms the breaker.
    with pytest.raises(ConnectError):
        with pool.acquire(cfg):
            pass
    assert attempts["n"] == 1
    assert reachability.is_unreachable(9101) is True

    # 2nd + 3rd dials: breaker open → instant fail, NO new socket attempt.
    for _ in range(2):
        with pytest.raises(ConnectError):
            with pool.acquire(cfg):
                pass
    assert attempts["n"] == 1, "dead router was re-dialed while breaker open"


def test_pool_healthy_router_keeps_breaker_closed(monkeypatch):
    class _Ok:
        def __init__(self, **kw):
            pass
        def connect(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(pool, "MikrotikClient", _Ok)
    monkeypatch.setattr(pool, "resolve_connection_address", lambda cfg: cfg["host"])
    cfg = _cfg(rid=9102)

    with pool.acquire(cfg) as client:
        assert client is not None
    assert reachability.is_unreachable(9102) is False


def test_pool_auth_failure_does_not_trip_breaker(monkeypatch):
    """An auth failure proves the router is REACHABLE — the breaker must
    stay closed so a creds problem isn't mistaken for an outage."""
    class _Auth:
        def __init__(self, **kw):
            pass
        def connect(self):
            raise AuthError("invalid user or password")
        def close(self):
            pass

    monkeypatch.setattr(pool, "MikrotikClient", _Auth)
    monkeypatch.setattr(pool, "resolve_connection_address", lambda cfg: cfg["host"])
    cfg = _cfg(rid=9103)

    with pytest.raises(MikrotikError):
        with pool.acquire(cfg):
            pass
    assert reachability.is_unreachable(9103) is False


def test_pool_recovery_after_breaker_expiry(monkeypatch):
    """Once the TTL lapses, the next dial probes again and a now-healthy
    router closes the breaker and serves traffic."""
    monkeypatch.setenv("HOBERADIUS_MT_UNREACHABLE_TTL_SEC", "30")
    state = {"alive": False}

    class _Flaky:
        def __init__(self, **kw):
            pass
        def connect(self):
            if not state["alive"]:
                raise ConnectError("down")
        def close(self):
            pass

    monkeypatch.setattr(pool, "MikrotikClient", _Flaky)
    monkeypatch.setattr(pool, "resolve_connection_address", lambda cfg: cfg["host"])
    cfg = _cfg(rid=9104)

    # Dead → breaker armed.
    with pytest.raises(ConnectError):
        with pool.acquire(cfg):
            pass
    assert reachability.is_unreachable(9104) is True

    # Simulate TTL lapse by clearing the breaker (half-open), router back up.
    reachability.reset()
    state["alive"] = True
    with pool.acquire(cfg) as client:
        assert client is not None
    assert reachability.is_unreachable(9104) is False


# ── 4) page-facing admin-client surface benefits end-to-end ──────────
def test_admin_client_surface_fast_offline_and_not_redialed(monkeypatch):
    """The page-facing call (`system_resource`) on a dead router must
    return a clean offline envelope (ok=False) WITHOUT re-dialing the
    router on the second request — proving a dead router can't pin a
    worker thread on every page load (the 504 cascade)."""
    from app.radius.services import mikrotik_admin_client as mac

    attempts = {"n": 0}

    class _Dead:
        def __init__(self, **kw):
            pass
        def connect(self):
            attempts["n"] += 1
            raise ConnectError("no route to host")
        def close(self):
            pass

    # Dead dial at the pool layer; resolver passthrough so a plain
    # `address` is enough (no DB / VPN peer lookup needed).
    monkeypatch.setattr(pool, "MikrotikClient", _Dead)
    monkeypatch.setattr(pool, "resolve_connection_address", lambda cfg: cfg["host"])
    monkeypatch.setattr(mac, "resolve_connection_address", lambda nas: nas["address"])
    monkeypatch.setattr(
        mac, "resolve_connection_descriptor",
        lambda nas: {"address": nas["address"], "mode": "direct"},
    )

    nas = {"id": 9105, "address": "10.255.255.9", "connection_mode": "direct",
           "api_port": 8728, "api_user": "u", "api_password": "p",
           "api_use_tls": 0, "api_timeout_sec": 3}

    r1 = mac.system_resource(nas)
    assert r1.ok is False
    assert attempts["n"] == 1
    assert reachability.is_unreachable(9105) is True

    # Second page load: breaker short-circuits — still ok=False, NO re-dial.
    r2 = mac.system_resource(nas)
    assert r2.ok is False
    assert attempts["n"] == 1, "dead router re-dialed on second page load"
