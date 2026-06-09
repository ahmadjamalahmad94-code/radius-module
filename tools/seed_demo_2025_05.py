# -*- coding: utf-8 -*-
"""بيانات تجريبية لشهر 2025-05 (وسنة 2025) — فقط لتجربة منتقي الفترة
في صفحة «نظرة عامة — المشتركون». كل الصفوف موسومة demo-seed-2025
ويمكن حذفها لاحقًا بـ:  python tools/seed_demo_2025_05.py --clean
"""
import os
import random
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "instance", "hoberadius.db")
TAG = "demo-seed-2025"
TENANT = 1

def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    if "--clean" in sys.argv:
        n1 = cur.execute("DELETE FROM accounting_ledger_entries WHERE operator = ?", (TAG,)).rowcount
        n2 = cur.execute("DELETE FROM loan_entries WHERE created_by = ?", (TAG,)).rowcount
        n3 = cur.execute("DELETE FROM payment_transactions WHERE created_by = ?", (TAG,)).rowcount
        n4 = cur.execute("DELETE FROM bandwidth_usage_daily WHERE peak_mbps = -2025", ()).rowcount
        con.commit()
        print(f"cleaned: ledger={n1} loans={n2} payments={n3} bw={n4}")
        return

    subs = cur.execute(
        "SELECT id, username FROM subscribers WHERE tenant_id = ? LIMIT 10", (TENANT,)
    ).fetchall()
    if not subs:
        print("ERROR: no subscribers found")
        return

    rng = random.Random(2025)
    months = ["2025-02", "2025-03", "2025-05"]  # شهر 5 + شهرين إضافيين لشكل أغنى
    n_pay = n_loan = n_bw = 0

    for month in months:
        for i in range(rng.randint(6, 12)):
            sid, uname = subs[rng.randrange(len(subs))]
            day = rng.randint(1, 28)
            ts = f"{month}-{day:02d}T{rng.randint(8, 21):02d}:{rng.randint(0, 59):02d}:00.000000Z"
            amount = rng.choice([1.5, 3.0, 4.5, 6.0, 9.0])
            minutes = int(amount / 1.5) * 1440

            cur.execute(
                """INSERT INTO accounting_ledger_entries
                   (tenant_id, entry_type, direction, amount, currency, subscriber_id, username,
                    admin_id, operator, source_type, source_id, related_type, status, notes,
                    metadata_json, created_at)
                   VALUES (?, 'payment', 'credit', ?, 'JOD', ?, ?, 0, ?, 'payment', 0, '',
                           'posted', 'demo 2025', '{"demo": true}', ?)""",
                (TENANT, amount, sid, uname, TAG, ts),
            )
            cur.execute(
                """INSERT INTO payment_transactions
                   (tenant_id, subscriber_id, username, plan_id, amount, currency, method, status,
                    plan_price, discount_amount, discount_reason, effective_price, earned_minutes,
                    rounding_mode, created_by, notes, metadata_json, created_at)
                   VALUES (?, ?, ?, NULL, ?, 'JOD', 'cash', 'posted', ?, 0, '', ?, ?, 'floor',
                           ?, 'demo 2025', '{"demo": true}', ?)""",
                (TENANT, sid, uname, amount, amount, amount, minutes, TAG, ts),
            )
            n_pay += 1

        for i in range(rng.randint(2, 4)):
            sid, uname = subs[rng.randrange(len(subs))]
            day = rng.randint(1, 28)
            ts = f"{month}-{day:02d}T12:00:00.000000Z"
            cur.execute(
                """INSERT INTO loan_entries
                   (tenant_id, subscriber_id, username, duration_minutes, amount, currency, reason,
                    status, approval_status, starts_at, ends_at, max_limit_snapshot, created_by,
                    metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, 'JOD', 'demo 2025', 'settled', 'not_required',
                           ?, ?, 1440, ?, '{"demo": true}', ?)""",
                (TENANT, sid, uname, rng.choice([1440, 2880]), rng.choice([1.5, 3.0]),
                 ts, ts, TAG, ts),
            )
            n_loan += 1

        for i in range(rng.randint(8, 14)):
            sid, _ = subs[rng.randrange(len(subs))]
            day = f"{month}-{rng.randint(1, 28):02d}"
            cur.execute(
                """INSERT INTO bandwidth_usage_daily
                   (tenant_id, subscriber_id, day, bytes_in, bytes_out, sessions_count, peak_mbps)
                   VALUES (?, ?, ?, ?, ?, ?, -2025)""",
                (TENANT, sid, day,
                 rng.randint(1, 18) * 1024**3, rng.randint(1, 6) * 1024**3, rng.randint(1, 9)),
            )
            n_bw += 1

    con.commit()
    con.close()
    print(f"OK: seeded months {months} → payments={n_pay} loans={n_loan} bw-days={n_bw}")
    print("افتح: /admin/radius/subscribers/overview?period=monthly ثم جرّب منتقي الشهر،")
    print("و: /admin/radius/subscribers/overview?period=yearly لاختيار 2025/2026.")

if __name__ == "__main__":
    main()
