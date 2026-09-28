"""GET /api/v1/accounts?expiring_within_days=N.

The users service and the web list already filter «ينتهي خلال N أيام»
(attention=expiring_3d, dashboard counter expiring_soon), but the REST list
dropped the param, so the mobile app's dashboard deep link could not ask the
server for exactly those subscribers. Contract pinned here:

  * expiring_within_days=3 returns subscribers whose expire_at falls between
    now and now+3d — and not one expiring later, nor one already expired;
  * it combines with status (status=enabled like the web card);
  * absent param keeps the old behaviour;
  * bad values are a 422, never a silent full list.
"""
from __future__ import annotations

import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

for _m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
    sys.modules.pop(_m, None)

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture(scope="module")
def client():
    from app import create_app
    return create_app().test_client()


@pytest.fixture(scope="module")
def plan_id(client):
    """Own plan — don't depend on demo seeding having created plan #1."""
    res = client.post(
        "/api/v1/profiles",
        json={"name": "exp-filter-" + secrets.token_hex(3),
              "code": "EXF" + secrets.token_hex(2).upper(),
              "plan_type": "time", "duration_minutes": 60, "enabled": True,
              "speed_down_kbps": 4000, "speed_up_kbps": 2000},
        headers=AUTH,
    )
    assert res.status_code in (200, 201), res.get_json()
    return res.get_json()["data"]["id"]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


_PLAN: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _remember_plan(plan_id):
    _PLAN["id"] = plan_id


def _mk(client, tag: str, expire_at: datetime) -> str:
    u = f"exp{tag}_{secrets.token_hex(4)}"
    res = client.post(
        "/api/v1/accounts",
        json={"username": u, "password": "pw1234", "plan_id": _PLAN["id"],
              "status": "enabled", "expire_at": _iso(expire_at)},
        headers=AUTH,
    )
    assert res.status_code in (200, 201), res.get_json()
    return u


def _usernames(res) -> set[str]:
    return {i["username"] for i in res.get_json()["data"]["items"]}


def test_expiring_window_returns_only_near_expiries(client):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon = _mk(client, "soon", now + timedelta(days=1))
    later = _mk(client, "later", now + timedelta(days=20))
    past = _mk(client, "past", now - timedelta(days=2))

    res = client.get(
        "/api/v1/accounts?status=enabled&expiring_within_days=3&limit=500",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    got = _usernames(res)
    assert soon in got
    assert later not in got
    assert past not in got


def test_without_param_keeps_old_behaviour(client):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    later = _mk(client, "nofilter", now + timedelta(days=40))
    res = client.get("/api/v1/accounts?search=" + later, headers=AUTH)
    assert res.status_code == 200
    assert later in _usernames(res)


@pytest.mark.parametrize("bad", ["abc", "0", "-1", "999"])
def test_invalid_value_is_422(client, bad):
    res = client.get(f"/api/v1/accounts?expiring_within_days={bad}", headers=AUTH)
    assert res.status_code == 422, res.get_json()
    assert res.get_json()["error"]["code"] == "validation_error"
