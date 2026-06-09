"""R1 foundation tests for card-batch lifecycle, visibility, and checker UI."""
from __future__ import annotations

import secrets

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _prefix() -> str:
    return "r1" + secrets.token_hex(4)


def _generate(client, auth_headers, *, count: int = 1, **overrides):
    body = {
        "plan_id": 1,
        "count": count,
        "username_prefix": _prefix(),
        "password_length": 12,
    }
    body.update(overrides)
    res = client.post("/api/v1/cards/generate", json=body, headers=auth_headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def _web_login(client) -> None:
    res = client.post(
        "/admin/radius/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    client.get(url)
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_card_batch_lifecycle_columns_are_additive(app):
    from app.radius.db.connection import db

    columns = {
        row["name"]
        for row in db().execute("PRAGMA table_info(card_batches)").fetchall()
    }
    assert {"deleted_at", "deleted_by", "delete_reason"}.issubset(columns)


def test_archiving_batch_does_not_hide_or_delete_cards(client, auth_headers):
    from app.radius.db.repos import cards_repo

    data = _generate(client, auth_headers, count=2)
    batch_id = data["batch"]["id"]

    assert cards_repo.archive_batch(1, batch_id, actor="qa", reason="operator cleanup")
    assert cards_repo.archive_batch(1, batch_id, actor="qa", reason="again") is False

    batch = cards_repo.get_batch(1, batch_id)
    cards = cards_repo.list_cards(1, batch_id=batch_id)
    assert batch is not None
    assert batch.deleted_at is not None
    assert batch.deleted_by == "qa"
    assert batch.delete_reason == "operator cleanup"
    assert batch.status == "deleted"
    assert len(cards) == 2


def test_batch_summary_counts_available_active_expired_and_revoked(client, auth_headers):
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso
    from app.radius.db.repos import cards_repo

    data = _generate(client, auth_headers, count=4)
    batch_id = data["batch"]["id"]
    cards = cards_repo.list_cards(1, batch_id=batch_id, limit=10)
    assert len(cards) == 4

    with transaction() as conn:
        conn.execute(
            "UPDATE cards SET used = 1, first_used_at = ? WHERE id = ?",
            (now_iso(), cards[0].id),
        )
        conn.execute("UPDATE cards SET expire_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", cards[1].id))
        conn.execute("UPDATE cards SET revoked = 1 WHERE id = ?", (cards[2].id,))

    res = client.get(f"/api/v1/cards/batches/{batch_id}/summary", headers=auth_headers)
    assert res.status_code == 200, res.get_json()
    summary = res.get_json()["data"]["summary"]
    assert summary["total_cards"] == 4
    assert summary["active_count"] == 1
    assert summary["expired_count"] == 1
    assert summary["revoked_count"] == 1
    assert summary["available_count"] == 1
    assert summary["remaining_count"] == 1
    assert "password" not in summary


def test_card_batches_operations_page_filters_and_exports_csv(client, auth_headers):
    data = _generate(
        client,
        auth_headers,
        count=2,
        package_name="Ops Batch QA",
        price_per_card=3.5,
    )
    batch = data["batch"]
    _web_login(client)

    page = client.get(
        "/admin/radius/cards/batches",
        query_string={"q": batch["batch_code"], "per_page": "10"},
    )
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert batch["batch_code"] in html
    assert "bops-table" in html
    assert "CSV" in html
    assert "Excel" in html
    assert "PDF" in html
    assert "أرشفة آمنة" in html

    export = client.get(
        "/admin/radius/cards/batches/export.csv",
        query_string={"q": batch["batch_code"]},
    )
    assert export.status_code == 200
    assert export.headers["Content-Type"].startswith("text/csv")
    csv_text = export.get_data(as_text=True)
    assert batch["batch_code"] in csv_text
    assert "رقم الحزمة" in csv_text
    for card in data["cards"]:
        assert card["password"] not in csv_text

    xlsx = client.get(
        "/admin/radius/cards/batches/export.xlsx",
        query_string={"q": batch["batch_code"]},
    )
    assert xlsx.status_code == 200
    assert xlsx.headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert xlsx.data.startswith(b"PK")

    pdf = client.get(
        "/admin/radius/cards/batches/export.pdf",
        query_string={"q": batch["batch_code"]},
    )
    assert pdf.status_code == 200
    assert pdf.headers["Content-Type"].startswith("application/pdf")
    assert pdf.data.startswith(b"%PDF")


def test_card_batches_web_import_external_file_is_safe_bookkeeping(client, auth_headers):
    from app.radius.db.connection import db

    _web_login(client)
    page = client.get("/admin/radius/cards/batches/import")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "استيراد ملف كروت" in html
    assert "ملف خارجي" in html

    token = _csrf(client, "/admin/radius/cards/batches/import")
    username_one = "web-external-qa-1-" + secrets.token_hex(4)
    username_two = "web-external-qa-2-" + secrets.token_hex(4)
    secret_one = "visible-secret-one"
    secret_two = "visible-secret-two"
    res = client.post(
        "/admin/radius/cards/batches/import",
        data={
            "_csrf_token": token,
            "plan_id": "1",
            "source_type": "external",
            "package_name": "Web External Import QA",
            "price_per_card": "1.50",
            "csv_text": f"username,password\n{username_one},{secret_one}\n{username_two},{secret_two}\n",
            "sync_to_radius": "1",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Web External Import QA" in html
    assert username_one not in html
    assert secret_one not in html
    assert secret_two not in html

    external_batch = db().execute(
        """
        SELECT id, source_type, original_count, generated
        FROM card_batches
        WHERE package_name = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        ("Web External Import QA",),
    ).fetchone()
    assert external_batch is not None
    assert external_batch["source_type"] == "external"
    assert external_batch["original_count"] == 2
    assert external_batch["generated"] == 2

    radius_accounts = db().execute(
        "SELECT username FROM subscribers WHERE username IN (?, ?)",
        (username_one, username_two),
    ).fetchall()
    assert radius_accounts == []


def test_card_batches_bulk_archive_is_soft_and_preserves_cards(client, auth_headers):
    from app.radius.db.repos import cards_repo

    data = _generate(client, auth_headers, count=2, package_name="Bulk Archive QA")
    batch_id = data["batch"]["id"]
    _web_login(client)
    token = _csrf(client, "/admin/radius/cards/batches")

    res = client.post(
        "/admin/radius/cards/batches/bulk",
        data={
            "_csrf_token": token,
            "batch_ids": str(batch_id),
            "bulk_action": "archive",
            "reason": "qa bulk archive",
            "return_to": "/admin/radius/cards/batches",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    archived = cards_repo.get_batch(1, batch_id)
    cards = cards_repo.list_cards(1, batch_id=batch_id)
    assert archived is not None
    assert archived.deleted_at is not None
    assert archived.delete_reason == "qa bulk archive"
    assert len(cards) == 2


def test_card_checker_ui_route_and_result_never_expose_password(client, auth_headers):
    data = _generate(client, auth_headers, count=1)
    card = data["cards"][0]

    _web_login(client)
    res = client.get(
        "/admin/radius/cards/checker",
        query_string={"query": card["username"]},
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert card["username"] in html
    assert "فحص بطاقة" in html
    assert "الدفعة" in html
    assert "الباقة" in html
    assert "كلمة مرور" in html
    assert "مركز عمليات البطاقة" in html
    assert card["password"] not in html


def test_card_checker_operations_show_accounting_and_lock_mac(client, auth_headers):
    from app.radius.db.connection import transaction
    from app.radius.db.repos import cards_repo

    data = _generate(client, auth_headers, count=1)
    card = data["cards"][0]
    username = card["username"]
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO radacct(
                tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
                nasportid, nasporttype, acctstarttime, acctupdatetime,
                acctstoptime, acctsessiontime, acctinputoctets, acctoutputoctets,
                callingstationid, calledstationid, framedipaddress, servicetype,
                framedprotocol, connectinfo_start
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, "sess-online", "uniq-online", username, "10.10.0.1",
                "ether1", "Wireless-802.11", "2026-05-20T08:00:00Z",
                "2026-05-20T08:20:00Z", None, 1200, 2048, 4096,
                "AA:BB:CC:DD:EE:01", "hotspot", "172.16.1.50", "Framed-User",
                "PPP", "android wifi"
            ),
        )
        conn.execute(
            """
            INSERT INTO radacct(
                tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
                nasportid, nasporttype, acctstarttime, acctupdatetime,
                acctstoptime, acctsessiontime, acctinputoctets, acctoutputoctets,
                callingstationid, calledstationid, framedipaddress, servicetype,
                framedprotocol, connectinfo_start, acctterminatecause
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, "sess-old", "uniq-old", username, "10.10.0.2",
                "ether2", "Ethernet", "2026-05-19T08:00:00Z",
                "2026-05-19T09:00:00Z", "2026-05-19T09:00:00Z",
                3600, 1024, 2048, "AA:BB:CC:DD:EE:02", "hotspot",
                "172.16.1.60", "Framed-User", "PPP", "windows", "User-Request"
            ),
        )

    _web_login(client)
    res = client.get(
        "/admin/radius/cards/checker",
        query_string={"query": username},
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # R13.A.4: assertions updated for the new operations-room layout.
    # Same data is shown — labels and section names changed:
    #   "عدد MAC مختلف"        → stat tile labelled "MACs مميَّزة"
    #   "جدول الجلسات التفصيلي" → section labelled "آخر الجلسات"
    # R13.A.7: redesigned to match approved references. Stats tile labels
    # changed from "MACs مميَّزة" to "الأجهزة المتّصلة". Sessions section
    # title kept as "آخر الجلسات".
    assert "الأجهزة المتّصلة" in html
    assert "آخر الجلسات" in html
    # R13.A.7: the redesigned sessions list shows MAC + IP + duration
    # + status, not the raw acctsessionid (which is internal). MAC is the
    # primary visible identifier of each session row now.
    assert "AA:BB:CC:DD:EE:01" in html
    # R13.A.6: device fingerprinting replaces the raw "android wifi"
    # connect_info string with a parsed badge. The MAC AA:BB:CC has the
    # U/L bit set (randomized) + connect_info contains "android" →
    # infer_device labels it as a modern Android phone.
    assert "أندرويد" in html, \
        "device fingerprint badge for android-hinted random MAC must render"
    # password must never leak — regardless of layout
    assert card["password"] not in html

    token = _csrf(client, f"/admin/radius/cards/checker?query={username}")
    lock = client.post(
        "/admin/radius/cards/checker",
        data={
            "_csrf_token": token,
            "_card_action": "lock_mac",
            "query": username,
            "username": username,
            "card_id": str(card["id"]),
            "mac": "AA:BB:CC:DD:EE:01",
        },
        follow_redirects=True,
    )
    assert lock.status_code == 200
    updated = cards_repo.get_card_by_username(1, username)
    assert updated is not None
    assert updated.locked_mac == "AA:BB:CC:DD:EE:01"
    assert "تم تثبيت عنوان MAC" in lock.get_data(as_text=True)


def test_card_checker_ui_empty_and_long_query_are_safe(client):
    _web_login(client)
    empty = client.get("/admin/radius/cards/checker")
    assert empty.status_code == 200
    assert "مركز عمليات البطاقة" in empty.get_data(as_text=True)

    long_query = client.get(
        "/admin/radius/cards/checker",
        query_string={"query": "x" * 129},
    )
    assert long_query.status_code == 200
    assert "لا يتجاوز 128" in long_query.get_data(as_text=True)


def test_roadmap_audit_payload_shape_is_stable():
    from app.radius.core.constants import AUDIT_ACTION_LOAN_GRANT
    from app.radius.services.audit_events import roadmap_audit_payload

    payload = roadmap_audit_payload(
        domain="loans",
        action=AUDIT_ACTION_LOAN_GRANT,
        reason="temporary activation",
        metadata={"hours": 3},
    )
    assert payload == {
        "schema": "customer-roadmap.audit.v1",
        "domain": "loans",
        "action": "loan_grant",
        "reason": "temporary activation",
        "metadata": {"hours": 3},
    }
