"""Append-only accounting repository.

The ledger path intentionally has no delete helper. Voids and corrections are
stored as new rows so reports can reconstruct history.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ...core.errors import RadiusConflict, RadiusValidationError
from ...core.numbers import finite_float, round_money
from ...core.system_config import default_currency
from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, row_to_dict

# المبلغ المُسدَّد فعلًا من سلفة = مجموع قيود تسويتها المُرحَّلة. التسوية الجزئيّة
# تُبقي السلفة «open» والمتبقّي = amount − هذا المجموع (لا عمود إضافيّ يتزامن).
_LOAN_SETTLED_SQL = (
    "(SELECT COALESCE(SUM(s.amount), 0) FROM settlement_entries s "
    "WHERE s.tenant_id = loan_entries.tenant_id AND s.loan_id = loan_entries.id "
    "AND s.status = 'posted')"
)
# هامش التقريب (قرشان نصفيّان) عند مقارنة المبالغ العشريّة.
_MONEY_EPS = 0.005

# صفوف «الدفعات» في التقارير: الدفعة المُرحَّلة + قيد إلغائها (بمبلغٍ سالب)، فتصفو
# المجاميع (+X ثمّ −X). **العدّ** (عدد العمليات/التفعيلات/المتوسّط) يستثني الدفعة
# المُلغاة وقيدَ إلغائها معًا — كانت دفعةٌ وإلغاؤها «عمليّتين» (r09 F16/N11).
# ``l.id NOT IN (…)`` استعلامٌ غير مرتبط يُقيَّم مرّةً واحدة (لا فهرس على
# reversal_of_entry_id، فـ EXISTS المرتبط لكلّ صفّ يمسح الدفتر كلّه).
_PAYMENT_ROWS_SQL = """(
            (l.entry_type = 'payment' AND l.status = 'posted')
            OR (l.entry_type IN ('void', 'reversal', 'correction') AND orig.entry_type = 'payment')
          )"""
_COUNTED_PAYMENT_SQL = (
    "(l.entry_type = 'payment' AND l.id NOT IN (SELECT r.reversal_of_entry_id "
    "FROM accounting_ledger_entries r WHERE r.reversal_of_entry_id IS NOT NULL))"
)
_CURRENCY_SQL = "UPPER(COALESCE({col}, ''))"


def _currency_code(value: Any) -> str:
    """عملة الصفّ المخزّنة (صفٌّ قديم بلا عملة = عملة النظام)."""
    return str(value or "").strip().upper() or default_currency()


def _range_sql(column: str, utc_from: str = "", utc_to: str = "") -> tuple[str, list[Any]]:
    """حدّا فترة بطوابع UTC ‎[from, to)‎ مقارَنَين بعد التطبيع (``datetime()``
    يقبل ‎…T…Z‎ و«المسافة» معًا) — لا مقارنة نصّيّة ولا ``substr`` لليوم UTC."""
    sql, vals = "", []
    if utc_from:
        sql += f" AND datetime({column}) >= datetime(?)"
        vals.append(utc_from)
    if utc_to:
        sql += f" AND datetime({column}) < datetime(?)"
        vals.append(utc_to)
    return sql, vals


def _rscope(tenant_id: int, column: str = "l.subscriber_id", by: str = "id") -> tuple[str, list]:
    """fix3 (F02 H2 / F01 F7 / F08 H3): the REQUEST admin's subscriber scope on a
    subscriber-linked column — ``("", [])`` for the owner / co-owner / «عرض كل
    المشتركين» / unbound credentials / background jobs. One predicate
    (``services/subscriber_scope``) for every money list and report, web + API."""
    from ...services.subscriber_scope import scope_sql
    return scope_sql(column, by=by, tenant_id=int(tenant_id))


def _rscope_on() -> bool:
    """Is the request admin scoped (not seeing every subscriber)?"""
    from ...services.subscriber_scope import current_scope_admin_id
    return current_scope_admin_id() is not None


def _merge_by_currency(rows, sum_keys: tuple[str, ...]) -> list[dict]:
    """صفوفٌ مجمّعة بعملة (قد تتكرّر العملة الفارغة/الصغيرة) → قائمة by_currency
    مرتّبة: عملة النظام أوّلًا ثمّ الأكبر."""
    merged: dict[str, dict] = {}
    for r in rows:
        cur = _currency_code(r["currency"])
        slot = merged.setdefault(cur, {"currency": cur, **{k: 0 for k in sum_keys}})
        for k in sum_keys:
            slot[k] += r[k] or 0
    out = []
    for slot in merged.values():
        for k in sum_keys:
            v = slot[k]
            slot[k] = int(v) if isinstance(v, int) else round(float(v), 2)
        out.append(slot)
    system = default_currency()
    out.sort(key=lambda s: (s["currency"] != system, -abs(float(s[sum_keys[0]] or 0))))
    return out


def resolve_subscriber(tenant_id: int, *, subscriber_id: int | None = None,
                       username: str = "") -> Optional[dict]:
    if subscriber_id:
        row = db().execute(
            "SELECT * FROM subscribers WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
            (tenant_id, subscriber_id),
        ).fetchone()
    else:
        row = db().execute(
            "SELECT * FROM subscribers WHERE tenant_id = ? AND username = ? AND deleted_at IS NULL",
            (tenant_id, username),
        ).fetchone()
    return row_to_dict(row) if row else None


def resolve_plan(tenant_id: int, plan_id: int | None) -> Optional[dict]:
    if not plan_id:
        return None
    row = db().execute(
        "SELECT * FROM access_plans WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (tenant_id, plan_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def create_ledger_entry(conn, *, tenant_id: int, entry_type: str, amount: float,
                        direction: str = "credit", currency: str = "",
                        subscriber_id: int | None = None, username: str = "",
                        admin_id: int = 0, operator: str = "",
                        source_type: str = "", source_id: int | None = None,
                        related_type: str = "", related_id: int | None = None,
                        reversal_of_entry_id: int | None = None,
                        status: str = "posted", notes: str = "",
                        metadata: dict[str, Any] | None = None) -> int:
    currency = currency or default_currency()
    # المال يُكتب بخانتين (لا ضجيج فاصلة عائمة)؛ Infinity/NaN مرفوضة هنا
    # صراحةً (NaN كان يُكتب NULL فيُسقط NOT NULL بعد حفظ نصف الإجراء).
    amount = round_money(finite_float(amount, field="amount"))
    cur = conn.execute(
        """
        INSERT INTO accounting_ledger_entries(
            tenant_id, entry_type, direction, amount, currency,
            subscriber_id, username, admin_id, operator,
            source_type, source_id, related_type, related_id,
            reversal_of_entry_id, status, notes, metadata_json, created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            tenant_id, entry_type, direction, amount, currency,
            subscriber_id, username, admin_id, operator,
            source_type, source_id, related_type, related_id,
            reversal_of_entry_id, status, notes, json_dump(metadata or {}),
            now_iso(),
        ),
    )
    entry_id = cur.lastrowid
    _emit_ledger_event(
        conn, tenant_id=tenant_id, entry_id=entry_id, entry_type=entry_type,
        amount=amount, direction=direction, currency=currency,
        subscriber_id=subscriber_id, username=username, admin_id=admin_id,
        operator=operator, source_type=source_type, source_id=source_id,
        reversal_of_entry_id=reversal_of_entry_id,
    )
    return entry_id


# ── كلّ قيد ماليّ يظهر في مركز الأحداث ──────────────────────────────────
# الدفعات/السلف/التسويات/الإلغاءات لم تكن تظهر في مركز الأحداث ولا في /events
# إطلاقًا، رغم أنّ الويب يملك تسميات «ledger.*» جاهزة (event_labels). كلّ
# الكتابات الماليّة تمرّ بـ create_ledger_entry، فنُصدر هنا حدثًا واحدًا
# category=financial في **نفس الاتّصال/المعاملة** (ذرّيّ مع القيد: لا حدث بلا
# قيد ولا قيد مُلغًى يترك حدثًا). الهدف = المشترك (target_type=subscriber) كي
# يظهر في خطّه الزمنيّ بمركز الأحداث. فشل الإدراج لا يُسقط القيد أبدًا.
_LEDGER_EVENT_MESSAGES = {
    "payment": "دفعة",
    "time_extension": "تمديد وقت مدفوع",
    "loan": "سلفة",
    "debt": "دين",
    "settlement": "تسوية سلفة",
    "debt_settlement": "تسديد دين",
    "writeoff": "إعفاء من سلفة",
    "void": "إلغاء قيد",
    "cash_balance": "حركة رصيد نقديّ",
    "quota_topup": "شحن كوتا",
    "on_account_credit": "رصيد مقدَّم",
}


