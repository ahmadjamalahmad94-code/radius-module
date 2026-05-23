"""NPC Safe-Execution Phase 2 — snapshot capture engine."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_se2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
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


# ─── Reader interface ───────────────────────────────────────


def test_null_reader_refuses_every_call(app):
    from app.radius.services import (
        npc_router_state_reader as rd,
    )
    null = rd.NullStateReader()
    for method in (
        null.read_firewall_filters, null.read_address_lists,
        null.read_walled_garden, null.read_walled_garden_ip,
        null.read_managed_scheduler,
    ):
        with pytest.raises(rd.StateReaderNotConfigured):
            method(1)


def test_get_state_reader_defaults_to_null():
    from app.radius.services import (
        npc_router_state_reader as rd,
    )
    # Ensure no override sneaks in from a previous test.
    rd.set_state_reader(None)
    assert isinstance(rd.get_state_reader(), rd.NullStateReader)


def test_fake_reader_returns_provided_items():
    from app.radius.services import (
        npc_router_state_reader as rd,
    )
    reader = rd.FakeStateReader({
        rd.SECTION_FIREWALL_FILTER: [
            {"item_kind": "firewall_filter_rule",
             "source_id": "*5",
             "display_text": "drop",
             "payload": {"chain": "forward",
                          "action": "drop"}},
        ],
        rd.SECTION_ADDRESS_LIST: [
            rd.RouterItem(
                item_kind="address_list_entry",
                source_id="*7",
                display_text="HOBE_NPC_BLOCK:42",
                payload={"list": "HOBE_NPC_BLOCK_42",
                          "address": "tiktok.com"},
            ),
        ],
    })
    f = reader.read_firewall_filters(1)
    assert len(f) == 1
    assert f[0].source_id == "*5"
    assert f[0].payload["chain"] == "forward"

    al = reader.read_address_lists(1)
    assert len(al) == 1
    assert al[0].payload["address"] == "tiktok.com"

    # Missing sections default to empty.
    assert reader.read_walled_garden(1) == []
    assert reader.read_walled_garden_ip(1) == []
    assert reader.read_managed_scheduler(1) == []


# ─── Read pipeline ───────────────────────────────────────────


def test_read_pre_apply_state_uses_active_reader(app):
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )
    fake = rd.FakeStateReader({
        rd.SECTION_FIREWALL_FILTER: [
            {"item_kind": "firewall_filter_rule",
             "source_id": "*1",
             "display_text": "input accept winbox",
             "payload": {"chain": "input",
                          "action": "accept",
                          "dst-port": "8291"}},
        ],
    })
    out = cap.read_pre_apply_state(7, reader=fake)
    assert rd.SECTION_FIREWALL_FILTER in out
    assert len(out[rd.SECTION_FIREWALL_FILTER]) == 1
    # Sections not provided return empty lists, not None.
    assert out[rd.SECTION_ADDRESS_LIST] == []
    assert out[rd.SECTION_SCHEDULER] == []


def test_read_pre_apply_state_fails_closed_on_null_reader(app):
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )
    rd.set_state_reader(None)  # ensure default
    with pytest.raises(rd.StateReaderNotConfigured):
        cap.read_pre_apply_state(1)


def test_read_pre_apply_state_wraps_unexpected_errors(app):
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )

    class _Boom:
        def read_firewall_filters(self, _):
            raise RuntimeError("network down")
        # The rest can be omitted because the first call
        # raises and short-circuits the pipeline.

    with pytest.raises(rd.StateReadError) as exc_info:
        cap.read_pre_apply_state(1, reader=_Boom())
    assert "network down" in str(exc_info.value)


# ─── Capture ─────────────────────────────────────────────────


def test_capture_pre_apply_snapshot_persists_items(app):
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )
    fake = rd.FakeStateReader({
        rd.SECTION_FIREWALL_FILTER: [
            {"item_kind": "firewall_filter_rule",
             "source_id": "*1",
             "display_text": "drop",
             "payload": {"chain": "forward",
                          "action": "drop"}},
            {"item_kind": "firewall_filter_rule",
             "source_id": "*2",
             "display_text": "accept",
             "payload": {"chain": "input",
                          "action": "accept"}},
        ],
        rd.SECTION_ADDRESS_LIST: [
            {"item_kind": "address_list_entry",
             "source_id": "*3",
             "display_text": "blocked tiktok",
             "payload": {"list": "BLOCK",
                          "address": "tiktok.com"}},
        ],
    })
    with app.app_context():
        out = cap.capture_pre_apply_snapshot(
            tenant_id=1, router_id=42,
            policy_id=10, policy_type="web_block",
            created_by="alice",
            reader=fake,
        )
        # 3 items across 2 sections.
        assert out.item_count == 3
        assert set(out.sections_present) == {
            rd.SECTION_FIREWALL_FILTER, rd.SECTION_ADDRESS_LIST,
        }
        # Verify the rows landed.
        from app.radius.db.repos import npc_snapshots_repo as r
        items = r.list_items(out.snapshot_id)
    assert len(items) == 3
    kinds = {it["item_kind"] for it in items}
    assert "firewall_filter_rule" in kinds
    assert "address_list_entry" in kinds


def test_capture_redacts_secret_keys(app):
    """Payload field that LOOKS like a secret (e.g. `password`)
    is replaced with `<redacted>` BEFORE reaching the repo.
    The repo's hard reject is the second layer; this is the
    first."""
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )
    fake = rd.FakeStateReader({
        rd.SECTION_FIREWALL_FILTER: [
            {"item_kind": "firewall_filter_rule",
             "source_id": "*9",
             "display_text": "legit rule",
             "payload": {
                 "chain": "forward",
                 # These three are forbidden field-key names.
                 "password": "hunter2",
                 "API-Key": "abc123",
                 "credentials": {
                     "private-key": "AAAA==",
                 },
             }},
        ],
    })
    with app.app_context():
        out = cap.capture_pre_apply_snapshot(
            tenant_id=1, router_id=1,
            policy_id=1, policy_type="web_block",
            reader=fake,
        )
        # Redaction surfaced on the result + on the snapshot
        # row's notes for the change-history UI.
        assert out.item_count == 1
        assert "password" in out.redacted_keys
        # Case-folded.
        assert any("api-key" in k.lower() or "api_key" in k.lower()
                   for k in out.redacted_keys)

        from app.radius.db.repos import npc_snapshots_repo as r
        items = r.list_items(out.snapshot_id)
        payload = items[0]["payload"]
    # Forbidden keys are dropped entirely; safe ones remain.
    assert payload["chain"] == "forward"
    assert "password" not in payload
    assert "API-Key" not in payload
    # Nested forbidden key also gone; the `credentials`
    # parent stays as an empty dict.
    assert payload.get("credentials") == {}


def test_capture_fails_closed_when_reader_throws(app):
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )

    class _Broken:
        def read_firewall_filters(self, _):
            raise RuntimeError("ssh handshake failed")
        def read_address_lists(self, _):
            return []
        def read_walled_garden(self, _):
            return []
        def read_walled_garden_ip(self, _):
            return []
        def read_managed_scheduler(self, _):
            return []

    with app.app_context():
        with pytest.raises(rd.StateReadError):
            cap.capture_pre_apply_snapshot(
                tenant_id=1, router_id=1,
                reader=_Broken(),
            )
        # No partial snapshot rows landed.
        from app.radius.db.connection import db
        rows = db().execute(
            "SELECT COUNT(*) AS n FROM network_policy_snapshots "
            "WHERE tenant_id=1"
        ).fetchone()
        assert rows["n"] == 0


# ─── Tenant scoping ──────────────────────────────────────────


def test_capture_records_tenant_id_on_snapshot(app):
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )
    fake = rd.FakeStateReader({
        rd.SECTION_FIREWALL_FILTER: [
            {"item_kind": "firewall_filter_rule",
             "source_id": "*1",
             "display_text": "x",
             "payload": {"chain": "forward"}},
        ],
    })
    with app.app_context():
        out = cap.capture_pre_apply_snapshot(
            tenant_id=7, router_id=3, reader=fake,
        )
        from app.radius.db.repos import npc_snapshots_repo as r
        assert r.get_snapshot(7, out.snapshot_id) is not None
        # Cross-tenant read returns None.
        assert r.get_snapshot(1, out.snapshot_id) is None


# ─── No mutation contract ────────────────────────────────────


def test_reader_interface_exposes_only_read_methods():
    """Hard invariant: the reader's public surface is read-
    only. A future hand-rolled adapter that adds a `write_*`
    method must trip this test."""
    from app.radius.services import (
        npc_router_state_reader as rd,
    )
    # Inspect both shipped implementations.
    for impl in (rd.NullStateReader(), rd.FakeStateReader({})):
        public = [m for m in dir(impl)
                  if not m.startswith("_")]
        for name in public:
            assert name.startswith("read_"), (
                f"reader method {name} is not a `read_*` "
                "method — capture must remain read-only."
            )


# ─── Empty router ────────────────────────────────────────────


def test_capture_with_completely_empty_router(app):
    """Brand-new router → no sections populated. Capture
    should still produce a snapshot row (item_count=0) so the
    apply path has a baseline to roll back to."""
    from app.radius.services import (
        npc_router_state_reader as rd,
        npc_snapshot_capture_service as cap,
    )
    fake = rd.FakeStateReader({})
    with app.app_context():
        out = cap.capture_pre_apply_snapshot(
            tenant_id=1, router_id=1, reader=fake,
        )
        assert out.item_count == 0
        assert out.sections_present == ()
        from app.radius.db.repos import npc_snapshots_repo as r
        snap = r.get_snapshot(1, out.snapshot_id)
    assert snap is not None
    assert snap["snapshot_type"] == "composite"
