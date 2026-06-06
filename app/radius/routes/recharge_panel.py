"""لوحة الشحن — شاشة تفعيل/تجديد سريعة للمدراء والموزعين.

فلسفة الصفحة: «صفر إعادة تنفيذ للمنطق المالي». كل عملية مال تمرّ عبر
المسارات/الخدمات الموجودة أصلًا:

  • تجديد نفس الباقة / شحن مبلغ نقدي → POST radius.users_payment_create
    (services/accounting.AccountingService.create_payment — السعر الفعلي
    عبر effective_subscriber_price بما فيه طبقة أسعار المدراء admin_pricing).
  • تفعيل بالوقت → POST radius.users_extend
    (services/users.UsersService.extend_time — مجاني/مدفوع/دين).
  • تغيير العرض ثم تفعيل → POST radius.users_change_plan ثم دفعة تجديد.

هذا الملف يضيف قراءات فقط (GET) تخدم الواجهة الفورية:
  بحث لحظي، بطاقة المشترك، آخر عمليات هذا المسؤول، ومؤشرات المحفظة.
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session

from ..core.errors import RadiusError
from ..core.system_config import default_currency, format_money
from ..db.repos import accounting_repo
from ..services.accounting import service_from_context
from ..services.plans import get_plans_service
from ..services.users import get_users_service


def register_recharge_panel_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/recharge", "recharge_panel", recharge_panel, methods=["GET"])
    bp.add_url_rule(
        "/recharge/search.json",
        "recharge_search_json",
        recharge_search_json,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/recharge/subscriber/<username>.json",
        "recharge_subscriber_json",
        recharge_subscriber_json,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/recharge/recent.json",
        "recharge_recent_json",
        recharge_recent_json,
        methods=["GET"],
    )


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


# ─────────────────────────── مؤشرات المحفظة ───────────────────────────

def _wallet_balance_for_admin(tenant_id: int) -> float:
    """رصيد محفظة المسؤول الحالي.

    المحافظ في wallets (Business OS) بوحدة minor (قروش). للمدير نجمع
    محافظه (owner_type='manager' و owner_id=admin_id)؛ super_admin يرى
    رصيد كل المحافظ (نظرة الشركة). أي فشل يُرجع 0.0 — المؤشر لا يكسر
    الصفحة أبدًا.
    """
    try:
        from ..db.connection import db

        if session.get("is_super_admin"):
            row = db().execute(
                "SELECT COALESCE(SUM(balance_minor),0) FROM wallets WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
        else:
            row = db().execute(
                "SELECT COALESCE(SUM(balance_minor),0) FROM wallets "
                "WHERE tenant_id=? AND owner_type='manager' AND owner_id=?",
                (tenant_id, int(session.get("admin_id") or 0)),
            ).fetchone()
        return round(float(row[0] or 0) / 100.0, 2)
    except Exception:  # noqa: BLE001 — مؤشر عرض فقط
        return 0.0


def _open_debts_total(tenant_id: int, actor: str) -> float:
    """إجمالي الديون المفتوحة (سلف غير مسددة) — نفس مصدر «المركز المالي».

    المدير يرى السلف التي سجّلها هو؛ super_admin يرى إجمالي المستأجر.
    """
    try:
        from ..db.connection import db

        if session.get("is_super_admin"):
            row = db().execute(
                "SELECT COALESCE(SUM(amount),0) FROM loan_entries "
                "WHERE tenant_id=? AND status='open'",
                (tenant_id,),
            ).fetchone()
        else:
            row = db().execute(
                "SELECT COALESCE(SUM(amount),0) FROM loan_entries "
                "WHERE tenant_id=? AND status='open' AND created_by=?",
                (tenant_id, actor),
            ).fetchone()
        return round(float(row[0] or 0), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _today_prefix() -> str:
    """بادئة تاريخ اليوم (UTC) — created_at مخزَّن بـ now_iso (UTC ISO)."""
    return datetime.utcnow().strftime("%Y-%m-%d")


def _collected_today(tenant_id: int, actor: str) -> float:
    """محصلة اليوم: مجموع دفعات payment_transactions المرحَّلة اليوم لهذا
    المسؤول (created_by) — super_admin يرى مجموع الكل."""
    try:
        from ..db.connection import db

        if session.get("is_super_admin"):
            row = db().execute(
                "SELECT COALESCE(SUM(amount),0) FROM payment_transactions "
                "WHERE tenant_id=? AND status='posted' AND created_at LIKE ?",
                (tenant_id, _today_prefix() + "%"),
            ).fetchone()
        else:
            row = db().execute(
                "SELECT COALESCE(SUM(amount),0) FROM payment_transactions "
                "WHERE tenant_id=? AND status='posted' AND created_by=? "
                "AND created_at LIKE ?",
                (tenant_id, actor, _today_prefix() + "%"),
            ).fetchone()
        return round(float(row[0] or 0), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _recent_operations(tenant_id: int, actor: str, limit: int = 5) -> list[dict]:
    """آخر عمليات اليوم لهذا المسؤول — دفعات payment_transactions فقط
    (قراءة عبر repo القائم ثم فلترة بالمنفّذ + اليوم)."""
    out: list[dict] = []
    try:
        rows = accounting_repo.list_payments(tenant_id, limit=120)
    except Exception:  # noqa: BLE001
        rows = []
    today = _today_prefix()
    is_super = bool(session.get("is_super_admin"))
    for p in rows:
        created = str(p.get("created_at") or "")
        if not created.startswith(today):
            continue
        if not is_super and str(p.get("created_by") or "") != actor:
            continue
        out.append({
            "id": p.get("id"),
            "username": p.get("username") or "",
            "amount": float(p.get("amount") or 0),
            "amount_label": format_money(p.get("amount"), p.get("currency")),
            "method": p.get("method") or "cash",
            # طريقة الدفع معرَّبة من الخادم (القالب يعرّب محلياً كذلك كحارس).
            "method_label": {"cash": "نقدًا", "transfer": "حوالة", "wallet": "محفظة"}.get(
                p.get("method"), p.get("method") or "نقدًا"
            ),
            "status": p.get("status") or "posted",
            "earned_minutes": int(p.get("earned_minutes") or 0),
            "created_at": created,
            "created_by": p.get("created_by") or "",
        })
        if len(out) >= limit:
            break
    return out


# ─────────────────────────── الصفحة الرئيسية ───────────────────────────

def recharge_panel():
    tenant_id = _tid()
    actor = _actor()
    plans = [
        p for p in get_plans_service().list(limit=500)
        if getattr(p, "id", None)
    ]
    kpis = {
        "wallet_balance": _wallet_balance_for_admin(tenant_id),
        "open_debts": _open_debts_total(tenant_id, actor),
        "collected_today": _collected_today(tenant_id, actor),
    }
    return render_template(
        "radius/recharge_panel.html",
        kpis=kpis,
        plans=plans,
        recent=_recent_operations(tenant_id, actor),
        currency=default_currency(),
    )


# ─────────────────────────── JSON للواجهة الحية ───────────────────────────

def recharge_search_json():
    """بحث لحظي بالمستخدم/الاسم/الجوال — يعيد استخدام نفس خدمة قائمة
    المشتركين (SQL pushdown على username/full_name/mobile)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"ok": True, "items": []})
    items = get_users_service().list(search=q, limit=8)
    return jsonify({
        "ok": True,
        "items": [
            {
                "username": s.username,
                "full_name": s.full_name or "",
                "mobile": s.mobile or "",
                "status": s.status or "",
                # توحيد مصدر الترجمة: نُرسل الحالة معرَّبة من الخادم أيضاً
                # (القالب يعرّب محلياً كحارس، وهذا يضمن صحّتها عند المصدر).
                "status_label": _status_label(s.status or ""),
                "plan_id": s.plan_id,
            }
            for s in items
        ],
    })