def ledger_event_message(entry_type: str, amount: Any, currency: str = "",
                         username: str = "") -> str:
    """نصّ حدث القيد: «إلغاء قيد: -1,000,000,000.00 ILS — r03_u020».

    المبلغ بمنزلتين وفواصل آلاف دائمًا — كان ``{amt:g}`` يطبع ‎-1e+09‎."""
    label = _LEDGER_EVENT_MESSAGES.get(entry_type, entry_type)
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        amt = 0.0
    who = f" — {username}" if username else ""
    return f"{label}: {amt:,.2f} {currency or ''}".rstrip() + who


def _emit_ledger_event(conn, *, tenant_id: int, entry_id: int, entry_type: str,
                       amount: float, direction: str, currency: str,
                       subscriber_id: int | None, username: str, admin_id: int,
                       operator: str, source_type: str, source_id: int | None,
                       reversal_of_entry_id: int | None) -> None:
    import sqlite3

    op = str(operator or "")
    if admin_id:
        actor_type, actor_id = "admin", int(admin_id)
    elif op.startswith("api-token:"):
        tok = op.split(":", 1)[1]
        actor_type, actor_id = "api_token", (int(tok) if tok.isdigit() else None)
    elif op:
        actor_type, actor_id = "admin", None
    else:
        actor_type, actor_id = "system", None
    key = "ledger.void" if (entry_type == "void" or reversal_of_entry_id) else f"ledger.{entry_type}"
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        amt = 0.0
    metadata = {
        "ledger_entry_id": entry_id, "entry_type": entry_type,
        "amount": amt, "direction": direction, "currency": currency,
        "username": username, "operator": op,
        "source_type": source_type, "source_id": source_id,
        "reversal_of_entry_id": reversal_of_entry_id,
    }
    try:
        conn.execute(
            """
            INSERT INTO business_events (
              tenant_id, category, severity, actor_type, actor_id, target_type,
              target_id, event_key, message, metadata_json, correlation_id,
              created_at
            ) VALUES (?, 'financial', 'info', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id, actor_type, actor_id,
                "subscriber" if subscriber_id else "ledger_entry",
                int(subscriber_id) if subscriber_id else int(entry_id),
                key, ledger_event_message(entry_type, amt, currency, username),
                json_dump(metadata), f"ledger:{entry_id}", now_iso(),
            ),
        )
    except sqlite3.Error:  # جدول غائب في قاعدة قديمة/اختبار — القيد أهمّ
        pass


def list_ledger_entries(tenant_id: int, *, entry_type: str = "",
                        subscriber_id: int | None = None, limit: int = 100,
                        offset: int = 0) -> list[dict]:
    sc, sv = _rscope(tenant_id, "subscriber_id")
    sql = "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ?" + sc
    vals: list[Any] = [tenant_id, *sv]
    if entry_type:
        sql += " AND entry_type = ?"
        vals.append(entry_type)
    if subscriber_id:
        sql += " AND subscriber_id = ?"
        vals.append(subscriber_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def subscriber_names(tenant_id: int, ids) -> dict[int, str]:
    """أسماء العرض الحقيقية للمستفيدين دفعة واحدة: {المعرّف: الاسم}.

    تُستخدم لإظهار اسم حقيقي في عمود «المستفيد» بالسجل المالي بدل «#رقم»
    خام: الاسم الكامل إن وُجد، وإلا اسم المستخدم. استعلام واحد لكل الصفحة.
    """
    uniq = sorted({int(i) for i in ids if i})
    if not uniq:
        return {}
    placeholders = ",".join("?" for _ in uniq)
    rows = db().execute(
        "SELECT id, full_name, username FROM subscribers "
        f"WHERE tenant_id = ? AND id IN ({placeholders})",
        [tenant_id, *uniq],
    ).fetchall()
    out: dict[int, str] = {}
    for r in rows:
        full = (r["full_name"] or "").strip()
        out[int(r["id"])] = full or (r["username"] or "").strip()
    return out


def get_ledger_entry(tenant_id: int, entry_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ? AND id = ?",
        (tenant_id, entry_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def create_payment(*, tenant_id: int, subscriber: dict, plan: dict | None,
                   amount: float, currency: str, method: str,
                   created_by: str, plan_price: float, custom_price: float | None,
                   discount_amount: float, discount_reason: str,
                   effective_price: float, earned_minutes: int,
                   rounding_mode: str, notes: str,
                   distributor_id: int | None = None,
                   metadata: dict[str, Any] | None = None) -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO payment_transactions(
                tenant_id, subscriber_id, username, plan_id, amount, currency,
                method, status, plan_price, custom_price, discount_amount,
                discount_reason, effective_price, earned_minutes, rounding_mode,
                created_by, notes, metadata_json, distributor_id, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, subscriber["id"], subscriber["username"],
                plan["id"] if plan else None, amount, currency, method,
                "posted", plan_price, custom_price, discount_amount,
                discount_reason, effective_price, earned_minutes,
                rounding_mode, created_by, notes, json_dump(metadata or {}),
                distributor_id,
                now_iso(),
            ),
        )
        payment_id = cur.lastrowid
        ledger_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="payment",
            amount=amount,
            direction="credit",
            currency=currency,
            subscriber_id=subscriber["id"],
            username=subscriber["username"],
            operator=created_by,
            source_type="payment",
            source_id=payment_id,
            notes=notes,
            metadata={
                "plan_id": plan["id"] if plan else None,
                "plan_price": plan_price,
                "custom_price": custom_price,
                "discount_amount": discount_amount,
                "discount_reason": discount_reason,
                "effective_price": effective_price,
                "earned_minutes": earned_minutes,
                "rounding_mode": rounding_mode,
                **(metadata or {}),
            },
        )
        conn.execute(
            "UPDATE payment_transactions SET ledger_entry_id = ? WHERE id = ?",
            (ledger_id, payment_id),
        )
    return get_payment(tenant_id, payment_id) or {}


def get_payment(tenant_id: int, payment_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM payment_transactions WHERE tenant_id = ? AND id = ?",
        (tenant_id, payment_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def mark_payment_applied(tenant_id: int, payment_id: int, *, minutes: int,
                         radius_action_id: str = "", was_unlimited: bool = False) -> None:
    """يُسجّل على الدفعة أنّ وقتها طُبِّق فعلًا على الحساب (بالدقائق) — كي
    يستطيع إلغاؤها لاحقًا أن يسترجع **المدّة نفسها بالضبط** لا تقديرًا.

    ``was_unlimited``: الحساب كان بلا تاريخ انتهاء (غير محدود) قبل الدفعة؛
    إلغاؤها يُعيده غير محدود بدل أن يجعل انتهاءه «الآن»."""
    with transaction() as conn:
        conn.execute(
            "UPDATE payment_transactions SET metadata_json = json_set("
            "COALESCE(NULLIF(metadata_json, ''), '{}'), '$.applied_minutes', ?, "
            "'$.radius_action_id', ?, '$.was_unlimited', ?) WHERE tenant_id = ? AND id = ?",
            (int(minutes), str(radius_action_id or ""), 1 if was_unlimited else 0,
             tenant_id, int(payment_id)),
        )


def mark_loan_applied(tenant_id: int, loan_id: int, *, minutes: int) -> None:
    """مثل ``mark_payment_applied`` للسلفة: الدقائق التي طُبِّقت فعلًا على
    الحساب — كي يسترجعها عكسُ قيد السلفة من الدفتر بالضبط."""
    with transaction() as conn:
        conn.execute(
            "UPDATE loan_entries SET metadata_json = json_set("
            "COALESCE(NULLIF(metadata_json, ''), '{}'), '$.applied_minutes', ?) "
            "WHERE tenant_id = ? AND id = ?",
            (int(minutes), tenant_id, int(loan_id)),
        )


def payment_settlements(tenant_id: int, payment_id: int) -> list[dict]:
    """تسويات السلف المُرحَّلة التي سدّدتها هذه الدفعة (خيار «خصم»)."""
    return [dict(r) for r in db().execute(
        "SELECT * FROM settlement_entries WHERE tenant_id = ? AND payment_id = ? "
        "AND status = 'posted' ORDER BY id", (tenant_id, int(payment_id)),
    ).fetchall()]


def payment_debt_settlements(tenant_id: int, payment_id: int) -> list[dict]:
    """قيود «تسديد دين» (رصيد سالب) التي سدّدتها هذه الدفعة ولم تُعكس بعد."""
    return [dict(r) for r in db().execute(
        "SELECT e.* FROM accounting_ledger_entries e WHERE e.tenant_id = ? "
        "AND e.entry_type = 'debt_settlement' AND e.reversal_of_entry_id IS NULL "
        "AND json_extract(e.metadata_json, '$.payment_id') = ? "
        "AND NOT EXISTS (SELECT 1 FROM accounting_ledger_entries r "
        "  WHERE r.tenant_id = e.tenant_id AND r.reversal_of_entry_id = e.id) "
        "ORDER BY e.id", (tenant_id, int(payment_id)),
    ).fetchall()]


def void_settlement(*, tenant_id: int, settlement_id: int, actor: str,
                    reason: str = "", reverse_ledger: bool = True) -> dict:
    """عكسُ تسوية سلفة: تُعلَّم «voided» (فتخرج من مجموع المُسدَّد ويعود
    المتبقّي)، وتُعاد السلفة «open» إن كانت قد أُغلقت بالتسوية، ويُكتب قيدٌ
    عكسيّ لقيدها في الدفتر (``reverse_ledger``). مرّةً واحدة (كتابة مشروطة)."""
    with transaction() as conn:
        claim = conn.execute(
            "UPDATE settlement_entries SET status = 'voided' "
            "WHERE tenant_id = ? AND id = ? AND status = 'posted'",
            (tenant_id, int(settlement_id)),
        )
        if claim.rowcount != 1:
            raise RadiusConflict("هذه التسوية مُلغاة مسبقًا.")
        row = dict(conn.execute(
            "SELECT * FROM settlement_entries WHERE tenant_id = ? AND id = ?",
            (tenant_id, int(settlement_id)),
        ).fetchone())
        reopened = conn.execute(
            "UPDATE loan_entries SET status = 'open', settled_at = NULL "
            "WHERE tenant_id = ? AND id = ? AND status = 'settled'",
            (tenant_id, row.get("loan_id")),
        ).rowcount == 1
        ledger_id = row.get("ledger_entry_id")
        if reverse_ledger and ledger_id and not conn.execute(
            "SELECT 1 FROM accounting_ledger_entries WHERE tenant_id = ? "
            "AND reversal_of_entry_id = ? LIMIT 1", (tenant_id, int(ledger_id)),
        ).fetchone():
            original = conn.execute(
                "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ? AND id = ?",
                (tenant_id, int(ledger_id)),
            ).fetchone()
            if original:
                original = dict(original)
                create_ledger_entry(
                    conn, tenant_id=tenant_id, entry_type="void",
                    amount=-float(original["amount"] or 0),
                    direction="debit" if original["direction"] == "credit" else "credit",
                    currency=original["currency"],
                    subscriber_id=original["subscriber_id"],
                    username=original["username"], operator=actor,
                    source_type="settlement_void", source_id=int(settlement_id),
                    related_type="loan", related_id=row.get("loan_id"),
                    reversal_of_entry_id=int(ledger_id), status="void", notes=reason,
                    metadata={"voided_settlement_id": int(settlement_id),
                              "reason": reason},
                )
    loan = get_loan(tenant_id, int(row.get("loan_id") or 0)) or {}
    return {"settlement_id": int(settlement_id), "loan_id": row.get("loan_id"),
            "amount": float(row.get("amount") or 0), "loan_reopened": reopened,
            "loan_status": loan.get("status"),
            "loan_outstanding": loan.get("outstanding", 0.0)}


def set_loan_status(tenant_id: int, loan_id: int, *, status: str,
                    expect: tuple[str, ...]) -> bool:
    """تغيير حالة سلفة بكتابةٍ مشروطة (حالتها الحاليّة ضمن ``expect``)."""
    marks = ",".join("?" for _ in expect)
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE loan_entries SET status = ?, settled_at = "
            "CASE WHEN ? = 'open' THEN NULL ELSE COALESCE(settled_at, ?) END "
            f"WHERE tenant_id = ? AND id = ? AND status IN ({marks})",
            (status, status, now_iso(), tenant_id, int(loan_id), *expect),
        )
    return cur.rowcount == 1


def loan_posted_settlements(tenant_id: int, loan_id: int) -> int:
    return int(db().execute(
        "SELECT COUNT(*) FROM settlement_entries WHERE tenant_id = ? AND loan_id = ? "
        "AND status = 'posted'", (tenant_id, int(loan_id)),
    ).fetchone()[0] or 0)


def void_payment(*, tenant_id: int, payment: dict, actor: str,
                 reason: str = "") -> Optional[dict]:
    """إلغاء دفعة **مرّةً واحدة** (ذرّيًّا): أوّل عبارةٍ كتابةٌ مشروطة على حالة
    الدفعة، فطلبان متوازيان لا يُنتجان قيدين عكسيّين أبدًا."""
    ledger_id = payment.get("ledger_entry_id")
    if not ledger_id:
        return None
    original = get_ledger_entry(tenant_id, int(ledger_id))
    if not original:
        return None
    amount = -float(original["amount"] or 0)
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE payment_transactions SET status = 'voided' "
            "WHERE tenant_id = ? AND id = ? AND status != 'voided'",
            (tenant_id, payment["id"]),
        )
        if cur.rowcount != 1:
            raise RadiusConflict("الدفعة مُلغاة مسبقًا.")
        if conn.execute(
            "SELECT 1 FROM accounting_ledger_entries WHERE tenant_id = ? "
            "AND reversal_of_entry_id = ? LIMIT 1", (tenant_id, int(ledger_id)),
        ).fetchone():
            raise RadiusConflict("قيد هذه الدفعة معكوسٌ مسبقًا من الدفتر.")
        void_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="void",
            amount=amount,
            direction="debit" if original["direction"] == "credit" else "credit",
            currency=original["currency"],
            subscriber_id=original["subscriber_id"],
            username=original["username"],
            operator=actor,
            source_type="payment_void",
            source_id=payment["id"],
            related_type="payment",
            related_id=payment["id"],
            reversal_of_entry_id=int(ledger_id),
            status="void",
            notes=reason,
            metadata={"voided_payment_id": payment["id"], "reason": reason},
        )
    return {
        "payment": get_payment(tenant_id, payment["id"]) or {},
        "entry": get_ledger_entry(tenant_id, void_id) or {},
    }


def list_payments(tenant_id: int, *, subscriber_id: int | None = None,
                  distributor_id: int | None = None,
                  limit: int = 100, offset: int = 0) -> list[dict]:
    sc, sv = _rscope(tenant_id, "subscriber_id")
    sql = "SELECT * FROM payment_transactions WHERE tenant_id = ?" + sc
    vals: list[Any] = [tenant_id, *sv]
    if subscriber_id:
        sql += " AND subscriber_id = ?"
        vals.append(subscriber_id)
    if distributor_id:
        sql += " AND distributor_id = ?"
        vals.append(distributor_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def create_loan(*, tenant_id: int, subscriber: dict, duration_minutes: int,
                amount: float, currency: str, reason: str, created_by: str,
                starts_at: str, ends_at: str, max_limit_snapshot: int,
                metadata: dict[str, Any] | None = None) -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO loan_entries(
                tenant_id, subscriber_id, username, duration_minutes, amount,
                currency, reason, status, approval_status, starts_at, ends_at,
                max_limit_snapshot, created_by, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, subscriber["id"], subscriber["username"],
                duration_minutes, amount, currency, reason, "open",
                "not_required", starts_at, ends_at, max_limit_snapshot,
                created_by, json_dump(metadata or {}), now_iso(),
            ),
        )
        loan_id = cur.lastrowid
        ledger_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="loan",
            amount=amount,
            direction="debit",
            currency=currency,
            subscriber_id=subscriber["id"],
            username=subscriber["username"],
            operator=created_by,
            source_type="loan",
            source_id=loan_id,
            notes=reason,
            metadata={
                "duration_minutes": duration_minutes,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "max_limit_snapshot": max_limit_snapshot,
                **(metadata or {}),
            },
        )
        conn.execute(
            "UPDATE loan_entries SET ledger_entry_id = ? WHERE id = ?",
            (ledger_id, loan_id),
        )
    return get_loan(tenant_id, loan_id) or {}


def _loan_row(row) -> dict:
    """صفّ سلفة + ``settled_amount`` (المُسدَّد) + ``outstanding`` (المتبقّي)."""
    d = dict(row)
    amount = float(d.get("amount") or 0)
    settled = float(d.get("settled_amount") or 0)
    d["settled_amount"] = round(settled, 2)
    d["original_amount"] = round(amount, 2)
    d["outstanding"] = (round(max(amount - settled, 0.0), 2)
                        if d.get("status") == "open" else 0.0)
    return d


def get_loan(tenant_id: int, loan_id: int) -> Optional[dict]:
    row = db().execute(
        f"SELECT *, {_LOAN_SETTLED_SQL} AS settled_amount "
        "FROM loan_entries WHERE tenant_id = ? AND id = ?",
        (tenant_id, loan_id),
    ).fetchone()
    return _loan_row(row) if row else None


def _loans_where(tenant_id: int, *, status: str = "",
                 subscriber_id: int | None = None) -> tuple[str, list[Any]]:
    sc, sv = _rscope(tenant_id, "subscriber_id")
    sql = " WHERE tenant_id = ?" + sc
    vals: list[Any] = [tenant_id, *sv]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    if subscriber_id:
        sql += " AND subscriber_id = ?"
        vals.append(subscriber_id)
    return sql, vals


def list_loans(tenant_id: int, *, status: str = "", subscriber_id: int | None = None,
               limit: int = 100, offset: int = 0) -> list[dict]:
    where, vals = _loans_where(tenant_id, status=status, subscriber_id=subscriber_id)
    sql = (f"SELECT *, {_LOAN_SETTLED_SQL} AS settled_amount FROM loan_entries"
           + where + " ORDER BY id DESC LIMIT ? OFFSET ?")
    vals.extend([limit, offset])
    return [_loan_row(r) for r in db().execute(sql, vals).fetchall()]


def loan_totals(tenant_id: int, *, status: str = "",
                subscriber_id: int | None = None) -> dict:
    """إجماليّات **كل** السلف المطابقة للفلتر (لا الصفحة المعروضة فقط) —
    العدد والقيمة والمتبقّي المفتوح — محسوبةً في SQL."""
    where, vals = _loans_where(tenant_id, status=status, subscriber_id=subscriber_id)
    row = db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total, "
        f"COALESCE(SUM(CASE WHEN status = 'open' THEN MAX(amount - {_LOAN_SETTLED_SQL}, 0) "
        "ELSE 0 END), 0) AS outstanding, "
        "COALESCE(SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), 0) AS open_count "
        "FROM loan_entries" + where, vals,
    ).fetchone()
    # Per-currency split (money deferral, a10 F8): there is no FX rate, so a
    # mixed-currency tenant gets one number per currency next to the legacy
    # single-number fields (which stay for older clients).
    by_currency = [
        {"currency": r["currency"] or "", "count": int(r["n"] or 0),
         "total_amount": round(float(r["total"] or 0), 2),
         "outstanding": round(float(r["outstanding"] or 0), 2)}
        for r in db().execute(
            "SELECT UPPER(COALESCE(currency, '')) AS currency, COUNT(*) AS n, "
            "COALESCE(SUM(amount), 0) AS total, "
            f"COALESCE(SUM(CASE WHEN status = 'open' THEN MAX(amount - {_LOAN_SETTLED_SQL}, 0) "
            "ELSE 0 END), 0) AS outstanding "
            "FROM loan_entries" + where + " GROUP BY UPPER(COALESCE(currency, '')) "
            "ORDER BY total DESC", vals,
        ).fetchall()
    ]
    return {
        "count": int(row["n"] or 0),
        "total_amount": round(float(row["total"] or 0), 2),
        "open_count": int(row["open_count"] or 0),
        "outstanding": round(float(row["outstanding"] or 0), 2),
        "by_currency": by_currency,
        "mixed_currency": len(by_currency) > 1,
    }


def settle_loan(*, tenant_id: int, loan: dict, amount: float, currency: str,
                method: str, created_by: str, notes: str = "",
                metadata: dict[str, Any] | None = None,
                payment_id: int | None = None) -> dict:
    """تسوية سلفة (كاملة أو **جزئيّة**) مرّةً واحدة وذرّيًّا.

    أوّل عبارةٍ في المعاملة كتابةٌ مشروطة: تُحدَّث السلفة فقط إن كانت ما تزال
    «open» والمبلغ لا يتجاوز المتبقّي — فتسويتان متوازيتان لا تمرّان معًا، ولا
    تُقبل تسويةٌ فوق الدين. تبقى السلفة مفتوحةً حتى يُسدَّد متبقّيها كلّه."""
    amount = round(float(amount), 2)
    now = now_iso()
    with transaction() as conn:
        claim = conn.execute(
            f"""
            UPDATE loan_entries
            SET status = CASE WHEN amount - {_LOAN_SETTLED_SQL} - ? <= ?
                              THEN 'settled' ELSE 'open' END,
                settled_at = CASE WHEN amount - {_LOAN_SETTLED_SQL} - ? <= ?
                                  THEN ? ELSE settled_at END
            WHERE tenant_id = ? AND id = ? AND status = 'open'
              AND ? <= amount - {_LOAN_SETTLED_SQL} + ?
            """,
            (amount, _MONEY_EPS, amount, _MONEY_EPS, now,
             tenant_id, loan["id"], amount, _MONEY_EPS),
        )
        if claim.rowcount != 1:
            raise RadiusConflict(
                "السلفة ليست مفتوحة أو المبلغ يتجاوز المتبقّي عليها.")
        cur = conn.execute(
            """
            INSERT INTO settlement_entries(
                tenant_id, subscriber_id, username, loan_id, amount, currency,
                method, status, created_by, notes, metadata_json, created_at,
                payment_id
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, loan["subscriber_id"], loan["username"], loan["id"],
                amount, currency, method, "posted", created_by, notes,
                json_dump(metadata or {}), now_iso(),
                int(payment_id) if payment_id else None,
            ),
        )
        settlement_id = cur.lastrowid
        ledger_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="settlement",
            amount=amount,
            direction="credit",
            currency=currency,
            subscriber_id=loan["subscriber_id"],
            username=loan["username"],
            operator=created_by,
            source_type="settlement",
            source_id=settlement_id,
            related_type="loan",
            related_id=loan["id"],
            notes=notes,
            metadata=metadata,
        )
        conn.execute(
            "UPDATE settlement_entries SET ledger_entry_id = ? WHERE id = ?",
            (ledger_id, settlement_id),
        )
        conn.execute(
            "UPDATE loan_entries SET settlement_entry_id = ? WHERE tenant_id = ? AND id = ?",
            (settlement_id, tenant_id, loan["id"]),
        )
    out = get_settlement(tenant_id, settlement_id) or {}
    after = get_loan(tenant_id, int(loan["id"])) or {}
    out["loan_status"] = after.get("status")
    out["loan_outstanding"] = after.get("outstanding", 0.0)
    return out


