#!/usr/bin/env python
"""Verify the FINANCE / COLLECTION flows actually record to the database.

This script runs DUMMY transactions inside a real Flask app context against the
dev SQLite DB (tenant 1) and asserts the expected row inserts across every
finance table, then deletes the rows it created and confirms the table counts
return to baseline.

Flows checked:
  (a) Subscriber PAYMENT            -> +1 payment_transactions, +1 accounting_ledger_entries
  (b) LOAN issue                    -> +1 loan_entries,         +1 accounting_ledger_entries
  (c) LOAN settle                   -> +1 settlement_entries,   +1 accounting_ledger_entries
  (d) Payment COLLECTION request    -> +1 payment_requests
      then approve / apply path     -> +1 payment_collection_transactions,
                                       +1 payment_proofs, +1 accounting_ledger_entries

SAFETY:
  - Operates only on the dev DB (instance/hoberadius.db) tenant 1.
  - Every row is tagged with DUMMY-VERIFY in its note / reference field.
  - All inserted rows are deleted at the end (FK-safe order). Final counts are
    re-printed to prove the baseline is restored.

Usage (from repo root):
    python scripts/verify_finance_flows.py
"""
from __future__ import annotations

import os
import sys
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
ACTOR = "verify-finance-script"
DUMMY = "DUMMY-VERIFY"

# Tables we watch. Counts are scoped to tenant 1 where the column exists.
WATCH_TABLES = [
    "payment_transactions",
    "accounting_ledger_entries",
    "loan_entries",
    "settlement_entries",
    "payment_requests",
    "payment_proofs",
    "payment_collection_transactions",
]


def _count(conn, table: str) -> int:
    # payment_proofs / payment_collection_transactions have no tenant_id column.
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "tenant_id" in cols:
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE tenant_id = ?", (TENANT_ID,)
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


def _resolve_dummy_subscriber(conn) -> dict:
    """Reuse an existing seeded subscriber; create one only if none exists."""
    row = conn.execute(
        "SELECT id, username, plan_id, custom_price FROM subscribers "
        "WHERE tenant_id = ? AND deleted_at IS NULL ORDER BY id LIMIT 1",
        (TENANT_ID,),
    ).fetchone()
    if row:
        return {"id": row[0], "username": row[1], "plan_id": row[2],
                "created": False}
    # No subscriber seeded — create a tagged dummy one.
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    sub = subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=TENANT_ID,
        username="dummy_verify_subscriber",
        password="dummy-verify-pw",
        full_name=DUMMY,
    ))
    return {"id": int(sub.id), "username": sub.username, "plan_id": None,
            "created": True}


