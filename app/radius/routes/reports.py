"""
Reports — قراءات تحليلية مبنية على جداولنا (radacct, radpostauth, audit_log,
sync_queue, webhook_deliveries). كلها read-only، tenant-scoped.
"""
from __future__ import annotations

import json

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..services.dashboard_reports import DashboardReportsService


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _svc() -> DashboardReportsService:
    return DashboardReportsService(tenant_id=_tid())


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "غير معروف"


_ACTION_LABELS = {
    "create": "إنشاء",
    "update": "تعديل",
    "delete": "حذف",
    "disable": "تعطيل",
    "enable": "تفعيل",
    "extend_time": "تمديد",
    "reset_password": "إعادة تعيين كلمة المرور",
    "bulk_set_speeds": "تحديث جماعي للسرعات",
    "notification.manual_queued": "رسالة يدوية",
    "payment_collection.settings_saved": "حفظ إعدادات التحصيل",
    "payment_collection.request_approved": "اعتماد طلب دفع",
    "payment_collection.request_rejected": "رفض طلب دفع",
}

_TARGET_LABELS = {
    "user": "مشترك",
    "subscriber": "مشترك",
    "card": "كرت",
    "plan": "باقة",
    "admin": "مدير",
    "manager": "مدير",
    "distributor": "موزّع",
    "notification_campaign": "حملة رسائل",
    "payment_request": "طلب دفع",
    "router": "راوتر",
    "nas": "جهاز شبكة",
    "service": "خدمة",
}


def _display_action(action: str) -> str:
    action = (action or "").strip()
    if not action:
        return "غير محدد"
    return _ACTION_LABELS.get(action, action.replace("_", " ").replace(".", " "))


def _display_target(target_type: str, target_id: object) -> str:
    label = _TARGET_LABELS.get((target_type or "").strip(), "كيان")
    return f"{label} #{target_id}" if target_id not in (None, "") else label


def _display_actor(actor: str) -> str:
    actor = (actor or "").strip()
    if actor.startswith("api-token"):
        return "مفتاح ربط"
    if actor == "system":
        return "النظام"
    return actor or "غير معروف"


def _payload_summary(raw: object) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return "تفاصيل محفوظة في السجل"
    if not isinstance(data, dict) or not data:
        return "تفاصيل محفوظة في السجل"
    keys = {
        "username": "المستخدم",
        "plan": "الباقة",
        "plan_id": "رقم الباقة",
        "status": "الحالة",
        "amount": "المبلغ",
        "channel": "القناة",
        "count": "العدد",
    }
    bits = []
    for key, label in keys.items():
        value = data.get(key)
        if value not in (None, "", [], {}):
            bits.append(f"{label}: {value}")
        if len(bits) >= 3:
            break
    return "، ".join(bits) if bits else "تفاصيل محفوظة في السجل"