def writeoff_loan(*, tenant_id: int, loan: dict, currency: str, created_by: str,
                  notes: str = "", metadata: dict[str, Any] | None = None) -> dict:
    """Forgive (مسامحة) an open loan: void it + post a reversing CREDIT ledger
    entry so the debt disappears from the books. Append-only / audit-preserving —
    the original loan debit stays; the write-off credit nets it to zero.
    """
    with transaction() as conn:
        # كتابةٌ مشروطة أوّلًا: لا مسامحةَ لسلفةٍ أُغلقت (تسوية/مسامحة متوازية).
        claim = conn.execute(
            "UPDATE loan_entries SET status = 'voided', settled_at = ? "
            "WHERE tenant_id = ? AND id = ? AND status = 'open'",
            (now_iso(), tenant_id, loan["id"]),
        )
        if claim.rowcount != 1:
            raise RadiusConflict("السلفة ليست مفتوحة.")
        # المسامحة تشطب **المتبقّي** فقط — ما سُدِّد جزئيًّا قبلها يبقى مُسدَّدًا.
        settled = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM settlement_entries "
            "WHERE tenant_id = ? AND loan_id = ? AND status = 'posted'",
            (tenant_id, loan["id"]),
        ).fetchone()[0]
        amount = round(max(float(loan.get("amount") or 0) - float(settled or 0), 0.0), 2)
        ledger_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="writeoff",
            amount=amount,
            direction="credit",
            currency=currency,
            subscriber_id=loan["subscriber_id"],
            username=loan["username"],
            operator=created_by,
            source_type="loan_writeoff",
            source_id=loan["id"],
            related_type="loan",
            related_id=loan["id"],
            notes=notes or "مسامحة سلفة",
            metadata=metadata or {"action": "writeoff"},
        )
    out = get_loan(tenant_id, loan["id"]) or {}
    out["writeoff_ledger_entry_id"] = ledger_id
    return out


