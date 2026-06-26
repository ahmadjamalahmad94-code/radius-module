"""Diagnostics redesign v2 — performance (circuit-breaker reuse) +
the explicit named-checklist card with a *why* for every skipped state.

Per-file isolation (fresh app/db). The reachability breaker is reset by the
autouse conftest fixture between tests."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_diagv2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.10.0.0/24")
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
    u = f"dv2_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="dv2-pass",
                             full_name="DV2", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "dv2-pass"})
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int, host: str, mode: str = "direct") -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     connection_mode, vpn_peer_address, created_at)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, 'hr-test', 'pw', ?, ?, ?)""",
                (nas_id, f"rt-{nas_id}", host, mode,
                 host if mode == "vpn" else "", now),
            )


# ─────────────────────────────────────────────────────────────────────
# PERFORMANCE — circuit-breaker reuse
# ─────────────────────────────────────────────────────────────────────

def test_breaker_skips_second_probe_after_failure(app, monkeypatch):
    """A failed TCP dial arms the breaker; the next diagnose for the same
    router must NOT pay the timeout again — it returns instantly with an
    explicit breaker_open verdict and zero further probe calls."""
    _seed(app, nas_id=300, host="203.0.113.30", mode="direct")
    from app.radius.services import mt_diagnostics

    calls = {"tcp": 0, "api": 0}

    def _tcp(host, port, timeout=2.5):
        calls["tcp"] += 1
        return {"ok": False, "latency_ms": None, "error": "timed_out",
                "hint": "stub"}

    def _api(cfg):
        calls["api"] += 1
        return {"ok": False, "latency_ms": None, "identity": "",
                "error": "x", "hint": ""}

    monkeypatch.setattr(mt_diagnostics, "_tcp_probe", _tcp)
    monkeypatch.setattr(mt_diagnostics, "_api_probe", _api)

    with app.app_context():
        first = mt_diagnostics.diagnose_one(1, 300)
        second = mt_diagnostics.diagnose_one(1, 300)

    # First probe ran for real and failed.
    assert first["status"] == "tcp_failed"
    assert first["breaker_open"] is False
    # Second was short-circuited by the breaker — no extra probe at all.
    assert second["status"] == "tcp_failed"
    assert second["breaker_open"] is True
    assert calls["tcp"] == 1 and calls["api"] == 0
    # Still actionable: probable causes are present on the reused verdict.
    assert second["probable_causes"]


def test_breaker_closes_after_tcp_success(app, monkeypatch):
    """A reachable router (TCP ok) closes the breaker — it never gets
    skipped, even right after an unrelated failure elsewhere."""
    _seed(app, nas_id=301, host="203.0.113.31", mode="direct")
    from app.radius.services import mt_diagnostics
    from app.radius.integration.mikrotik import reachability

    monkeypatch.setattr(mt_diagnostics, "_tcp_probe",
                        lambda h, p, timeout=2.5: {"ok": True, "latency_ms": 12,
                                                   "error": "", "hint": ""})
    monkeypatch.setattr(mt_diagnostics, "_api_probe",
                        lambda cfg: {"ok": True, "latency_ms": 30,
                                     "identity": "R1", "ntp": {"checked": True,
                                     "enabled": True, "clock": "", "warning": ""},
                                     "error": "", "hint": ""})
    with app.app_context():
        entry = mt_diagnostics.diagnose_one(1, 301)
        assert entry["status"] == "ok"
        assert reachability.is_unreachable(301) is False


def test_diagnostics_shell_still_does_not_probe(app, client, monkeypatch):
    """The page shell stays a pure-DB render — no probe, no breaker touch."""
    _seed(app, nas_id=302, host="203.0.113.32")
    from app.radius.services import mt_diagnostics
    calls = {"n": 0}
    monkeypatch.setattr(mt_diagnostics, "_tcp_probe",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or
                        {"ok": False, "latency_ms": None, "error": "", "hint": ""})
    with app.app_context():
        _login(client)
        html = client.get("/admin/radius/diagnostics").get_data(as_text=True)
    assert "data-diag-card" in html
    assert "/admin/radius/diagnostics/router/302" in html
    assert calls["n"] == 0


# ─────────────────────────────────────────────────────────────────────
# REDESIGN — explicit named-checklist + why for skipped states
# ─────────────────────────────────────────────────────────────────────

def _card(client, nas_id):
    return client.get(f"/admin/radius/diagnostics/router/{nas_id}").get_data(as_text=True)


def test_card_renders_named_checklist(app, client, monkeypatch):
    """A reachable router shows the three named checks, each with a state."""
    _seed(app, nas_id=310, host="203.0.113.40", mode="direct")
    from app.radius.services import mt_diagnostics
    monkeypatch.setattr(mt_diagnostics, "_tcp_probe",
                        lambda h, p, timeout=2.5: {"ok": True, "latency_ms": 11,
                                                   "error": "", "hint": ""})
    monkeypatch.setattr(mt_diagnostics, "_api_probe",
                        lambda cfg: {"ok": True, "latency_ms": 22, "identity": "CCR-X",
                                     "ntp": {"checked": True, "enabled": True,
                                             "clock": "jan/01 00:00", "warning": ""},
                                     "error": "", "hint": ""})
    with app.app_context():
        _login(client)
        html = _card(client, 310)
    assert "dchk-list" in html
    assert 'data-dchk="ok"' in html
    assert "الوصول إلى المنفذ" in html
    assert "تسجيل الدخول (API)" in html
    assert "تزامن الساعة (NTP)" in html
    assert "CCR-X" in html                       # identity surfaced in the reason


def test_card_explains_why_checks_were_skipped(app, client, monkeypatch):
    """The owner's complaint: «غير معروف» was vague. A closed port must say
    WHY login/NTP weren't checked — not leave them blank/unknown."""
    _seed(app, nas_id=311, host="203.0.113.41", mode="direct")
    from app.radius.services import mt_diagnostics
    monkeypatch.setattr(mt_diagnostics, "_tcp_probe",
                        lambda h, p, timeout=2.5: {"ok": False, "latency_ms": None,
                                                   "error": "timed_out", "hint": "stub"})
    with app.app_context():
        _login(client)
        html = _card(client, 311)
    assert 'data-dchk="fail"' in html            # TCP failed
    assert 'data-dchk="skip"' in html            # login + NTP skipped
    # explicit reasons, not a bare «غير معروف»
    assert "لم يُفحص: المنفذ مغلق" in html
    assert "يتطلّب تسجيل دخول ناجحاً" in html
    # repair script (code-card) still present for the closed-port case
    assert 'data-mt-repair-mode="direct"' in html


def test_disabled_router_checks_say_disabled(app, client):
    """A disabled router shows all checks skipped with the disabled reason."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor, nas_type,
                     enabled, api_user, api_password, connection_mode,
                     vpn_peer_address, created_at)
                   VALUES (312, 1, 'rt-off', '203.0.113.42', 'sek', 'mikrotik',
                           'hotspot', 0, 'hr-test', 'pw', 'direct', '', ?)""",
                (now,),
            )
        _login(client)
        html = _card(client, 312)
    assert 'data-diag-status="disabled"' in html
    assert "الراوتر معطّل من الإعدادات" in html