def _decorate_audit_rows(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["actor_label"] = _display_actor(str(row.get("actor") or ""))
        row["action_label"] = _display_action(str(row.get("action") or ""))
        row["target_type_label"] = _TARGET_LABELS.get(str(row.get("target_type") or ""), "كيان")
        row["target_display"] = _display_target(str(row.get("target_type") or ""), row.get("target_id"))
        row["payload_summary"] = _payload_summary(row.get("payload_json"))
    return rows


def register_reports_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/reports", "reports_home", reports_home, methods=["GET"])
    bp.add_url_rule("/reports/summary.json", "reports_summary_json", reports_summary_json, methods=["GET"])
    bp.add_url_rule("/reports/financial", "reports_financial", reports_financial, methods=["GET"])
    bp.add_url_rule("/reports/cards", "reports_cards", reports_cards, methods=["GET"])
    bp.add_url_rule("/reports/distributors", "reports_distributors", reports_distributors, methods=["GET"])
    bp.add_url_rule("/reports/archive", "reports_archive", reports_archive, methods=["GET"])
    bp.add_url_rule("/reports/archive/create", "reports_archive_create", reports_archive_create, methods=["POST"])
    bp.add_url_rule("/reports/sessions", "rep_sessions", rep_sessions, methods=["GET"])
    bp.add_url_rule("/reports/failed_logins", "rep_failed_logins", rep_failed_logins, methods=["GET"])
    bp.add_url_rule("/reports/login_status", "rep_login_status", rep_login_status, methods=["GET"])
    bp.add_url_rule("/reports/login_states", "rep_login_states", rep_login_states, methods=["GET"])
    # R12.3: صفحتان مخصّصتان مفصولتان — لكل نوع حساب رابطه ومساره الخاص،
    # تُربطان من قسمَي «البطاقات» و«المشتركون» في الشريط الجانبي (لا من
    # التقارير). فتح صفحة الكروت يعرض الكروت فقط، وصفحة المشتركين تعرض
    # المشتركين فقط.
    bp.add_url_rule("/reports/login_states/cards", "rep_login_states_cards",
                    rep_login_states_cards, methods=["GET"])
    bp.add_url_rule("/reports/login_states/subscribers",
                    "rep_login_states_subscribers",
                    rep_login_states_subscribers, methods=["GET"])
    bp.add_url_rule("/reports/login_states/sub_portal",
                    "rep_login_states_sub_portal",
                    rep_login_states_sub_portal, methods=["GET"])
    bp.add_url_rule("/reports/login_states/card_store",
                    "rep_login_states_card_store",
                    rep_login_states_card_store, methods=["GET"])
    bp.add_url_rule("/reports/login_states/admin",
                    "rep_login_states_admin",
                    rep_login_states_admin, methods=["GET"])
    bp.add_url_rule("/reports/mac_history", "rep_mac_history", rep_mac_history, methods=["GET"])
    bp.add_url_rule("/reports/profile_changes", "rep_profile_changes", rep_profile_changes, methods=["GET"])
    bp.add_url_rule("/reports/api_messages", "rep_api_messages", rep_api_messages, methods=["GET"])
    bp.add_url_rule("/reports/coa_failures", "rep_coa_failures", rep_coa_failures, methods=["GET"])
    bp.add_url_rule("/reports/manager_events", "rep_manager_events", rep_manager_events, methods=["GET"])
    bp.add_url_rule("/reports/manager_login_status", "rep_manager_login_status", rep_manager_login_status, methods=["GET"])
    bp.add_url_rule("/reports/user_events", "rep_user_events", rep_user_events, methods=["GET"])
    bp.add_url_rule("/reports/speed_failures", "rep_speed_failures", rep_speed_failures, methods=["GET"])
    bp.add_url_rule("/reports/used_cards", "rep_used_cards", rep_used_cards, methods=["GET"])
    bp.add_url_rule("/reports/balance_movements", "rep_balance_movements", rep_balance_movements, methods=["GET"])
    bp.add_url_rule("/reports/cash_transactions", "rep_cash_transactions", rep_cash_transactions, methods=["GET"])


def reports_home():
    svc = _svc()
    summary = svc.executive_summary(
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
    )
    return render_template(
        "radius/reports_center.html",
        summary=summary,
        catalog=svc.report_catalog(),
        active="home",
    )


def reports_summary_json():
    summary = _svc().executive_summary(
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
    )
    return jsonify({"status": "ok", "summary": summary})


def reports_financial():
    return _report_page("financial", "التقارير المالية")


def reports_cards():
    return _report_page("cards", "تقارير الكروت")


def reports_distributors():
    return _report_page("distributors", "تقارير الموزعين")


def _report_page(report_type: str, title: str):
    data = _svc().report_data(
        report_type,
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
    )
    return render_template(
        "radius/reports_detail.html",
        title=title,
        report_type=report_type,
        data=data,
        active=report_type,
    )


def reports_archive():
    svc = _svc()
    return render_template(
        "radius/reports_archive.html",
        archives=svc.list_archives(),
        summary=svc.executive_summary(),
        active="archive",
    )


def reports_archive_create():
    archive = _svc().create_archive_snapshot(
        archive_type=request.form.get("archive_type") or "yearly",
        period=request.form.get("period") or "",
        report_type=request.form.get("report_type") or "financial",
        actor=_actor(),
    )
    flash(
        "تم إنشاء نسخة أرشيف جديدة." if archive.get("created") else "نسخة الأرشيف موجودة مسبقًا، وتم الحفاظ عليها بدون تغيير.",
        "success",
    )
    return redirect(url_for("radius.reports_archive"))


def _limit() -> tuple[int, int]:
    try:
        l = min(max(int(request.args.get("limit") or 100), 1), 1000)
        o = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        l, o = 100, 0
    return l, o


def _args() -> dict:
    """فلاتر مشتركة لصفحات التقارير: بحث نصّي + نطاق تاريخ."""
    return {
        "q":         (request.args.get("q") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }


def _date_where(col: str, date_from: str, date_to: str) -> tuple[list, list]:
    where, params = [], []
    if date_from:
        where.append(f"{col} >= ?"); params.append(f"{date_from} 00:00:00")
    if date_to:
        where.append(f"{col} <= ?"); params.append(f"{date_to} 23:59:59")
    return where, params


def _audit_rows(base_where: str, base_params: list, f: dict, *,
                q_cols=("actor", "action", "target_id"), limit: int = 500):
    """قراءة audit_log مع فلاتر q + نطاق تاريخ. يرجّع (rows, total_count)."""
    where = [base_where]
    params = list(base_params)
    if f["q"]:
        like = f"%{f['q']}%"
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in q_cols) + ")")
        params += [like] * len(q_cols)
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    total = db().execute(
        f"SELECT COUNT(*) AS c FROM audit_log WHERE {where_sql}", params
    ).fetchone()["c"]
    rows = [dict(r) for r in db().execute(
        f"SELECT * FROM audit_log WHERE {where_sql} ORDER BY id DESC LIMIT ?",
        params + [limit],
    ).fetchall()]
    return _decorate_audit_rows(rows), total