def get_settlement(tenant_id: int, settlement_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM settlement_entries WHERE tenant_id = ? AND id = ?",
        (tenant_id, settlement_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def ledger_entry_reversed(tenant_id: int, entry_id: int) -> bool:
    """هل لهذا القيد قيدٌ عكسيّ مُسجَّل؟"""
    return bool(db().execute(
        "SELECT 1 FROM accounting_ledger_entries WHERE tenant_id = ? "
        "AND reversal_of_entry_id = ? LIMIT 1", (tenant_id, int(entry_id)),
    ).fetchone())


def is_reversal_entry(entry: dict) -> bool:
    """قيدٌ عكسيّ (إلغاء/تصحيح) — لا يُعكس هو نفسه."""
    return (bool(entry.get("reversal_of_entry_id"))
            or str(entry.get("entry_type") or "") in {"void", "reversal"}
            or str(entry.get("status") or "") == "void")


def void_ledger_entry(*, tenant_id: int, entry_id: int, actor: str,
                      reason: str = "") -> Optional[dict]:
    """قيدٌ عكسيّ **واحد** لكل قيد، ولا عكسَ لقيدٍ عكسيّ.

    العبارة الأولى كتابةٌ (تأخذ قفل الكتابة) فيرى الفحصُ التالي أحدثَ البيانات:
    طلبان متوازيان على القيد نفسه ⇒ واحدٌ ينجح والآخر 409."""
    with transaction() as conn:
        conn.execute(
            "UPDATE accounting_ledger_entries SET status = status "
            "WHERE tenant_id = ? AND id = ?", (tenant_id, entry_id),
        )
        row = conn.execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ? AND id = ?",
            (tenant_id, entry_id),
        ).fetchone()
        if not row:
            return None
        original = dict(row)
        if is_reversal_entry(original):
            raise RadiusValidationError("لا يمكن عكس قيدٍ عكسيّ.")
        if conn.execute(
            "SELECT 1 FROM accounting_ledger_entries WHERE tenant_id = ? "
            "AND reversal_of_entry_id = ? LIMIT 1", (tenant_id, entry_id),
        ).fetchone():
            raise RadiusConflict("هذا القيد معكوسٌ (مُلغى) مسبقًا.")
        amount = -float(original["amount"] or 0)
        new_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="void",
            amount=amount,
            direction="debit" if original["direction"] == "credit" else "credit",
            currency=original["currency"],
            subscriber_id=original["subscriber_id"],
            username=original["username"],
            operator=actor,
            source_type="ledger_void",
            source_id=entry_id,
            reversal_of_entry_id=entry_id,
            status="void",
            notes=reason,
            metadata={"voided_entry_id": entry_id, "reason": reason},
        )
    return get_ledger_entry(tenant_id, new_id)


