"""Dashboard API compatibility tests."""
from __future__ import annotations

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def test_dashboard_api_returns_nested_sections_and_flat_counter_aliases(client, monkeypatch):
    def fake_metrics():
        return {
            "subscribers": {"total": 17, "active": 14, "online": 3},
            "cards": {"total": 200, "used": 40, "available": 160, "batches": 5},
            "plans": {"total": 4, "enabled": 3},
            "nas": {"total": 2, "enabled": 2},
            "system": {"db_ok": True},
            "alerts": [],
        }

    monkeypatch.setattr(
        "app.radius.services.dashboard_metrics.build_dashboard_metrics",
        fake_metrics,
    )

    res = client.get("/api/v1/dashboard", headers=AUTH)

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["subscribers"]["total"] == 17
    assert data["cards"]["available"] == 160
    assert data["total_subscribers"] == 17
    assert data["active_subscribers"] == 14
    assert data["online_now"] == 3
    assert data["plans_total"] == 4
    assert data["total_cards"] == 200
    assert data["used_cards"] == 40
    assert data["available_cards"] == 160
    assert data["total_batches"] == 5
    assert data["nas_devices"] == 2