def _status_label(status: str) -> str:
    return {
        "enabled": "نشط",
        "disabled": "معطّل",
        "expired": "منتهي",
        "suspended": "موقوف",
        "pending": "قيد التفعيل",
        "banned": "محظور",
    }.get(status, status or "غير معروف")


def recharge_subscriber_json(username: str):
    """بطاقة المشترك للوحة الشحن: الحالة، الانتهاء، الرصيد، الباقة،
    والسعر الفعلي لهذا المشترك (custom > سعر المدير > سعر العرض) —
    نفس مصدر الحقيقة effective_subscriber_price عبر price_basis."""
    try:
        sub = get_users_service().get(username)
    except RadiusError:
        return jsonify({"ok": False, "error": "المشترك غير موجود."}), 404

    acc = service_from_context()
    basis = acc.price_basis(sub)

    plan_name = ""
    if sub.plan_id:
        plan = accounting_repo.resolve_plan(_tid(), int(sub.plan_id))
        plan_name = (plan or {}).get("name") or ""

    # آخر دفعة مسجّلة لهذا المشترك — تُعرض كمرجع «آخر فاتورة».
    last_payment = None
    try:
        pays = acc.list_payments(subscriber_id=int(sub.id), limit=1)
        if pays:
            p = pays[0]
            last_payment = {
                "id": p.get("id"),
                "amount_label": format_money(p.get("amount"), p.get("currency")),
                "created_at": p.get("created_at") or "",
                "created_by": p.get("created_by") or "",
            }
    except Exception:  # noqa: BLE001 — مرجع عرض فقط
        last_payment = None

    open_loans = 0.0
    try:
        open_loans = sum(
            float(ln.get("amount") or 0)
            for ln in acc.open_loans_for(subscriber_id=int(sub.id))
        )
    except Exception:  # noqa: BLE001
        open_loans = 0.0

    expire_iso = sub.expire_at.isoformat(timespec="seconds") if sub.expire_at else ""
    return jsonify({
        "ok": True,
        "subscriber": {
            "id": sub.id,
            "username": sub.username,
            "full_name": sub.full_name or "",
            "mobile": sub.mobile or "",
            "status": sub.status or "",
            "status_label": _status_label(sub.status or ""),
            "balance": float(sub.balance or 0),
            "balance_label": format_money(sub.balance or 0),
            "expire_at": expire_iso,
            "plan_id": sub.plan_id,
            "plan_name": plan_name,
            # السعر الفعلي + مدة الباقة بالدقائق — أساس كل حسابات الواجهة.
            "price": float(basis.get("price") or 0),
            "price_label": format_money(basis.get("price") or 0),
            "plan_minutes": int(basis.get("minutes") or 0),
            "price_custom": bool(basis.get("custom")),
            "open_loans": open_loans,
            "open_loans_label": format_money(open_loans),
        },
        "last_payment": last_payment,
    })


def recharge_recent_json():
    """آخر 5 عمليات اليوم لهذا المسؤول — يُستدعى بعد كل عملية ناجحة
    لتحديث الشريط دون إعادة تحميل، ومعه المؤشرات الثلاثة المحدثة."""
    tenant_id = _tid()
    actor = _actor()
    return jsonify({
        "ok": True,
        "items": _recent_operations(tenant_id, actor),
        "kpis": {
            "wallet_balance": format_money(_wallet_balance_for_admin(tenant_id)),
            "open_debts": format_money(_open_debts_total(tenant_id, actor)),
            "collected_today": format_money(_collected_today(tenant_id, actor)),
        },
    })