def _local_offset_minutes(tenant_id: int) -> int:
    """إزاحة منطقة المشغّل الآن بالدقائق (+180 لغزّة/دمشق). آمنة دائمًا."""
    try:
        from ...core.system_config import tenant_tzinfo
        off = tenant_tzinfo(tenant_id).utcoffset(datetime.utcnow())
        return int(off.total_seconds() // 60) if off is not None else 0
    except Exception:  # noqa: BLE001 — إعدادٌ تالف لا يكسر التقرير
        return 180


def _local_ts_expr(column: str, tenant_id: int) -> str:
    """الطابع المخزَّن (UTC، بـT/Z أو بمسافة) مُحوَّلًا إلى وقت المشغّل المحلّيّ —
    كي يبدأ «اليوم» في التقارير عند منتصف ليله هو لا منتصف ليل UTC."""
    return f"datetime({column}, '{_local_offset_minutes(tenant_id):+d} minutes')"


def sales_summary(tenant_id: int, *, grain: str = "daily") -> list[dict]:
    local = _local_ts_expr("l.created_at", tenant_id)
    if grain == "monthly":
        expr = f"substr({local}, 1, 7)"
    elif grain == "yearly":
        expr = f"substr({local}, 1, 4)"
    else:
        expr = f"substr({local}, 1, 10)"
    # العدد/المتوسّط على الدفعات الفعليّة فقط (المُلغاة وقيد إلغائها خارج العدّ)؛
    # المجموع صافٍ كما كان. ``by_currency`` لكلّ فترة — لا جمع ILS+USD في رقم.
    sc, sv = _rscope(tenant_id)
    frm = f"""
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ?
          AND {_PAYMENT_ROWS_SQL}{sc}
    """
    counted = f"CASE WHEN {_COUNTED_PAYMENT_SQL} THEN 1 ELSE 0 END"
    rows = db().execute(
        f"""
        SELECT {expr} AS period,
               COALESCE(SUM({counted}), 0) AS transactions,
               COUNT(DISTINCT CASE WHEN {_COUNTED_PAYMENT_SQL} THEN l.username END) AS subscribers,
               COALESCE(SUM(l.amount), 0) AS total,
               ROUND(CAST(COALESCE(SUM(l.amount), 0) AS REAL) / NULLIF(SUM({counted}), 0), 2) AS avg_amount
        {frm}
        GROUP BY {expr}
        ORDER BY period DESC
        LIMIT 60
        """,
        (tenant_id, *sv),
    ).fetchall()
    cur_sql = _CURRENCY_SQL.format(col="l.currency")
    split: dict[str, list] = {}
    for r in db().execute(
        f"""
        SELECT {expr} AS period, {cur_sql} AS currency,
               COALESCE(SUM({counted}), 0) AS transactions,
               COALESCE(SUM(l.amount), 0) AS total
        {frm}
        GROUP BY {expr}, {cur_sql}
        """,
        (tenant_id, *sv),
    ).fetchall():
        split.setdefault(r["period"], []).append(r)
    out = []
    for r in rows:
        item = dict(r)
        item["total"] = round(float(item["total"] or 0), 2)
        by_cur = _merge_by_currency(split.get(item["period"], []), ("total", "transactions"))
        for c in by_cur:
            c["avg_amount"] = (round(c["total"] / c["transactions"], 2)
                               if c["transactions"] else None)
        item["by_currency"] = by_cur
        item["mixed_currency"] = len(by_cur) > 1
        out.append(item)
    return out


def subscriber_payment_report(tenant_id: int, *, subscriber_id: int | None = None,
                              limit: int | None = None, offset: int = 0) -> list[dict]:
    """دفعات المستفيدين — **كل** الدافعين (كان مقصوصًا على 200 بصمت).
    ``limit``/``offset`` اختياريّان للترقيم؛ بدونهما يُعاد الكلّ."""
    sc, sv = _rscope(tenant_id)
    frm = f"""
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ?
          AND {_PAYMENT_ROWS_SQL}{sc}
    """
    counted = f"CASE WHEN {_COUNTED_PAYMENT_SQL} THEN 1 ELSE 0 END"
    # «العدد» = الدفعات الفعليّة (الدفعة المُلغاة وقيد إلغائها لا يُعدّان).
    sql = f"""
        SELECT l.subscriber_id, l.username, COALESCE(SUM({counted}), 0) AS count,
               COALESCE(SUM(l.amount), 0) AS total,
               MAX(l.created_at) AS last_entry_at
        {frm}
    """
    vals: list[Any] = [tenant_id, *sv]
    if subscriber_id:
        sql += " AND l.subscriber_id = ?"
        vals.append(subscriber_id)
    cur_sql = _CURRENCY_SQL.format(col="l.currency")
    split_sql = (f"SELECT l.subscriber_id, l.username, {cur_sql} AS currency, "
                 f"COALESCE(SUM({counted}), 0) AS count, COALESCE(SUM(l.amount), 0) AS total "
                 + frm + (" AND l.subscriber_id = ?" if subscriber_id else "")
                 + f" GROUP BY l.subscriber_id, l.username, {cur_sql}")
    sql += " GROUP BY l.subscriber_id, l.username ORDER BY last_entry_at DESC, l.username"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        vals.extend([int(limit), max(int(offset or 0), 0)])
    rows = [dict(r) for r in db().execute(sql, vals).fetchall()]
    if not rows:
        return rows
    split: dict[tuple, list] = {}
    split_vals = [tenant_id, *sv] + ([subscriber_id] if subscriber_id else [])
    for r in db().execute(split_sql, split_vals).fetchall():
        split.setdefault((r["subscriber_id"], r["username"]), []).append(r)
    for item in rows:
        item["total"] = round(float(item["total"] or 0), 2)
        by_cur = _merge_by_currency(split.get((item["subscriber_id"], item["username"]), []),
                                    ("total", "count"))
        item["by_currency"] = by_cur
        item["mixed_currency"] = len(by_cur) > 1
    return rows


def subscriber_payment_totals(tenant_id: int) -> dict:
    """إجماليّ «دفعات المستفيدين» كلّه في SQL (عدد الدافعين والمجموع).
    ``entries`` = الدفعات الفعليّة (بلا المُلغاة وقيود إلغائها) — كانت دفعةٌ
    وإلغاؤها قيدين؛ ``ledger_rows`` = كلّ صفوف الدفتر الداخلة في المجموع."""
    counted = f"CASE WHEN {_COUNTED_PAYMENT_SQL} THEN 1 ELSE 0 END"
    sc, sv = _rscope(tenant_id)
    row = db().execute(
        f"""
        SELECT COUNT(DISTINCT l.username) AS payers, COALESCE(SUM({counted}), 0) AS entries,
               COUNT(*) AS ledger_rows,
               COALESCE(SUM(l.amount), 0) AS total
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ?
          AND (
            (l.entry_type = 'payment' AND l.status = 'posted')
            OR (l.entry_type IN ('void', 'reversal', 'correction') AND orig.entry_type = 'payment')
          ){sc}
        """,
        (tenant_id, *sv),
    ).fetchone()
    # Per-currency split (no FX rate — one number per currency, a10 F8).
    by_currency = [
        {"currency": r["currency"] or "", "entries": int(r["entries"] or 0),
         "total": round(float(r["total"] or 0), 2)}
        for r in db().execute(
            f"""
            SELECT UPPER(COALESCE(l.currency, '')) AS currency, COALESCE(SUM({counted}), 0) AS entries,
                   COALESCE(SUM(l.amount), 0) AS total
            FROM accounting_ledger_entries l
            LEFT JOIN accounting_ledger_entries orig
              ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
            WHERE l.tenant_id = ?
              AND (
                (l.entry_type = 'payment' AND l.status = 'posted')
                OR (l.entry_type IN ('void', 'reversal', 'correction') AND orig.entry_type = 'payment')
              ){sc}
            GROUP BY UPPER(COALESCE(l.currency, ''))
            ORDER BY total DESC
            """,
            (tenant_id, *sv),
        ).fetchall()
    ]
    return {"payers": int(row["payers"] or 0), "entries": int(row["entries"] or 0),
            "ledger_rows": int(row["ledger_rows"] or 0),
            "total": round(float(row["total"] or 0), 2),
            "by_currency": by_currency, "mixed_currency": len(by_currency) > 1}


def subscriber_total_paid(tenant_id: int, subscriber_id: int) -> float:
    """مجموع **كل** دفعات المشترك غير الملغاة (لا آخر 50 فقط)."""
    row = db().execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payment_transactions "
        "WHERE tenant_id = ? AND subscriber_id = ? AND status != 'voided'",
        (tenant_id, int(subscriber_id)),
    ).fetchone()
    return round(float(row[0] or 0), 2)


