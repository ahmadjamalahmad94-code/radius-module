"""F-04 — coverage for POST /api/v1/invoices/<id>/status (used by the Flutter
app: invoices_repository.dart updateStatus + saas_modules_repository.dart).

Body contract from the app (InvoiceStatusUpdate.toApiJson):
``{"status": <str>, "note"?: <non-empty str>}``; the app parses ``data`` as an
InvoiceRecord, so the response must carry the updated row.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest

DEV = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_inv_status_")
    keys = ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED",
            "HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "HOBERADIUS_ENV", "FLASK_ENV")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
    os.environ.pop("HOBERADIUS_ENV", None)
    os.environ.pop("FLASK_ENV", None)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
    yield flask_app
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _invoice(app, client, *, status="pending", note="first") -> int:
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        s = subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="inv_" + uuid4().hex[:8],
            password="p1234567", status="enabled"))
    r = client.post("/api/v1/invoices", headers=DEV, json={
        "subscriber_id": int(s.id), "amount": 5, "status": status, "note": note})
    assert r.status_code == 201, r.get_json()
    return int(r.get_json()["data"]["id"])


def _row(app, iid):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute(
            "SELECT status, note FROM invoices WHERE id = ?", (iid,)).fetchone()


def test_requires_api_token(app, client):
    iid = _invoice(app, client)
    r = client.post(f"/api/v1/invoices/{iid}/status", json={"status": "paid"})
    assert r.status_code == 401
    assert _row(app, iid)["status"] == "pending"


def test_status_and_note_update_and_response_is_the_record(app, client):
    iid = _invoice(app, client)
    r = client.post(f"/api/v1/invoices/{iid}/status", headers=DEV,
                    json={"status": "paid", "note": "cash received"})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    # fields InvoiceRecord.fromJson reads
    assert data["id"] == iid
    assert data["status"] == "paid"
    assert data["note"] == "cash received"
    assert data["updated_at"]
    row = _row(app, iid)
    assert (row["status"], row["note"]) == ("paid", "cash received")


def test_omitted_note_keeps_the_previous_note(app, client):
    """The app omits ``note`` when blank — that must not wipe the old note."""
    iid = _invoice(app, client, note="keep me")
    r = client.post(f"/api/v1/invoices/{iid}/status", headers=DEV,
                    json={"status": "canceled"})
    assert r.status_code == 200
    assert r.get_json()["data"]["note"] == "keep me"
    assert _row(app, iid)["status"] == "canceled"


@pytest.mark.parametrize("status", ["refunded", "failed", "pending", "paid", "canceled"])
def test_every_allowed_status_is_accepted(app, client, status):
    iid = _invoice(app, client)
    r = client.post(f"/api/v1/invoices/{iid}/status", headers=DEV,
                    json={"status": status})
    assert r.status_code == 200
    assert _row(app, iid)["status"] == status


@pytest.mark.parametrize("body", [{"status": "PAID"}, {"status": "done"}, {"status": ""}, {}])
def test_unknown_status_is_422_and_row_unchanged(app, client, body):
    iid = _invoice(app, client)
    r = client.post(f"/api/v1/invoices/{iid}/status", headers=DEV, json=body)
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "validation_error"
    assert _row(app, iid)["status"] == "pending"


@pytest.mark.parametrize("raw", ["[1]", '"x"'])
def test_non_object_body_is_422_not_500(app, client, raw):
    iid = _invoice(app, client)
    r = client.post(f"/api/v1/invoices/{iid}/status", headers=DEV, data=raw,
                    content_type="application/json")
    assert r.status_code == 422


def test_unknown_invoice_is_404(app, client):
    r = client.post("/api/v1/invoices/987654/status", headers=DEV,
                    json={"status": "paid"})
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "not_found"
