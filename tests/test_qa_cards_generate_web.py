"""Regression: web POST to /cards/generate must not 500.

Guards against the `_collect_batch_options` NameError (time_value/time_unit
were referenced but never bound in scope), which made every card-batch
create/edit return HTTP 500.
"""
from __future__ import annotations

import secrets
from uuid import uuid4

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"cards_web_{uuid4().hex[:10]}"
    password = "cards-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Cards Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    client.get(url)
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_cards_generate_web_post_does_not_500(client):
    _web_login(client)
    token = _csrf(client, "/admin/radius/cards/generate")
    res = client.post(
        "/admin/radius/cards/generate",
        data={
            "_csrf_token": token,
            "plan_id": 1,
            "count": 1,
            "username_prefix": "qa" + secrets.token_hex(3),
            "username_length": 8,
            "password_length": 6,
            "batch_type": "printed",
            "time_limit_minutes": 60,  # exercises the time-normalisation path
            "device_count": 1,
        },
        follow_redirects=False,
    )
    # Success = redirect to the new batch / list, or a 200 re-render with a
    # validation flash. The bug produced a 500; assert that never happens.
    assert res.status_code != 500, res.get_data(as_text=True)[:500]
    assert res.status_code in {200, 302, 303}