def payment_revenue_items(tenant_id: int, *, limit: int = 200,
                          offset: int = 0) -> list[dict]:
    """دفعات المشتركين بشكل «سجلّ إيراد» — المصدر نفسه لتقارير المبيعات
    (قيود الدفتر ``payment``)، مع حالة «voided» للدفعة المعكوسة."""
    sc, sv = _rscope(tenant_id)
    rows = db().execute(
        """
        SELECT l.id, l.source_id, l.amount, l.currency, l.username, l.subscriber_id,
               l.created_at, l.operator,
               CASE WHEN EXISTS (SELECT 1 FROM accounting_ledger_entries v
                                 WHERE v.tenant_id = l.tenant_id
                                   AND v.reversal_of_entry_id = l.id)
                    THEN 'voided' ELSE 'posted' END AS status
        FROM accounting_ledger_entries l
        WHERE l.tenant_id = ? AND l.entry_type = 'payment' AND l.status = 'posted'
        """ + sc + """
        ORDER BY l.id DESC LIMIT ? OFFSET ?
        """,
        (tenant_id, *sv, int(limit), max(int(offset or 0), 0)),
    ).fetchall()
    return [dict(r) for r in rows]


def loan_report(tenant_id: int) -> list[dict]:
    outstanding = (f"COALESCE(SUM(CASE WHEN status = 'open' "
                   f"THEN MAX(amount - {_LOAN_SETTLED_SQL}, 0) ELSE 0 END), 0)")
    sc, sv = _rscope(tenant_id, "subscriber_id")
    rows = db().execute(
        f"""
        SELECT status, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total,
               {outstanding} AS outstanding,
               COALESCE(SUM(duration_minutes), 0) AS duration_minutes
        FROM loan_entries
        WHERE tenant_id = ?{sc}
        GROUP BY status
        ORDER BY status
        """,
        (tenant_id, *sv),
    ).fetchall()
    split: dict[str, list] = {}
    cur_sql = _CURRENCY_SQL.format(col="currency")
    for r in db().execute(
        f"SELECT status, {cur_sql} AS currency, COALESCE(SUM(amount), 0) AS total, "
        f"{outstanding} AS outstanding FROM loan_entries WHERE tenant_id = ?{sc} "
        f"GROUP BY status, {cur_sql}",
        (tenant_id, *sv),
    ).fetchall():
        split.setdefault(r["status"], []).append(r)
    out = []
    for r in rows:
        item = dict(r)
        by_cur = _merge_by_currency(split.get(item["status"], []), ("total", "outstanding"))
        item["by_currency"] = by_cur
        item["mixed_currency"] = len(by_cur) > 1
        out.append(item)
    return out


def activation_report(tenant_id: int) -> list[dict]:
    # «عدد التفعيلات» = الدفعات الفعليّة فقط: الدفعة المُلغاة وقيد إلغائها لا
    # يُعدّان (كانا تفعيلين)؛ صافي الدقائق يبقى كما هو (+م ثمّ −م).
    counted = _COUNTED_PAYMENT_SQL
    sc, sv = _rscope(tenant_id)
    rows = db().execute(
        f"""
        SELECT
            l.entry_type, l.status, l.amount, l.username, l.subscriber_id,
            l.metadata_json, orig.metadata_json AS orig_metadata_json,
            CASE WHEN {counted} THEN 1 ELSE 0 END AS counted
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ?
          AND (
            (l.entry_type = 'payment' AND l.status = 'posted')
            OR (l.entry_type IN ('void', 'reversal', 'correction') AND orig.entry_type = 'payment')
          ){sc}
        """,
        (tenant_id, *sv),
    ).fetchall()
    totals: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        meta = json_load(item.get("metadata_json"), default={}) or {}
        orig_meta = json_load(item.get("orig_metadata_json"), default={}) or {}
        minutes = int((orig_meta if item["entry_type"] != "payment" else meta).get("earned_minutes") or 0)
        if item["entry_type"] != "payment":
            minutes = -minutes
        key = item.get("username") or ""
        current = totals.setdefault(key, {
            "username": key,
            "subscriber_id": item.get("subscriber_id"),
            "activation_count": 0,
            "earned_minutes": 0,
        })
        current["activation_count"] += int(item.get("counted") or 0)
        current["earned_minutes"] += minutes
    return sorted(totals.values(), key=lambda x: x["earned_minutes"], reverse=True)


# ────────────────────────────────────────────────────────────────────────
# Subscribers Overview — period-bucketed reporting series (monthly/yearly).
# Mirror sales_summary's grain pattern. Consumed by
# routes/subscribers_overview.py. See SERVICES_COOKBOOK.md §20.
# ────────────────────────────────────────────────────────────────────────