# ─────────────── 1. Sessions (radacct) ───────────────

def rep_sessions():
    limit, offset = _limit()
    f = _args()
    username = (request.args.get("username") or "").strip()
    sql = "SELECT * FROM radacct WHERE tenant_id = ?"
    vals: list = [_tid()]
    if username:
        sql += " AND username LIKE ?"
        vals.append(f"%{username}%")
    dw, dp = _date_where("acctstarttime", f["date_from"], f["date_to"])
    if dw:
        sql += " AND " + " AND ".join(dw); vals += dp
    sql += " ORDER BY radacctid DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    rows = [dict(r) for r in db().execute(sql, vals).fetchall()]
    return render_template("radius/rep_sessions.html",
                            items=rows, username=username, limit=limit, filters=f)


# ─────────────── 2. Failed logins (radpostauth Access-Reject) ───────────────

def rep_failed_logins():
    f = _args()
    where = ["tenant_id = ?", "reply != 'Access-Accept'"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR nas LIKE ? OR class LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    dw, dp = _date_where("authdate", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) AS c FROM radpostauth WHERE {where_sql}", params).fetchone()["c"]
    rows = [dict(r) for r in db().execute(
        f"SELECT * FROM radpostauth WHERE {where_sql} ORDER BY id DESC LIMIT 500", params
    ).fetchall()]
    # تعريب عمود «السبب»: رمز radpostauth.class → عربي عبر الخريطة الموحّدة،
    # وأي رمز غير معروف يُؤنَّس (لا snake_case إنجليزي خام). الخام يبقى في title.
    from ..services.login_events import reason_label
    for r in rows:
        r["reason_ar"] = reason_label(r.get("class") or "")
    # مؤشّر «آخر 24 ساعة» — عدّ بسيط مستقل عن الفلاتر (نفس الجدول والشرط الأساسي)
    last24 = db().execute(
        "SELECT COUNT(*) AS c FROM radpostauth "
        "WHERE tenant_id = ? AND reply != 'Access-Accept' "
        "AND authdate >= datetime('now', '-1 day')", [_tid()]
    ).fetchone()["c"]
    return render_template("radius/rep_failed_logins.html",
                           items=rows, total=total, last24=last24, filters=f, limit=500)


# ─────────────── 3. Login status (last login per user) ───────────────

def rep_login_status():
    f = _args()
    status = (request.args.get("status") or "").strip()
    where = ["tenant_id = ?"]
    params: list = [_tid()]
    if f["q"]:
        where.append("username LIKE ?"); params.append(f"%{f['q']}%")
    if status in ("enabled", "disabled", "expired"):
        where.append("status = ?"); params.append(status)
    where_sql = " AND ".join(where)
    rows = [dict(r) for r in db().execute(f"""
        SELECT username, last_login_at, last_seen_at, status, expire_at, online_count
        FROM subscribers WHERE {where_sql}
        ORDER BY last_seen_at DESC NULLS LAST LIMIT 500
    """, params).fetchall()]
    return render_template("radius/rep_login_status.html", items=rows, filters=f, status=status)


# ─────────────── 3b. Login states (unified: panel + portal + RADIUS) ───────────────
# الصفحة الرئيسية لحالات تسجيل الدخول + ثلاث صفحات فرعية مفروزة بدقة حسب الفاعل.
# نمط المسار: نفس العنوان /reports/login_states مع ?actor=admin|subscriber|card —
# يحافظ على الروابط القديمة (?actor=…) كما هي ويُبقي تفعيل الشريط الجانبي تلقائيًا.

# تعريف ثابت للأقسام الخمسة — عنوان وأيقونة وسطر تعريفي ومصدر مثبّت لكل قسم.
# detail_endpoint يربط بطاقة القسم في الصفحة الرئيسية بصفحته المخصّصة.
_LOGIN_STATES_KINDS = {
    "subscriber": {
        "title": "حالات دخول المشتركين",
        "icon": "user",
        "subtitle": "محاولات مصادقة المشتركين عبر شبكة RADIUS (Access-Accept/Reject) — جهاز الشبكة وسبب الفشل.",
        "search_ph": "بحث (اسم المشترك / جهاز الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_subscribers",
    },
    "card": {
        "title": "حالات دخول البطاقات",
        "icon": "ticket",
        "subtitle": "محاولات مصادقة البطاقات عبر شبكة RADIUS — عنوان الجهاز وجهاز الشبكة وسبب الفشل.",
        "search_ph": "بحث (اسم البطاقة / عنوان الجهاز / جهاز الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_cards",
    },
    "sub_portal": {
        "title": "حالات بوابة المشتركين",
        "icon": "door-open",
        "subtitle": "محاولات دخول المشتركين عبر بوابة المشتركين على الويب — عنوان الشبكة والمتصفح والجهاز.",
        "search_ph": "بحث (اسم المشترك / عنوان الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_sub_portal",
        "actor": "subscriber",
    },
    "card_store": {
        "title": "حالات بوابة متجر البطاقات",
        "icon": "store",
        "subtitle": "محاولات دخول وتسجيل العملاء عبر متجر البطاقات (store API) — بالجوال وعنوان الشبكة.",
        "search_ph": "بحث (رقم الجوال / عنوان الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_card_store",
        "actor": "card",
    },
    "admin": {
        "title": "حالات دخول المدراء",
        "icon": "user-shield",
        "subtitle": "كل محاولات دخول المدراء إلى لوحة الإدارة — نجاحًا وفشلًا، مع عنوان الشبكة والمتصفح والجهاز.",
        "search_ph": "بحث (اسم المدير / عنوان الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_admin",
    },
}

# توافق خلفي: الروابط القديمة ?actor=subscriber|card|admin → الصفحة المخصّصة.
_ACTOR_COMPAT = {
    "subscriber": "radius.rep_login_states_subscribers",
    "card":       "radius.rep_login_states_cards",
    "admin":      "radius.rep_login_states_admin",
}


def _render_login_states_detail(actor: str, *, self_endpoint: str,
                                kind_key: str | None = None,
                                source_lock: str = ""):
    """يعرض صفحة حالات الدخول المفروزة لقسم واحد من الأقسام الخمسة.

    ``self_endpoint``: الراوت الذي تعود إليه فلاتر الصفحة ونموذج البحث.
    ``kind_key``: المفتاح في _LOGIN_STATES_KINDS (subscriber / card /
        sub_portal / card_store / admin). يُمرَّر افتراضيًا = actor.
    ``source_lock``: قيمة source مثبّتة على مستوى الراوت — لا يُمكن تجاوزها
        من URL params (يحمي من خلط RADIUS بالبوابة). فارغ = source حرّ.

    الفرز دقيق على مستوى الاستعلام في الخدمة: الويب يُقيَّد بـ
    target_type والشبكة بعضوية جدول الكروت — لا تخمين بصيغة الاسم.
    """
    from ..services.login_events import (
        fetch_login_events, ACTOR_LABELS, SOURCE_LABELS,
    )
    kk = kind_key or actor
    effective_source = (source_lock if source_lock
                        else (request.args.get("source") or "").strip())
    filters = {
        "actor":     actor,
        "result":    (request.args.get("result") or "").strip(),
        "source":    effective_source,
        "q":         (request.args.get("q") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }
    data = fetch_login_events(_tid(), **filters)
    return render_template(
        "radius/rep_login_states_detail.html",
        kind=kk, meta=_LOGIN_STATES_KINDS[kk], kinds=_LOGIN_STATES_KINDS,
        rows=data["rows"], stats=data["stats"],
        shown=data["shown"], matched=data["matched"],
        filters=filters, actor_labels=ACTOR_LABELS, source_labels=SOURCE_LABELS,
        self_endpoint=self_endpoint,
        source_locked=bool(source_lock),
    )


def rep_login_states_cards():
    """«حالات دخول البطاقات» — RADIUS فقط (قسم البطاقات)."""
    return _render_login_states_detail(
        "card", self_endpoint="radius.rep_login_states_cards",
        kind_key="card", source_lock="network")


def rep_login_states_subscribers():
    """«حالات دخول المشتركين» — RADIUS فقط (قسم المشتركون)."""
    return _render_login_states_detail(
        "subscriber", self_endpoint="radius.rep_login_states_subscribers",
        kind_key="subscriber", source_lock="network")


def rep_login_states_sub_portal():
    """«حالات بوابة المشتركين» — بوابة الويب للمشتركين فقط."""
    return _render_login_states_detail(
        "subscriber", self_endpoint="radius.rep_login_states_sub_portal",
        kind_key="sub_portal", source_lock="portal")


def rep_login_states_card_store():
    """«حالات بوابة متجر البطاقات» — دخول/تسجيل المتجر."""
    return _render_login_states_detail(
        "card", self_endpoint="radius.rep_login_states_card_store",
        kind_key="card_store", source_lock="portal")


def rep_login_states_admin():
    """«حالات دخول المدراء» — لوحة الإدارة فقط (قسم الإدارة)."""
    return _render_login_states_detail(
        "admin", self_endpoint="radius.rep_login_states_admin",
        kind_key="admin")


def rep_login_states():
    from ..services.login_events import login_states_overview
    actor = (request.args.get("actor") or "").strip()

    # توافق خلفي: ?actor=subscriber/card/admin → الصفحة المخصّصة الجديدة.
    if actor in _ACTOR_COMPAT:
        return redirect(url_for(_ACTOR_COMPAT[actor]))

    # ── الصفحة الرئيسية: خمس بطاقات بعدّادات مصغّرة ──
    # ‏login_states_overview على main يُرجع 3 دلاء (subscriber/card/admin)؛
    # نوسّعها إلى 5 بقيم صفرية للقسمَين الجديدَين (sub_portal/card_store)
    # حتى لا يفشل القالب على المفاتيح الناقصة. الأرقام تتطابق عند تحديث
    # الخدمة لاحقًا لتفصل بوابة عن متجر، بلا تغيير في الـUI.
    raw_overview = login_states_overview(_tid())
    _empty = {"total": 0, "ok": 0, "fail": 0, "today": 0}
    overview = {key: dict(raw_overview.get(key, _empty))
                for key in _LOGIN_STATES_KINDS.keys()}
    totals = {
        "total": sum(v["total"] for v in overview.values()),
        "ok":    sum(v["ok"] for v in overview.values()),
        "fail":  sum(v["fail"] for v in overview.values()),
        "today": sum(v["today"] for v in overview.values()),
    }
    return render_template(
        "radius/rep_login_states.html",
        overview=overview, totals=totals, kinds=_LOGIN_STATES_KINDS,
    )


# ─────────────── 4. MAC history (per username distinct MACs) ───────────────

def rep_mac_history():
    f = _args()
    where = ["tenant_id = ?", "callingstationid != ''"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR callingstationid LIKE ? OR nasipaddress LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    where_sql = " AND ".join(where)
    rows = [dict(r) for r in db().execute(f"""
        SELECT username, callingstationid AS mac, nasipaddress,
               COUNT(*) AS sessions, MAX(acctstarttime) AS last_seen
        FROM radacct WHERE {where_sql}
        GROUP BY username, callingstationid
        ORDER BY last_seen DESC NULLS LAST LIMIT 500
    """, params).fetchall()]
    return render_template("radius/rep_mac_history.html", items=rows, filters=f)


# ─────────────── 5. Profile (plan) changes (audit_log) ───────────────

def rep_profile_changes():
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND target_type = 'user' AND action IN ('update','extend_time')",
        [_tid()], f, limit=300)
    return render_template("radius/rep_profile_changes.html", items=rows, total=total, filters=f)


# ─────────────── 6. API messages (audit_log where actor=api-token) ───────────────

def rep_api_messages():
    f = _args()
    rows, total = _audit_rows("tenant_id = ? AND actor LIKE 'api-token%'", [_tid()], f, limit=300)
    return render_template("radius/rep_api_messages.html", items=rows, total=total, filters=f)


# ─────────────── 7. CoA failures (sync_queue disconnect failed) ───────────────

def rep_coa_failures():
    f = _args()
    where = ["tenant_id = ?", "kind IN ('disconnect','reset_password')",
             "status IN ('failed','retrying')"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(kind LIKE ? OR status LIKE ?)")
        params += [f"%{f['q']}%"] * 2
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    rows = [dict(r) for r in db().execute(
        f"SELECT * FROM sync_queue WHERE {where_sql} ORDER BY id DESC LIMIT 300", params
    ).fetchall()]
    return render_template("radius/rep_coa_failures.html", items=rows, filters=f)


# ─────────────── 8. Manager events (admin actions) ───────────────

def rep_manager_events():
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND actor NOT LIKE 'api-token%' AND actor != 'system'",
        [_tid()], f, limit=500)
    return render_template("radius/rep_manager_events.html", items=rows, total=total, filters=f)


# ─────────────── 9. Manager login status (admins) ───────────────

def rep_manager_login_status():
    f = _args()
    where, params = [], []
    if f["q"]:
        where.append("(username LIKE ? OR full_name LIKE ? OR email LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    # ضمّ اسم الدور العربي (roles.display_name / name) ليُعرض بدل role_id الرقمي
    rows = [dict(r) for r in db().execute(f"""
        SELECT a.id, a.username, a.full_name, a.email, a.role_id,
               a.is_super_admin, a.enabled, a.last_login_at, a.created_at,
               COALESCE(NULLIF(r.display_name, ''), r.name) AS role_name
        FROM admins a LEFT JOIN roles r ON r.id = a.role_id
        {where_sql} ORDER BY a.last_login_at DESC NULLS LAST
    """, params).fetchall()]
    return render_template("radius/rep_manager_login_status.html", items=rows, filters=f)


# ─────────────── 10. User events (per subscriber) ───────────────

def rep_user_events():
    f = _args()
    rows, total = _audit_rows("tenant_id = ? AND target_type = 'user'", [_tid()], f, limit=500)
    return render_template("radius/rep_user_events.html", items=rows, total=total, filters=f)


# ─────────────── 11. Speed-update failures (audit_log) ───────────────

def rep_speed_failures():
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND result_status = 'failed' "
        "AND (action LIKE '%speed%' OR action LIKE '%profile%' OR action = 'bulk_set_speeds')",
        [_tid()], f, q_cols=("actor", "action", "target_id", "error_message"), limit=300)
    return render_template("radius/rep_speed_failures.html", items=rows, total=total, filters=f)


# ─────────────── 12. Used recharge cards (cards) ───────────────

def rep_used_cards():
    f = _args()
    where = ["c.tenant_id = ?", "c.used = 1"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(c.username LIKE ? OR c.used_by_mac LIKE ?)")
        params += [f"%{f['q']}%"] * 2
    dw, dp = _date_where("c.first_used_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) AS c FROM cards c WHERE {where_sql}", params).fetchone()["c"]
    rows = [dict(r) for r in db().execute(f"""
        SELECT c.id, c.username, c.used_by_mac, c.first_used_at, c.expire_at,
               c.revoked, c.plan_id, COALESCE(p.name, '') AS plan_name
        FROM cards c LEFT JOIN access_plans p ON p.id = c.plan_id
        WHERE {where_sql}
        ORDER BY c.first_used_at DESC NULLS LAST LIMIT 500
    """, params).fetchall()]
    return render_template("radius/rep_used_cards.html", items=rows, total=total, filters=f)


# ─────────────── 13. Balance movements (accounting + distributor ledger) ───────────────

def rep_balance_movements():
    f = _args()
    rows: list[dict] = []
    # حركات الرصيد العامة (مشتركون/مدراء) من accounting_ledger_entries
    where = ["tenant_id = ?"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR operator LIKE ? OR entry_type LIKE ? OR source_type LIKE ?)")
        params += [f"%{f['q']}%"] * 4
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    ws = " AND ".join(where)
    try:
        for r in db().execute(f"""
            SELECT created_at, entry_type, direction, amount, currency, username,
                   operator, admin_id, source_type, status, notes
            FROM accounting_ledger_entries WHERE {ws}
            ORDER BY id DESC LIMIT 400""", params).fetchall():
            d = dict(r); d["scope"] = "general"; rows.append(d)
    except Exception:
        pass
    # حركات رصيد الموزّعين
    dwhere = ["dl.tenant_id = ?"]
    dparams: list = [_tid()]
    if f["q"]:
        dwhere.append("(d.name LIKE ? OR dl.entry_type LIKE ?)")
        dparams += [f"%{f['q']}%"] * 2
    ddw, ddp = _date_where("dl.created_at", f["date_from"], f["date_to"])
    dwhere += ddw; dparams += ddp
    dws = " AND ".join(dwhere)
    try:
        for r in db().execute(f"""
            SELECT dl.created_at, dl.entry_type, dl.direction, dl.amount, dl.currency,
                   COALESCE(d.name,'') AS username, dl.created_by AS operator,
                   dl.distributor_id AS admin_id, 'distributor' AS source_type,
                   dl.status, dl.notes
            FROM distributor_ledger_entries dl
            LEFT JOIN distributors d ON d.id = dl.distributor_id
            WHERE {dws} ORDER BY dl.id DESC LIMIT 400""", dparams).fetchall():
            x = dict(r); x["scope"] = "distributor"; rows.append(x)
    except Exception:
        pass
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    rows = rows[:500]
    return render_template("radius/rep_balance_movements.html", items=rows, total=len(rows), filters=f)


# ─────────────── 14. Cash transactions (payment_transactions) ───────────────

def rep_cash_transactions():
    f = _args()
    where = ["tenant_id = ?"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR created_by LIKE ? OR method LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    ws = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) AS c FROM payment_transactions WHERE {ws}", params).fetchone()["c"]
    agg = db().execute(
        f"SELECT COALESCE(SUM(amount),0) AS total_amount, COALESCE(SUM(discount_amount),0) AS total_discount "
        f"FROM payment_transactions WHERE {ws}", params).fetchone()
    rows = [dict(r) for r in db().execute(f"""
        SELECT id, created_at, username, amount, currency, method, status,
               plan_price, effective_price, discount_amount, discount_reason,
               earned_minutes, created_by, notes
        FROM payment_transactions WHERE {ws}
        ORDER BY id DESC LIMIT 500""", params).fetchall()]
    return render_template("radius/rep_cash_transactions.html", items=rows, total=total,
                           total_amount=agg["total_amount"], total_discount=agg["total_discount"], filters=f)
