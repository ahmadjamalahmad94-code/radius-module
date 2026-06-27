# -*- coding: utf-8 -*-
"""SSTP/PPTP management-tunnel RADIUS provisioning — MSCHAP-v2 compatibility.

Covers the permanent fix for the ccr4 live incident (FreeRADIUS rejected
`rtr-ccr4` because it had no MSCHAP-v2-usable secret):

  * NT-Password (MD4/UTF-16-LE) computed correctly (known vectors).
  * provision_tunnel writes BOTH Cleartext-Password + NT-Password.
  * ensure_tunnel_radius_user is idempotent: no IP churn, password preserved,
    legacy (Cleartext-only) accounts upgraded to carry NT-Password.
  * tunnel_radius_status / diagnose_tunnel_login distinguish every failure
    mode the "Test SSTP / RADIUS Login" button must surface.
  * RouterOS SSTP mgmt block uses profile=default (NOT default-encryption).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sstp_prov_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "sstp-prov-secret")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    yield application
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


# ════════════ 1) NT-Password / MD4 correctness ════════════
def test_nt_password_known_vectors():
    from app.radius.services.router_mgmt_tunnel import nt_password_hash, _md4_pure
    # Classic MSCHAP NT-hash vector.
    assert nt_password_hash("password") == "8846F7EAEE8FB117AD06BDD830B7586C"
    # RFC 1320 MD4 vectors.
    assert _md4_pure(b"").hex() == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert _md4_pure(b"abc").hex() == "a448017aaf21d8525fc10ae87aa6729d"
    assert _md4_pure(b"message digest").hex() == "d9130a8164549fe818874806e1c7014b"
    # Empty password → empty NT (no crash).
    assert nt_password_hash("") == ""
    # Hex, uppercase, 32 chars.
    h = nt_password_hash("Secret-123")
    assert len(h) == 32 and h == h.upper()


# ════════════ 2) provision writes MSCHAP-compatible secret ════════════
def test_provision_writes_cleartext_and_nt(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        res = rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        st = rmt.tunnel_radius_status("CCR4", tenant_id=1, reveal_secret=True)
        assert st.exists and st.username == "rtr-CCR4"
        assert st.has_cleartext and st.has_nt
        assert st.mschap_compatible and not st.incompatible_secret
        assert st.cleartext == res.tunnel_password
        # NT-Password must equal MD4 of the cleartext.
        from app.radius.db.repos import freeradius_repo as fr
        rows = {r["attribute"]: r["value"]
                for r in fr.list_user_check(1, "rtr-CCR4")}
        assert rows["NT-Password"] == rmt.nt_password_attr_value(res.tunnel_password)
        assert rows["NT-Password"].startswith("0x")  # FreeRADIUS hex form
        # And it has a fixed Framed-IP → synced.
        assert st.framed_ip and st.synced


# ════════════ 3) ensure is idempotent ════════════
def test_ensure_idempotent_no_ip_or_password_churn(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        first = rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        ip1, pw1 = str(first.tunnel_ip), first.tunnel_password

        # Re-run ensure twice with no explicit password.
        e1 = rmt.ensure_tunnel_radius_user("CCR4", tenant_id=1)
        e2 = rmt.ensure_tunnel_radius_user("rtr-CCR4", tenant_id=1)
        assert not e1.created and not e2.created
        assert not e1.password_changed and not e2.password_changed
        assert str(e1.tunnel_ip) == ip1 == str(e2.tunnel_ip)   # no IP churn
        assert e1.password == pw1 == e2.password               # no pw churn

        # Exactly one Cleartext + one NT row remains (no duplication).
        checks = fr.list_user_check(1, "rtr-CCR4")
        attrs = [r["attribute"] for r in checks]
        assert attrs.count("Cleartext-Password") == 1
        assert attrs.count("NT-Password") == 1
        # Exactly one Framed-IP reply row.
        replies = [r["attribute"] for r in fr.list_user_reply(1, "rtr-CCR4")]
        assert replies.count("Framed-IP-Address") == 1


def test_ensure_creates_fresh_account_with_stable_ip(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        e = rmt.ensure_tunnel_radius_user("BrandNew", tenant_id=1)
        assert e.created and e.password_changed and e.password
        st = rmt.tunnel_radius_status("BrandNew", tenant_id=1)
        assert st.synced and st.framed_ip == str(e.tunnel_ip)


def test_ensure_explicit_password_match_router(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        e = rmt.ensure_tunnel_radius_user(
            "CCR4", tenant_id=1, password="Router-Sent-This-99")
        assert e.password == "Router-Sent-This-99" and e.password_changed
        st = rmt.tunnel_radius_status("CCR4", tenant_id=1, reveal_secret=True)
        assert st.cleartext == "Router-Sent-This-99"
        from app.radius.db.repos import freeradius_repo as fr
        rows = {r["attribute"]: r["value"] for r in fr.list_user_check(1, "rtr-CCR4")}
        assert rows["NT-Password"] == rmt.nt_password_attr_value("Router-Sent-This-99")


def test_ensure_upgrades_legacy_cleartext_only_account(app):
    """A pre-existing account with ONLY Cleartext-Password (the old
    provision path) must gain NT-Password without changing its secret/IP."""
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        # Simulate a legacy account: cleartext only, fixed IP.
        fr.replace_user_check(1, "rtr-Legacy",
                              [("Cleartext-Password", ":=", "oldpass123")])
        fr.replace_user_reply(1, "rtr-Legacy",
                              [("Framed-IP-Address", ":=", "10.50.0.7")])
        before = rmt.tunnel_radius_status("Legacy", tenant_id=1)
        assert before.has_cleartext and not before.has_nt   # legacy gap

        e = rmt.ensure_tunnel_radius_user("Legacy", tenant_id=1)
        assert not e.created and not e.password_changed
        assert e.password == "oldpass123"
        assert str(e.tunnel_ip) == "10.50.0.7"              # IP preserved

        after = rmt.tunnel_radius_status("Legacy", tenant_id=1)
        assert after.has_cleartext and after.has_nt and after.synced


# ════════════ 4) diagnose distinguishes every failure mode ════════════
def test_diagnose_invalid_user(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        d = rmt.diagnose_tunnel_login("Ghost", tenant_id=1)
        assert d["code"] == rmt.DIAG_INVALID_USER and not d["ok"]


def test_diagnose_ok_and_wrong_password(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        res = rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        d_ok = rmt.diagnose_tunnel_login("CCR4", tenant_id=1)
        assert d_ok["code"] == rmt.DIAG_OK and d_ok["ok"]
        # Correct password verifies.
        d_match = rmt.diagnose_tunnel_login(
            "CCR4", tenant_id=1, password=res.tunnel_password)
        assert d_match["ok"]
        # Wrong password is caught.
        d_bad = rmt.diagnose_tunnel_login("CCR4", tenant_id=1, password="nope")
        assert d_bad["code"] == rmt.DIAG_WRONG_PASSWORD and not d_bad["ok"]


def test_diagnose_missing_secret(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        # Account exists but only carries a non-password attribute.
        fr.replace_user_check(1, "rtr-NoPw", [("Simultaneous-Use", ":=", "1")])
        fr.replace_user_reply(1, "rtr-NoPw",
                              [("Framed-IP-Address", ":=", "10.50.0.9")])
        d = rmt.diagnose_tunnel_login("NoPw", tenant_id=1)
        assert d["code"] == rmt.DIAG_MISSING_SECRET


def test_diagnose_mschap_incompatible(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        # An irreversible/non-MSCHAP secret (e.g. bcrypt-style) is set.
        fr.replace_user_check(1, "rtr-Bcrypt",
                              [("Crypt-Password", ":=", "$2b$12$abcdef")])
        fr.replace_user_reply(1, "rtr-Bcrypt",
                              [("Framed-IP-Address", ":=", "10.50.0.10")])
        st = rmt.tunnel_radius_status("Bcrypt", tenant_id=1)
        assert st.incompatible_secret and not st.mschap_compatible
        d = rmt.diagnose_tunnel_login("Bcrypt", tenant_id=1)
        assert d["code"] == rmt.DIAG_MSCHAP_INCOMPATIBLE


def test_diagnose_disabled(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        # Append an Auth-Type := Reject (disabled) alongside the secret.
        existing = fr.list_user_check(1, "rtr-CCR4")
        attrs = [(r["attribute"], r["op"], r["value"]) for r in existing]
        attrs.append(("Auth-Type", ":=", "Reject"))
        fr.replace_user_check(1, "rtr-CCR4", attrs)
        d = rmt.diagnose_tunnel_login("CCR4", tenant_id=1)
        assert d["code"] == rmt.DIAG_DISABLED


def test_diagnose_expired(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        attrs = [(r["attribute"], r["op"], r["value"])
                 for r in fr.list_user_check(1, "rtr-CCR4")]
        attrs.append(("Expiration", ":=", "01 Jan 2020 00:00:00"))
        fr.replace_user_check(1, "rtr-CCR4", attrs)
        d = rmt.diagnose_tunnel_login("CCR4", tenant_id=1)
        assert d["code"] == rmt.DIAG_EXPIRED


def test_diagnose_no_framed_ip(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        fr.replace_user_check(1, "rtr-NoIp",
                              [("Cleartext-Password", ":=", "p"),
                               ("NT-Password", ":=", rmt.nt_password_hash("p"))])
        # No radreply row at all.
        d = rmt.diagnose_tunnel_login("NoIp", tenant_id=1)
        assert d["code"] == rmt.DIAG_NO_FRAMED_IP


# ════════════ 5) RouterOS SSTP block profile fix ════════════
def test_sstp_block_uses_profile_default_not_encryption():
    from app.radius.services.mt_provisioner import render_sstp_mgmt_block
    block = render_sstp_mgmt_block(
        nas_name="ccr4", accel_host="187.77.70.18",
        username="rtr-ccr4", password="pw123", port=443)
    # The ADD directive line must use profile=default, not default-encryption.
    # (The block now also emits a `remove [find name=...]` cleanup line first —
    # idempotent re-paste — so select the add line specifically.)
    cmd = [ln for ln in block.splitlines()
           if ln.startswith("/interface sstp-client add")][0]
    assert "profile=default " in cmd
    assert "default-encryption" not in cmd
    assert "verify-server-certificate=no" in cmd
    assert "/interface sstp-client remove [find name=hr-sstp-mgmt]" in block


def test_pptp_block_keeps_encryption():
    # PPTP is NOT TLS-wrapped — it relies on MPPE, so default-encryption stays.
    from app.radius.services.mt_provisioner import render_pptp_mgmt_block
    block = render_pptp_mgmt_block(
        nas_name="ccr4", accel_host="187.77.70.18",
        username="rtr-ccr4", password="pw123")
    assert "profile=default-encryption" in block


# ════════════ 6) account management primitives ════════════
def test_set_enabled_disabled_preserves_secret(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        res = rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        rmt.set_tunnel_enabled("CCR4", tenant_id=1, enabled=False)
        st = rmt.tunnel_radius_status("CCR4", tenant_id=1, reveal_secret=True)
        assert st.disabled and not st.synced
        assert st.cleartext == res.tunnel_password   # secret kept
        # Re-enable.
        rmt.set_tunnel_enabled("CCR4", tenant_id=1, enabled=True)
        st2 = rmt.tunnel_radius_status("CCR4", tenant_id=1)
        assert not st2.disabled and st2.synced


def test_ensure_does_not_reenable_disabled(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        rmt.set_tunnel_enabled("CCR4", tenant_id=1, enabled=False)
        # A plain re-sync must NOT silently re-enable.
        rmt.ensure_tunnel_radius_user("CCR4", tenant_id=1)
        assert rmt.tunnel_radius_status("CCR4", tenant_id=1).disabled


def test_set_and_clear_expiry(app):
    with app.app_context():
        from datetime import datetime, timezone
        from app.radius.services import router_mgmt_tunnel as rmt
        rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        rmt.set_tunnel_expiry("CCR4", tenant_id=1,
                              expire_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert rmt.tunnel_radius_status("CCR4", tenant_id=1).expired
        rmt.set_tunnel_expiry("CCR4", tenant_id=1, expire_at=None)
        assert not rmt.tunnel_radius_status("CCR4", tenant_id=1).expired


def test_list_tunnel_accounts(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        rmt.provision_tunnel("CCR4", transport="sstp", tenant_id=1)
        rmt.provision_tunnel("CCR5", transport="pptp", tenant_id=1)
        accounts = {a["username"]: a for a in rmt.list_tunnel_accounts(1)}
        assert "rtr-CCR4" in accounts and "rtr-CCR5" in accounts
        # Plaintext password is exposed for the reveal toggle.
        assert accounts["rtr-CCR4"]["password"]
        assert accounts["rtr-CCR4"]["status"]["synced"]


# ════════════ 7) reconcile backfill (the automatic ccr4 fix) ════════════
def test_reconcile_provisions_missing_existing_router(app):
    """Simulate ccr4: a v6 SSTP nas_devices row whose rtr- account is MISSING.
    The boot reconcile must create an MSCHAP-ready account with NO manual SQL."""
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.connection import transaction
        # A nas_devices row exists (router onboarded) but radcheck is empty.
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices (tenant_id, name, address, secret, "
                " vendor, nas_type, enabled, management_tunnel_type, "
                " management_secret_ref, created_at) "
                "VALUES (1,'ccr4','10.50.0.4','s','mikrotik','hotspot',1,"
                " 'sstp_mgmt','rtr-ccr4','2026-01-01T00:00:00Z')")
        assert not rmt.tunnel_radius_status("ccr4", tenant_id=1).exists

        report = rmt.reconcile_tunnel_accounts(1)
        assert "rtr-ccr4" in report.created
        st = rmt.tunnel_radius_status("ccr4", tenant_id=1)
        assert st.exists and st.has_cleartext and st.has_nt and st.synced

        # Idempotent: a second run leaves it untouched (no password churn).
        pw1 = rmt.tunnel_radius_status("ccr4", tenant_id=1, reveal_secret=True).cleartext
        report2 = rmt.reconcile_tunnel_accounts(1)
        assert "rtr-ccr4" in report2.ok and report2.changed == 0
        pw2 = rmt.tunnel_radius_status("ccr4", tenant_id=1, reveal_secret=True).cleartext
        assert pw1 == pw2


def test_reconcile_repairs_incompatible_account(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.connection import transaction
        from app.radius.db.repos import freeradius_repo as fr
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices (tenant_id, name, address, secret, "
                " vendor, nas_type, enabled, management_tunnel_type, "
                " management_secret_ref, created_at) "
                "VALUES (1,'ccr9','10.50.0.9','s','mikrotik','hotspot',1,"
                " 'sstp_mgmt','rtr-ccr9','2026-01-01T00:00:00Z')")
        # Account exists but only with a NON-MSCHAP secret (bcrypt-ish).
        fr.replace_user_check(1, "rtr-ccr9", [("Crypt-Password", ":=", "$2b$x")])
        report = rmt.reconcile_tunnel_accounts(1)
        assert "rtr-ccr9" in report.repaired
        assert rmt.tunnel_radius_status("ccr9", tenant_id=1).synced


# ════════════ 8) WAL checkpoint + pure-MD4 independence ════════════
def test_provision_checkpoints_wal(app, monkeypatch):
    """Every tunnel write must flush the WAL so the FreeRADIUS container's
    SQLite reader sees the rows (the rtr-ccr5 'Invalid user' root cause)."""
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        calls = {"n": 0}
        monkeypatch.setattr(rmt, "checkpoint_wal", lambda: calls.__setitem__("n", calls["n"] + 1) or True)
        rmt.provision_tunnel("CCR5", transport="sstp", tenant_id=1)
        assert calls["n"] >= 1
        rmt.ensure_tunnel_radius_user("CCR5", tenant_id=1)
        rmt.set_tunnel_enabled("CCR5", tenant_id=1, enabled=False)
        rmt.deprovision_tunnel("CCR5", tenant_id=1)
        assert calls["n"] >= 4   # provision + ensure + toggle + deprovision


def test_nt_hash_does_not_depend_on_hashlib_md4(app, monkeypatch):
    """The deployed app container's OpenSSL has md4 disabled. nt_password_hash
    MUST use the pure-Python MD4 and still produce the correct value even when
    hashlib.new('md4') raises."""
    import hashlib
    from app.radius.services import router_mgmt_tunnel as rmt
    orig = hashlib.new
    def boom(name, *a, **k):
        if name.lower() == "md4":
            raise ValueError("unsupported hash type md4")
        return orig(name, *a, **k)
    monkeypatch.setattr(hashlib, "new", boom)
    assert rmt.nt_password_hash("password") == "8846F7EAEE8FB117AD06BDD830B7586C"
    assert rmt.nt_password_attr_value("password") == "0x8846F7EAEE8FB117AD06BDD830B7586C"


def test_wal_checkpoint_real_call(app):
    """checkpoint_wal runs against the real DB without raising."""
    with app.app_context():
        from app.radius.db import connection
        # returns a bool; on a fresh WAL DB it should checkpoint cleanly
        assert connection.checkpoint_wal() in (True, False)


# ════════════ 9) unique tunnel-IP allocation (no 10.50.0.2 collision) ════════════
def test_two_provisions_get_distinct_ips(app):
    """provision_tunnel does not persist to nas_devices (the route does), so
    allocation MUST scan radreply to stay collision-free."""
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        r1 = rmt.provision_tunnel("R1", transport="sstp", tenant_id=1)
        r2 = rmt.provision_tunnel("R2", transport="sstp", tenant_id=1)
        r3 = rmt.provision_tunnel("R3", transport="sstp", tenant_id=1)
        ips = {str(r1.tunnel_ip), str(r2.tunnel_ip), str(r3.tunnel_ip)}
        assert len(ips) == 3, f"IP collision: {ips}"


def test_reconcile_assigns_distinct_ips_without_nas_column(app):
    """The exact ccr4/ccr5 == 10.50.0.2 scenario: two v6 routers whose
    management_remote_address is NOT persisted. reconcile must still give each a
    UNIQUE Framed-IP (allocation scans radreply, not just the NAS column)."""
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.connection import transaction
        with transaction() as c:
            for name in ("ccrA", "ccrB"):
                c.execute(
                    "INSERT INTO nas_devices (tenant_id, name, address, secret, "
                    " vendor, nas_type, enabled, management_tunnel_type, "
                    " management_secret_ref, created_at) "
                    "VALUES (1,?, '', 's','mikrotik','hotspot',1,'sstp_mgmt',?, "
                    " '2026-01-01T00:00:00Z')", (name, "rtr-" + name))
        rmt.reconcile_tunnel_accounts(1)
        a = rmt.tunnel_radius_status("ccrA", tenant_id=1).framed_ip
        b = rmt.tunnel_radius_status("ccrB", tenant_id=1).framed_ip
        assert a and b and a != b, f"tunnel-IP collision: ccrA={a} ccrB={b}"


def test_allocate_excludes_radreply_ips(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        # Pre-seed radreply with .2 and .3 assigned to other rtr- accounts.
        fr.replace_user_reply(1, "rtr-x", [("Framed-IP-Address", ":=", "10.50.0.2")])
        fr.replace_user_reply(1, "rtr-y", [("Framed-IP-Address", ":=", "10.50.0.3")])
        ip = rmt.allocate_tunnel_ip(1)
        assert str(ip) == "10.50.0.4"   # .1 server, .2/.3 taken → .4