def _grain_expr(column: str, grain: str) -> str:
    """SUBSTR bucket expression for a YYYY-MM (monthly) / YYYY (yearly) prefix.

    Only monthly + yearly are supported here on purpose — the Subscribers
    Overview deliberately drops the daily/weekly grains.
    """
    return f"substr({column}, 1, 4)" if grain == "yearly" else f"substr({column}, 1, 7)"


def loans_summary(tenant_id: int, *, grain: str = "monthly") -> list[dict]:
    """السلف — loans granted per period (count + value + minutes + still-open).

    Source: loan_entries (the dedicated loans table). Kept separate from
    الديون/outstanding so the two read as distinct buckets (operator decision).
    """
    expr = _grain_expr("created_at", grain)
    sc, sv = _rscope(tenant_id, "subscriber_id")
    rows = db().execute(
        f"""
        SELECT {expr} AS period,
               COUNT(*) AS count,
               COALESCE(SUM(amount), 0) AS total,
               COALESCE(SUM(duration_minutes), 0) AS minutes,
               COALESCE(SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), 0) AS still_open
        FROM loan_entries
        WHERE tenant_id = ?{sc}
        GROUP BY period
        ORDER BY period DESC
        LIMIT 24
        """,
        (tenant_id, *sv),
    ).fetchall()
    return [dict(r) for r in rows]


def activation_summary(tenant_id: int, *, grain: str = "monthly") -> list[dict]:
    """التفعيل — activations per period (posted payments that granted time).

    Source: payment_transactions with earned_minutes > 0 (simpler/cleaner than
    parsing ledger metadata). Returns count + amount collected + minutes granted.
    """
    expr = _grain_expr("created_at", grain)
    sc, sv = _rscope(tenant_id, "subscriber_id")
    rows = db().execute(
        f"""
        SELECT {expr} AS period,
               COUNT(*) AS count,
               COALESCE(SUM(amount), 0) AS amount,
               COALESCE(SUM(earned_minutes), 0) AS minutes
        FROM payment_transactions
        WHERE tenant_id = ?
          AND status = 'posted'
          AND earned_minutes > 0{sc}
        GROUP BY period
        ORDER BY period DESC
        LIMIT 24
        """,
        (tenant_id, *sv),
    ).fetchall()
    return [dict(r) for r in rows]


def data_usage_summary(tenant_id: int, *, grain: str = "monthly") -> list[dict]:
    """الجيجات — data consumed per period (bytes_in + bytes_out + sessions).

    Source: bandwidth_usage_daily (pre-aggregated, fast). Caller converts
    bytes → GB. This is consumption; quota *allocation* is point-in-time.
    """
    expr = _grain_expr("day", grain)
    sc, sv = _rscope(tenant_id, "subscriber_id")
    rows = db().execute(
        f"""
        SELECT {expr} AS period,
               COALESCE(SUM(bytes_in), 0) AS bytes_in,
               COALESCE(SUM(bytes_out), 0) AS bytes_out,
               COALESCE(SUM(sessions_count), 0) AS sessions
        FROM bandwidth_usage_daily
        WHERE tenant_id = ?{sc}
        GROUP BY period
        ORDER BY period DESC
        LIMIT 24
        """,
        (tenant_id, *sv),
    ).fetchall()
    return [dict(r) for r in rows]


def outstanding_summary(tenant_id: int) -> dict:
    """شو ضل / الديون — point-in-time outstanding (NOT period-bucketed).

    Per the operator's definition, «what's left» = two components measured
    as-of-now (balances are not historized per month):
      • open loans        — loan_entries.status = 'open'
      • negative balances — subscribers whose balance < 0 (in deficit)
    The credit side (positive balances) is returned for context.
    """
    sc, sv = _rscope(tenant_id, "subscriber_id")
    ssc, ssv = _rscope(tenant_id, "id")
    loans = db().execute(
        f"""
        SELECT COALESCE(SUM(MAX(amount - {_LOAN_SETTLED_SQL}, 0)), 0) AS total,
               COUNT(*) AS count,
               COALESCE(SUM(duration_minutes), 0) AS minutes
        FROM loan_entries
        WHERE tenant_id = ? AND status = 'open'{sc}
        """,
        (tenant_id, *sv),
    ).fetchone()
    bal = db().execute(
        """
        SELECT COALESCE(SUM(CASE WHEN balance < 0 THEN -balance ELSE 0 END), 0) AS owed,
               COALESCE(SUM(CASE WHEN balance < 0 THEN 1 ELSE 0 END), 0) AS owed_count,
               COALESCE(SUM(CASE WHEN balance > 0 THEN balance ELSE 0 END), 0) AS credit,
               COALESCE(SUM(CASE WHEN balance > 0 THEN 1 ELSE 0 END), 0) AS credit_count
        FROM subscribers
        WHERE tenant_id = ? AND deleted_at IS NULL""" + ssc + """
        """,
        (tenant_id, *ssv),
    ).fetchone()
    open_loans_total = float((loans and loans["total"]) or 0.0)
    owed = float((bal and bal["owed"]) or 0.0)
    return {
        "open_loans_total": open_loans_total,
        "open_loans_count": int((loans and loans["count"]) or 0),
        "open_loans_minutes": int((loans and loans["minutes"]) or 0),
        "balance_owed": owed,
        "balance_owed_count": int((bal and bal["owed_count"]) or 0),
        "balance_credit": float((bal and bal["credit"]) or 0.0),
        "balance_credit_count": int((bal and bal["credit_count"]) or 0),
        "outstanding_total": open_loans_total + owed,
    }