def main() -> int:
    from app import create_app
    app = create_app()

    results: list[str] = []
    created: dict[str, list[int]] = {t: [] for t in WATCH_TABLES}
    sub_created_id: int | None = None

    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services.accounting import AccountingService
        from app.radius.db.repos.payments_repo import (
            PaymentRequestRepository,
            PaymentProofRepository,
            PaymentTransactionRepository,
            PaymentCollectionLedgerRepository,
            PaymentSettingsRepository,
        )

        conn = db()
        service = AccountingService(TENANT_ID)

        subscriber = _resolve_dummy_subscriber(conn)
        if subscriber.get("created"):
            sub_created_id = subscriber["id"]
        print(f"Using subscriber: id={subscriber['id']} "
              f"username={subscriber['username']!r} "
              f"plan_id={subscriber['plan_id']}")

        baseline = _snapshot(conn)
        _print_counts("BASELINE COUNTS (tenant 1):", baseline)

        # -------- (a) Subscriber PAYMENT -------------------------------------
        before = _snapshot(conn)
        payment = service.create_payment(
            {
                "subscriber_id": subscriber["id"],
                "amount": 1.0,
                "method": "cash",
                "notes": DUMMY + " subscriber payment",
            },
            actor=ACTOR,
        )
        created["payment_transactions"].append(int(payment["id"]))
        if payment.get("ledger_entry_id"):
            created["accounting_ledger_entries"].append(int(payment["ledger_entry_id"]))
        after = _snapshot(conn)
        d_pt = after["payment_transactions"] - before["payment_transactions"]
        d_le = after["accounting_ledger_entries"] - before["accounting_ledger_entries"]
        ok_a = (d_pt == 1 and d_le == 1)
        results.append(_line(
            "(a) subscriber payment", ok_a,
            f"payment_transactions +{d_pt}, accounting_ledger_entries +{d_le} "
            f"| payment.id={payment['id']} ledger_id={payment.get('ledger_entry_id')} "
            f"amount={payment.get('amount')}",
        ))

        # -------- (b) LOAN issue ---------------------------------------------
        before = _snapshot(conn)
        loan = service.create_loan(
            {
                "subscriber_id": subscriber["id"],
                "amount": 1.0,
                "hours": 1,
                "reason": DUMMY + " loan issue",
            },
            actor=ACTOR,
        )
        created["loan_entries"].append(int(loan["id"]))
        if loan.get("ledger_entry_id"):
            created["accounting_ledger_entries"].append(int(loan["ledger_entry_id"]))
        after = _snapshot(conn)
        d_loan = after["loan_entries"] - before["loan_entries"]
        d_le = after["accounting_ledger_entries"] - before["accounting_ledger_entries"]
        ok_b = (d_loan == 1 and d_le == 1)
        results.append(_line(
            "(b) loan issue", ok_b,
            f"loan_entries +{d_loan}, accounting_ledger_entries +{d_le} "
            f"| loan.id={loan['id']} ledger_id={loan.get('ledger_entry_id')} "
            f"duration_minutes={loan.get('duration_minutes')}",
        ))

        # -------- (c) LOAN settle --------------------------------------------
        before = _snapshot(conn)
        settlement = service.settle_loan(
            int(loan["id"]),
            {
                "amount": 1.0,
                "method": "manual",
                "notes": DUMMY + " loan settle",
            },
            actor=ACTOR,
        )
        created["settlement_entries"].append(int(settlement["id"]))
        if settlement.get("ledger_entry_id"):
            created["accounting_ledger_entries"].append(int(settlement["ledger_entry_id"]))
        after = _snapshot(conn)
        d_st = after["settlement_entries"] - before["settlement_entries"]
        d_le = after["accounting_ledger_entries"] - before["accounting_ledger_entries"]
        ok_c = (d_st == 1 and d_le == 1)
        results.append(_line(
            "(c) loan settle", ok_c,
            f"settlement_entries +{d_st}, accounting_ledger_entries +{d_le} "
            f"| settlement.id={settlement['id']} ledger_id={settlement.get('ledger_entry_id')}",
        ))

        # -------- (d) Payment COLLECTION request create + approve/apply ------
        # The ledger apply path attaches a subscriber only when payer_type is
        # 'subscriber' AND payer_id resolves to a live subscriber row.
        before = _snapshot(conn)
        req = PaymentRequestRepository().create(
            tenant_id=TENANT_ID,
            payer_type="subscriber",
            payer_id=subscriber["id"],
            purpose="subscriber_renewal",
            amount=1.0,
            currency="JOD",
            provider="manual_wallet",
            receiver_wallet="0000000000",
            ttl_minutes=1440,
        )
        created["payment_requests"].append(int(req["id"]))
        after_req = _snapshot(conn)
        d_req = after_req["payment_requests"] - before["payment_requests"]

        # Approve / apply path, mirroring api/v1/payments.py:payment_collection_approve
        proof = PaymentProofRepository().create(
            payment_request_id=int(req["id"]),
            proof_type="manual_reference",
            reference_number=DUMMY,
            note=DUMMY + " collection proof",
        )
        created["payment_proofs"].append(int(proof["id"]))
        PaymentProofRepository().mark_reviewed(
            proof_id=int(proof["id"]),
            reviewed_by=None,
            review_status="approved",
            review_note=DUMMY,
        )
        PaymentRequestRepository().update_status(TENANT_ID, int(req["id"]), "paid")
        coll_tx = PaymentTransactionRepository().create(
            payment_request_id=int(req["id"]),
            amount=req["amount"],
            currency=req["currency"],
            status="paid_manual",
            provider_transaction_id=None,
            raw_payload={"proof_id": proof["id"], "review": "approved_manual", "tag": DUMMY},
        )
        created["payment_collection_transactions"].append(int(coll_tx["id"]))
        ledger_entry = PaymentCollectionLedgerRepository().apply_paid_request(
            tenant_id=TENANT_ID,
            request_id=int(req["id"]),
            actor=ACTOR,
        )
        if ledger_entry.get("id"):
            created["accounting_ledger_entries"].append(int(ledger_entry["id"]))
        after = _snapshot(conn)
        d_ct = after["payment_collection_transactions"] - before["payment_collection_transactions"]
        d_pr = after["payment_proofs"] - before["payment_proofs"]
        d_le = after["accounting_ledger_entries"] - before["accounting_ledger_entries"]
        ok_d = (d_req == 1 and d_ct == 1 and d_le == 1 and d_pr == 1)
        results.append(_line(
            "(d) collection req+approve", ok_d,
            f"payment_requests +{d_req}, payment_collection_transactions +{d_ct}, "
            f"payment_proofs +{d_pr}, accounting_ledger_entries +{d_le} "
            f"| request.id={req['id']} ref={req.get('reference_code')} "
            f"tx.id={coll_tx['id']} ledger_id={ledger_entry.get('id')}",
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
        _print_counts("COUNTS AFTER DUMMY TRANSACTIONS (tenant 1):", post_run)

        # ----------------------- CLEANUP -------------------------------------
        # FK-safe deletion order:
        #   payment_transactions  -> ledger (FK ledger_entry_id)
        #   settlement_entries    -> loan_entries (FK loan_id) + ledger
        #   loan_entries          -> ledger
        #   payment_proofs / payment_collection_transactions -> payment_requests
        #   payment_requests last
        #   accounting_ledger_entries last (everything else points at it)
        print("\nCLEANUP — deleting dummy rows the script created...")
        deleted: dict[str, int] = {t: 0 for t in WATCH_TABLES}
        try:
            with transaction() as txn:
                for table in (
                    "payment_transactions",
                    "settlement_entries",
                    "loan_entries",
                    "payment_proofs",
                    "payment_collection_transactions",
                    "payment_requests",
                    "accounting_ledger_entries",
                ):
                    ids = created.get(table) or []
                    for row_id in ids:
                        cur = txn.execute(
                            f"DELETE FROM {table} WHERE id = ?", (row_id,)
                        )
                        deleted[table] += cur.rowcount
                if sub_created_id is not None:
                    # Only remove a subscriber if THIS run created it.
                    txn.execute(
                        "DELETE FROM subscribers WHERE id = ? AND tenant_id = ?",
                        (sub_created_id, TENANT_ID),
                    )
            for table in WATCH_TABLES:
                if deleted[table]:
                    print(f"    deleted {deleted[table]} from {table} "
                          f"(ids={created[table]})")
            cleanup_ok = True
        except Exception as exc:  # noqa: BLE001
            cleanup_ok = False
            print(f"    CLEANUP ERROR: {exc!r}")
            print("    Dummy rows that may remain (delete manually by id):")
            for table in WATCH_TABLES:
                if created[table]:
                    print(f"      {table}: {created[table]}")
            if sub_created_id is not None:
                print(f"      subscribers: [{sub_created_id}]")

        final = _snapshot(conn)
        _print_counts("FINAL COUNTS AFTER CLEANUP (tenant 1):", final)

        restored = (final == baseline)
        print("\n" + "=" * 78)
        if restored:
            print("  CLEANUP: baseline RESTORED — all dummy rows removed.")
        else:
            print("  CLEANUP: baseline NOT fully restored — diff vs baseline:")
            for t in WATCH_TABLES:
                if final[t] != baseline[t]:
                    print(f"      {t}: baseline={baseline[t]} final={final[t]}")
        print("=" * 78)

    return 0 if (all_ok and cleanup_ok and restored) else 1


if __name__ == "__main__":
    raise SystemExit(main())
