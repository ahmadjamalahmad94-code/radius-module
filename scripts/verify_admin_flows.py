#!/usr/bin/env python
"""Verify the ADMINISTRATION flows actually record to the database.

This script runs DUMMY administration operations inside a real Flask app
context against the dev SQLite DB (tenant 1) and asserts the expected row
inserts/updates across every administration table, then deletes the rows it
created and confirms the table counts return to baseline.

Flows checked:
  (a) CREATE ADMIN              -> +1 admins,  +1 audit_log
  (b) CREATE ROLE              -> +1 roles
  (c) SETTING upsert           -> tenant_settings get/set round-trip
                                  (+1 tenant_settings row for a new key)
  (d) CREATE TENANT            -> +1 tenants,  +1 audit_log
  (e) SOFT-DELETE + RESTORE    -> the dummy admin: deleted_at NULL -> set,
                                  then restored back to NULL (both asserted)

SAFETY:
  - Operates only on the dev DB (instance/hoberadius.db) tenant 1.
  - Every row is tagged with DUMMY-VERIFY in a name / note / username field.
  - All inserted rows are deleted at the end (FK-safe order). Final counts are
    re-printed to prove the baseline is restored.

Usage (from repo root):
    python scripts/verify_admin_flows.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# --- make repo root importable + force dev-safe env BEFORE importing app -----
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")  # no background threads
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")    # do not reseed demo data
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

TENANT_ID = 1
ACTOR = "verify-admin-script"
DUMMY = "DUMMY-VERIFY"

# Tables we watch. Counts are scoped to tenant 1 where the column exists.
WATCH_TABLES = [
    "admins",
    "roles",
    "tenant_settings",
    "tenants",
    "audit_log",
]


def _count(conn, table: str) -> int:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "tenant_id" in cols:
        # roles.tenant_id is nullable (system roles use NULL); count both the
        # tenant-1 rows and the global NULL-tenant rows so a new dummy role
        # (which we insert with tenant_id=1) is captured deterministically.
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE tenant_id = ? OR tenant_id IS NULL", (TENANT_ID,)
        ).fetchone()[0])
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _snapshot(conn) -> dict[str, int]:
    return {t: _count(conn, t) for t in WATCH_TABLES}


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(f"\n{title}")
    for t in WATCH_TABLES:
        print(f"    {t:<35} {counts[t]}")


def _line(flow: str, ok: bool, detail: str) -> str:
    tag = "PASS" if ok else "FAIL"
    return f"[{tag}] {flow:<28} {detail}"


def _deleted_at(conn, table: str, row_id: int):
    row = conn.execute(
        f"SELECT deleted_at FROM {table} WHERE id = ?", (row_id,)
    ).fetchone()
    return row["deleted_at"] if row else "<missing>"


def main() -> int:
    from app import create_app
    app = create_app()

    results: list[str] = []
    created: dict[str, list[int]] = {t: [] for t in WATCH_TABLES}
    # tenant_settings has no `id` PK we track; remember (tenant_id, key) instead.
    created_setting_keys: list[str] = []

    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.core.tenant import Tenant, TENANT_TIER_STARTER
        from app.radius.db.repos import admins_repo, tenants_repo
        from app.radius.services.admins import get_admins_service
        from app.radius.services.tenants import get_tenants_service

        conn = db()
        admins_service = get_admins_service()
        tenants_service = get_tenants_service()

        # Unique suffix so re-runs never collide on unique columns.
        sfx = str(int(time.time()))

        baseline = _snapshot(conn)
        _print_counts("BASELINE COUNTS (tenant 1):", baseline)

        dummy_admin_id: int | None = None
        dummy_role_id: int | None = None
        dummy_tenant_id: int | None = None

        # -------- (a) CREATE ADMIN -------------------------------------------
        before = _snapshot(conn)
        admin = admins_service.create_admin(
            actor=ACTOR,
            username=f"dummy_verify_admin_{sfx}",
            password="dummy-verify-pw",
            full_name=f"{DUMMY} admin",
            email="dummy-verify@example.invalid",
            enabled=True,
        )
        dummy_admin_id = int(admin.id)
        created["admins"].append(dummy_admin_id)
        after = _snapshot(conn)
        d_admin = after["admins"] - before["admins"]
        d_audit = after["audit_log"] - before["audit_log"]
        # The new audit_log row id (newest for this admin target) — tracked for
        # cleanup. Service swallows audit errors, so guard for None.
        audit_row = conn.execute(
            "SELECT id FROM audit_log WHERE tenant_id = ? AND target_type = 'admin' "
            "AND target_id = ? ORDER BY id DESC LIMIT 1",
            (TENANT_ID, str(dummy_admin_id)),
        ).fetchone()
        if audit_row:
            created["audit_log"].append(int(audit_row["id"]))
        ok_a = (d_admin == 1 and d_audit == 1)
        results.append(_line(
            "(a) create admin", ok_a,
            f"admins +{d_admin}, audit_log +{d_audit} "
            f"| admin.id={admin.id} username={admin.username!r} role_id={admin.role_id}",
        ))

        # -------- (b) CREATE ROLE --------------------------------------------
        before = _snapshot(conn)
        role = admins_repo.create_role(
            name=f"dummy_verify_role_{sfx}",
            display_name=f"{DUMMY} role",
            description=f"{DUMMY} created by verify script",
            permissions=("subscribers.read",),
            tenant_id=TENANT_ID,
        )
        dummy_role_id = int(role.id)
        created["roles"].append(dummy_role_id)
        after = _snapshot(conn)
        d_role = after["roles"] - before["roles"]
        ok_b = (d_role == 1)
        results.append(_line(
            "(b) create role", ok_b,
            f"roles +{d_role} "
            f"| role.id={role.id} name={role.name!r} perms={list(role.permissions)}",
        ))

        # -------- (c) SETTING upsert (round-trip) ----------------------------
        before = _snapshot(conn)
        setting_key = f"dummy_verify_setting_{sfx}"
        setting_val = f"{DUMMY}-value-1"
        tenants_repo.set_setting(TENANT_ID, setting_key, setting_val, by=0)
        created_setting_keys.append(setting_key)
        read_back_1 = tenants_repo.get_setting(TENANT_ID, setting_key)
        # second upsert proves UPDATE path (no extra row)
        tenants_repo.set_setting(TENANT_ID, setting_key, f"{DUMMY}-value-2", by=0)
        read_back_2 = tenants_repo.get_setting(TENANT_ID, setting_key)
        after = _snapshot(conn)
        d_set = after["tenant_settings"] - before["tenant_settings"]
        ok_c = (d_set == 1 and read_back_1 == setting_val
                and read_back_2 == f"{DUMMY}-value-2")
        results.append(_line(
            "(c) setting upsert", ok_c,
            f"tenant_settings +{d_set} "
            f"| key={setting_key!r} set='{setting_val}' read='{read_back_1}' "
            f"-> updated read='{read_back_2}'",
        ))

        # -------- (d) CREATE TENANT ------------------------------------------
        before = _snapshot(conn)
        tenant = tenants_service.create(
            actor=ACTOR,
            tenant=Tenant(
                id=None,
                slug=f"dummy-verify-tenant-{sfx}",
                name=f"{DUMMY} tenant",
                display_name=f"{DUMMY} tenant",
                email="dummy-verify-tenant@example.invalid",
                plan_tier=TENANT_TIER_STARTER,
            ),
        )
        dummy_tenant_id = int(tenant.id)
        created["tenants"].append(dummy_tenant_id)
        after = _snapshot(conn)
        d_tenant = after["tenants"] - before["tenants"]
        d_audit_t = after["audit_log"] - before["audit_log"]
        audit_row_t = conn.execute(
            "SELECT id FROM audit_log WHERE tenant_id = ? AND target_type = 'tenant' "
            "AND target_id = ? ORDER BY id DESC LIMIT 1",
            (TENANT_ID, str(dummy_tenant_id)),
        ).fetchone()
        if audit_row_t:
            created["audit_log"].append(int(audit_row_t["id"]))
        ok_d = (d_tenant == 1 and d_audit_t == 1)
        results.append(_line(
            "(d) create tenant", ok_d,
            f"tenants +{d_tenant}, audit_log +{d_audit_t} "
            f"| tenant.id={tenant.id} slug={tenant.slug!r} tier={tenant.plan_tier}",
        ))

        # -------- (e) SOFT-DELETE + RESTORE (the dummy admin) ----------------
        # Drive the same paths the recycle-bin UI uses:
        #   archive_admin -> deleted_at set
        #   restore_admin -> deleted_at back to NULL
        before_del = _deleted_at(conn, "admins", dummy_admin_id)
        archived = admins_repo.archive_admin(
            dummy_admin_id, actor=ACTOR, reason=f"{DUMMY} soft-delete")
        after_del = _deleted_at(conn, "admins", dummy_admin_id)
        restored = admins_repo.restore_admin(dummy_admin_id, actor=ACTOR)
        after_restore = _deleted_at(conn, "admins", dummy_admin_id)
        ok_e = (
            archived is True and before_del is None and after_del is not None
            and restored is True and after_restore is None
        )
        results.append(_line(
            "(e) soft-delete + restore", ok_e,
            f"admin.id={dummy_admin_id} "
            f"deleted_at: {before_del!r} -> {after_del!r} -> {after_restore!r} "
            f"(archived={archived}, restored={restored})",
        ))

        # ----------------------- RESULTS -------------------------------------
        print("\n" + "=" * 78)
        print("FLOW RESULTS")
        print("=" * 78)
        for line in results:
            print("  " + line)
        all_ok = all(line.startswith("[PASS]") for line in results)
        print("-" * 78)
        print(f"  OVERALL: {'ALL FLOWS PASS' if all_ok else 'SOME FLOWS FAILED'}")
        print("=" * 78)

        post_run = _snapshot(conn)
        _print_counts("COUNTS AFTER DUMMY OPERATIONS (tenant 1):", post_run)

        # ----------------------- CLEANUP -------------------------------------
        # FK-safe deletion order:
        #   audit_log     -> independent (no FK pointing in)
        #   admins        -> role_id is ON DELETE SET NULL, so order vs roles
        #                    is safe either way; delete admins before roles.
        #   roles         -> after admins
        #   tenant_settings (by tenant_id+key)
        #   tenants       -> last (nothing dummy references it)
        print("\nCLEANUP — deleting dummy rows the script created...")
        deleted: dict[str, int] = {t: 0 for t in WATCH_TABLES}
        deleted_settings = 0
        try:
            with transaction() as txn:
                for table in ("audit_log", "admins", "roles", "tenants"):
                    for row_id in created.get(table) or []:
                        cur = txn.execute(
                            f"DELETE FROM {table} WHERE id = ?", (row_id,)
                        )
                        deleted[table] += cur.rowcount
                for key in created_setting_keys:
                    cur = txn.execute(
                        "DELETE FROM tenant_settings WHERE tenant_id = ? AND key = ?",
                        (TENANT_ID, key),
                    )
                    deleted_settings += cur.rowcount
            for table in WATCH_TABLES:
                if deleted[table]:
                    print(f"    deleted {deleted[table]} from {table} "
                          f"(ids={created[table]})")
            if deleted_settings:
                print(f"    deleted {deleted_settings} from tenant_settings "
                      f"(keys={created_setting_keys})")
            cleanup_ok = True
        except Exception as exc:  # noqa: BLE001
            cleanup_ok = False
            print(f"    CLEANUP ERROR: {exc!r}")
            print("    Dummy rows that may remain (delete manually by id/key):")
            for table in WATCH_TABLES:
                if created[table]:
                    print(f"      {table}: {created[table]}")
            if created_setting_keys:
                print(f"      tenant_settings keys (tenant {TENANT_ID}): "
                      f"{created_setting_keys}")

        final = _snapshot(conn)
        _print_counts("FINAL COUNTS AFTER CLEANUP (tenant 1):", final)

        restored_baseline = (final == baseline)
        print("\n" + "=" * 78)
        if restored_baseline:
            print("  CLEANUP: baseline RESTORED — all dummy rows removed.")
        else:
            print("  CLEANUP: baseline NOT fully restored — diff vs baseline:")
            for t in WATCH_TABLES:
                if final[t] != baseline[t]:
                    print(f"      {t}: baseline={baseline[t]} final={final[t]}")
        print("=" * 78)

    return 0 if (all_ok and cleanup_ok and restored_baseline) else 1


if __name__ == "__main__":
    raise SystemExit(main())