def top_debtors(tenant_id: int, *, limit: int = 8) -> list[dict]:
    """Per-subscriber drill-down rows for the overview — biggest open-loan
    holders + deepest negative balances, each deep-linking to Finance.
    """
    ssc, ssv = _rscope(tenant_id, "s.id")
    rows = db().execute(
        f"""
        SELECT s.id AS subscriber_id, s.username, s.full_name, s.balance,
               COALESCE(l.open_total, 0) AS open_loans_total,
               COALESCE(l.open_count, 0) AS open_loans_count
        FROM subscribers s
        LEFT JOIN (
            SELECT subscriber_id,
                   SUM(MAX(amount - {_LOAN_SETTLED_SQL}, 0)) AS open_total,
                   COUNT(*) AS open_count
            FROM loan_entries
            WHERE tenant_id = ? AND status = 'open'
            GROUP BY subscriber_id
        ) l ON l.subscriber_id = s.id
        WHERE s.tenant_id = ? AND s.deleted_at IS NULL{ssc}
          AND (s.balance < 0 OR COALESCE(l.open_total, 0) > 0)
        ORDER BY (COALESCE(l.open_total, 0) + CASE WHEN s.balance < 0 THEN -s.balance ELSE 0 END) DESC
        LIMIT ?
        """,
        (tenant_id, tenant_id, *ssv, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def profit_loss_summary(tenant_id: int) -> list[dict]:
    # 🔴 القيد العكسيّ يُخزَّن بمبلغٍ سالب **و**اتجاهٍ مقلوب (إلغاء دفعة X =
    # ‎-X مدين). جمعُه في جهة اتجاهه يطرح سالبًا من المدين ⇒ الإلغاء يُحسب
    # ربحًا مرّتين. الصحيح: العكسيّ يُجمع في جهة **القيد الأصليّ** (عكس اتجاهه)
    # بمبلغه السالب، فيُصفّر أصله.
    side = ("CASE WHEN reversal_of_entry_id IS NOT NULL "
            "THEN (CASE direction WHEN 'debit' THEN 'credit' ELSE 'debit' END) "
            "ELSE direction END")
    sc, sv = _rscope(tenant_id, "subscriber_id")
    row = db().execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN {side} = 'credit' THEN amount ELSE 0 END), 0) AS credits,
            COALESCE(SUM(CASE WHEN {side} = 'debit' THEN amount ELSE 0 END), 0) AS debits,
            COUNT(*) AS entries
        FROM accounting_ledger_entries
        WHERE tenant_id = ?{sc}
        """,
        (tenant_id, *sv),
    ).fetchone()
    credits = float(row["credits"] or 0)
    debits = float(row["debits"] or 0)
    # لكلّ عملة سطرها — لا سعر صرف، فجمع ILS+USD في «صافٍ» واحد مضلِّل.
    cur_sql = _CURRENCY_SQL.format(col="currency")
    split = db().execute(
        f"""
        SELECT {cur_sql} AS currency,
            COALESCE(SUM(CASE WHEN {side} = 'credit' THEN amount ELSE 0 END), 0) AS credits,
            COALESCE(SUM(CASE WHEN {side} = 'debit' THEN amount ELSE 0 END), 0) AS debits,
            COUNT(*) AS entries
        FROM accounting_ledger_entries
        WHERE tenant_id = ?{sc}
        GROUP BY {cur_sql}
        """,
        (tenant_id, *sv),
    ).fetchall()
    by_cur = _merge_by_currency(split, ("credits", "debits", "entries"))
    for c in by_cur:
        c["net"] = round(c["credits"] - c["debits"], 2)
    return [{
        "credits": credits,
        "debits": debits,
        "net": credits - debits,
        "entries": int(row["entries"] or 0),
        "source": "accounting_ledger_entries",
        "by_currency": by_cur,
        "mixed_currency": len(by_cur) > 1,
    }]


def revenue_summary(tenant_id: int, *, utc_from: str = "", utc_to: str = "") -> dict:
    """الإيراد الموحّد (ويب «المركز المالي» + «التقارير المالية» + الـAPI).

    المصدر نفسه لـ ``/api/v1/finance/revenue`` وتقرير «دفعات المستفيدين»:
      • دفعات المشتركين من دفتر المحاسبة (الصافي: الدفعة − إلغاؤها)؛
        ``transactions`` = الدفعات الفعليّة (بلا المُلغاة).
      • سجلّات ``revenue_records`` المُرحَّلة (مبيعات كروت المتجر…) —
        المُحصَّل وصافي ربحها. لا تتداخل مع الدفتر (دفترٌ آخر للكروت).
    الربح = الدفعات (صافي ربحها = مبلغها) + صافي ربح السجلّات.
    ``utc_from``/``utc_to`` حدّا ‎[from, to)‎ بطوابع UTC (يومٌ محلّيّ عبر
    ``local_period_utc_range``). المجاميع **لكلّ عملة** في ``by_currency``؛
    الحقول المفردة تبقى للتوافق (مجموعٌ خامّ قد يخلط العملات — راجع
    ``mixed_currency``)."""
    counted = f"CASE WHEN {_COUNTED_PAYMENT_SQL} THEN 1 ELSE 0 END"
    rng, rvals = _range_sql("l.created_at", utc_from, utc_to)
    sc, sv = _rscope(tenant_id)
    rng, rvals = rng + sc, [*rvals, *sv]
    cur_sql = _CURRENCY_SQL.format(col="l.currency")
    pay_rows = db().execute(
        f"""
        SELECT {cur_sql} AS currency, COALESCE(SUM(l.amount), 0) AS payments,
               COALESCE(SUM({counted}), 0) AS transactions
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ? AND {_PAYMENT_ROWS_SQL}{rng}
        GROUP BY {cur_sql}
        """,
        [tenant_id, *rvals],
    ).fetchall()
    rec_rows: list = []
    try:
        if _rscope_on():
            # card-store revenue records carry no subscriber: a scoped manager
            # sees only his own subscribers' payments (fix3).
            raise LookupError("scoped")
        rrng, rrvals = _range_sql("created_at", utc_from, utc_to)
        rec_rows = db().execute(
            f"""
            SELECT {_CURRENCY_SQL.format(col='currency')} AS currency,
                   COALESCE(SUM(collected_amount_minor), 0) AS collected_minor,
                   COALESCE(SUM(net_profit_minor), 0) AS profit_minor,
                   COUNT(*) AS records
            FROM revenue_records
            WHERE tenant_id = ? AND status = 'posted'{rrng}
            GROUP BY {_CURRENCY_SQL.format(col='currency')}
            """,
            [tenant_id, *rrvals],
        ).fetchall()
    except Exception:  # noqa: BLE001 — جدول غائب في قاعدة قديمة
        rec_rows = []
    merged: dict[str, dict] = {}

    def _slot(cur: str) -> dict:
        return merged.setdefault(cur, {"currency": cur, "revenue": 0.0, "profit": 0.0,
                                       "payments": 0.0, "transactions": 0,
                                       "records_collected": 0.0, "records_profit": 0.0,
                                       "records": 0})

    for r in pay_rows:
        s = _slot(_currency_code(r["currency"]))
        s["payments"] += float(r["payments"] or 0)
        s["transactions"] += int(r["transactions"] or 0)
    for r in rec_rows:
        s = _slot(_currency_code(r["currency"]))
        s["records_collected"] += float(r["collected_minor"] or 0) / 100.0
        s["records_profit"] += float(r["profit_minor"] or 0) / 100.0
        s["records"] += int(r["records"] or 0)
    by_cur = []
    for s in merged.values():
        s["revenue"] = s["payments"] + s["records_collected"]
        s["profit"] = s["payments"] + s["records_profit"]
        for k in ("revenue", "profit", "payments", "records_collected", "records_profit"):
            s[k] = round(s[k], 2)
        by_cur.append(s)
    system = default_currency()
    by_cur.sort(key=lambda s: (s["currency"] != system, -abs(s["revenue"])))

    def _total(key: str) -> float:
        return round(sum(float(s[key]) for s in by_cur), 2)

    return {
        "revenue": _total("revenue"),
        "profit": _total("profit"),
        "payments": _total("payments"),
        "transactions": sum(int(s["transactions"]) for s in by_cur),
        "records_collected": _total("records_collected"),
        "records_profit": _total("records_profit"),
        "records": sum(int(s["records"]) for s in by_cur),
        "by_currency": by_cur,
        "mixed_currency": len(by_cur) > 1,
    }


def card_sales_report(tenant_id: int) -> list[dict]:
    from ...services.card_batch_scope import batch_scope_sql
    bsc, bsv = batch_scope_sql(column="CAST(source_id AS INTEGER)", tenant_id=int(tenant_id))
    rows = db().execute(
        """
        SELECT source_id AS batch_id, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total
        FROM accounting_ledger_entries
        WHERE tenant_id = ? AND entry_type = 'payment'
          AND source_type = 'card_sale' AND status = 'posted'""" + bsc + """
        GROUP BY source_id
        ORDER BY total DESC
        LIMIT 200
        """,
        (tenant_id, *bsv),
    ).fetchall()
    return [dict(r) for r in rows]


def distributor_debts_report(tenant_id: int) -> list[dict]:
    from ...services.subscriber_scope import current_scope_admin_id
    scope = current_scope_admin_id(tenant_id=int(tenant_id))
    dsc, dsv = ("", []) if scope is None else (
        " AND (admin_id = ? OR login_admin_id = ?)", [int(scope), int(scope)])
    rows = db().execute(
        """
        SELECT id AS distributor_id, name, display_name, debt_balance, balance, credit_limit
        FROM distributors
        WHERE tenant_id = ? AND status = 'active'""" + dsc + """
        ORDER BY debt_balance DESC, name
        LIMIT 200
        """,
        (tenant_id, *dsv),
    ).fetchall()
    return [dict(r) for r in rows]


def create_report_snapshot(tenant_id: int, *, report_type: str,
                           result: dict | list, created_by: str = "",
                           date_from: str = "", date_to: str = "",
                           parameters: dict | None = None) -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO financial_report_snapshots(
                tenant_id, report_type, date_from, date_to, parameters_json,
                result_json, source, created_by, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, report_type, date_from, date_to,
                json_dump(parameters or {}), json_dump(result),
                "ledger", created_by, now_iso(),
            ),
        )
    row = db().execute(
        "SELECT * FROM financial_report_snapshots WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _serialize_report_snapshot(row_to_dict(row))


def _serialize_report_snapshot(row: dict) -> dict:
    if not row:
        return {}
    data = dict(row)
    data["parameters"] = json_load(data.pop("parameters_json", "{}"), {})
    data["result"] = json_load(data.pop("result_json", "{}"), {})
    return data


def list_report_snapshots(tenant_id: int, *, report_type: str = "",
                          limit: int = 50, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM financial_report_snapshots WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if report_type:
        sql += " AND report_type = ?"
        vals.append(report_type)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    rows = db().execute(sql, vals).fetchall()
    return [_serialize_report_snapshot(row_to_dict(r)) for r in rows]


def get_report_snapshot(tenant_id: int, snapshot_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM financial_report_snapshots WHERE tenant_id = ? AND id = ?",
        (tenant_id, snapshot_id),
    ).fetchone()
    return _serialize_report_snapshot(row_to_dict(row)) if row else None
