from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sgroups_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _logged_in(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
    return client


def test_subscriber_group_list_includes_usage_and_online_metrics(app):
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import subscriber_groups_repo

        with transaction() as c:
            gid = c.execute(
                """
                INSERT INTO subscriber_groups(tenant_id, name, description, created_at)
                VALUES (?,?,?,?)
                """,
                (1, "VIP", "priority users", now),
            ).lastrowid
            c.execute(
                """
                INSERT INTO subscribers(
                    tenant_id, username, password, status, subscriber_group_id,
                    custom_speed, created_at
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                (1, "fk-member", "pw", "enabled", gid, 1, now),
            )
            c.execute(
                """
                INSERT INTO subscribers(
                    tenant_id, username, password, status, group_name,
                    temporary_speed, created_at
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                (1, "legacy-member", "pw", "disabled", "VIP", 1, now),
            )
            c.execute(
                """
                INSERT INTO radacct(
                    tenant_id, acctsessionid, acctuniqueid, username,
                    nasipaddress, framedipaddress, callingstationid,
                    acctstarttime, acctinputoctets, acctoutputoctets
                )
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (1, "s1", "u1", "fk-member", "10.0.0.1", "10.1.1.2",
                 "AA:BB:CC:DD:EE:01", now, 1024, 2048),
            )
            c.execute(
                """
                INSERT INTO radacct(
                    tenant_id, acctsessionid, acctuniqueid, username,
                    nasipaddress, framedipaddress, callingstationid,
                    acctstarttime, acctstoptime, acctinputoctets, acctoutputoctets
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (1, "s2", "u2", "legacy-member", "10.0.0.1", "10.1.1.3",
                 "AA:BB:CC:DD:EE:02", now, now, 4096, 8192),
            )

        group = subscriber_groups_repo.list_groups(1)[0]

    assert group["members"] == 2
    assert group["enabled_members"] == 1
    assert group["disabled_members"] == 1
    assert group["custom_speed_members"] == 1
    assert group["temporary_speed_members"] == 1
    assert group["online_now"] == 1
    assert group["total_download_bytes"] == 5120
    assert group["total_upload_bytes"] == 10240
    assert group["session_count"] == 2


def test_subscriber_groups_page_renders_group_actions(app):
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction

        with transaction() as c:
            c.execute(
                """
                INSERT INTO subscriber_groups(tenant_id, name, description, created_at)
                VALUES (?,?,?,?)
                """,
                (1, "Office", "office users", now),
            )

    resp = _logged_in(app).get("/admin/radius/subscriber-groups")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "التحميل / الرفع" in body
    assert "إجراءات المجموعة" in body
    assert "subscriber-groups/1/disconnect-online" in body
    assert "subscriber-groups/1/quota/reset-daily" in body
