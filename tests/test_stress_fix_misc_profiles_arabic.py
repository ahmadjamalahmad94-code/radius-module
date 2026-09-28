"""Stress-fix (misc stream, A05 F13): /profiles errors are Arabic, never raw
English or a Python decoder message; negative limit is clamped."""
from __future__ import annotations

import re
import secrets
import sys

import pytest

for _m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
    sys.modules.pop(_m, None)

AUTH = {"Authorization": "Bearer dev-token-please-change"}
_LATIN_SENTENCE = re.compile(r"\b(must|unknown|required|expecting|line \d)\b", re.I)
_ARABIC = re.compile(r"[؀-ۿ]")


@pytest.fixture(scope="module")
def client():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOBERADIUS_NO_WORKER", "1")
        mp.setenv("HOBERADIUS_NO_SEED", "1")
        from app import create_app
        yield create_app().test_client()


def _plan(**extra):
    return {"name": "pa_" + secrets.token_hex(3), "plan_type": "time",
            "duration_minutes": 60, "speed_down_kbps": 4000, "speed_up_kbps": 2000, **extra}


@pytest.mark.parametrize("extra", [
    {"plan_type": "bogus"},
    {"speed_down_kbps": -1},
    {"concurrent_sessions": 0},
    {"service_scope": "moon"},
    {"max_loan_minutes": -5},
    {"metadata": "{bad"},
    {"name": ""},
])
def test_profile_errors_are_arabic(client, extra):
    res = client.post("/api/v1/profiles", headers=AUTH, json=_plan(**extra))
    assert res.status_code == 422, res.get_json()
    msg = res.get_json()["error"]["message"]
    assert _ARABIC.search(msg), msg
    assert not _LATIN_SENTENCE.search(msg), msg


def test_profile_not_found_is_arabic(client):
    for method in ("get", "patch", "delete"):
        res = getattr(client, method)("/api/v1/profiles/99999999", headers=AUTH, json={})
        assert res.status_code == 404
        assert "profile" not in res.get_json()["error"]["message"]


def test_profile_list_body_and_negative_limit(client):
    assert client.post("/api/v1/profiles", headers=AUTH, json=[1]).status_code == 422
    for _ in range(2):
        assert client.post("/api/v1/profiles", headers=AUTH, json=_plan()).status_code == 201
    data = client.get("/api/v1/profiles?limit=-5", headers=AUTH).get_json()["data"]
    assert data["count"] == 1
