"""
login_events — تتبّع موحّد لحالات تسجيل الدخول (نجاح/فشل) عبر كل القنوات.

مصدران للبيانات يُدمجان في خط زمني واحد:

  • تسجيل دخول الويب (لوحة الإدارة + بوابة المشترك + بوابة الكروت)
    يُسجَّل في audit_log بأفعال auth_login / auth_login_failed.
    RadiusAuditService يلتقط IP + User-Agent تلقائيًا من الطلب، فنشتق منهما
    نظام التشغيل والمتصفح ونوع الجهاز.

  • مصادقة الشبكة (RADIUS) للمشتركين والكروت من radpostauth (Access-Accept/Reject)
    مع جهاز الشبكة (NAS) وسبب الرفض (class).

كله read-only و tenant-scoped. record_login_event() محصّنة — لا تكسر الدخول أبدًا.
"""
from __future__ import annotations

from typing import Any

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.helpers import json_load
from .audit import get_audit_service

WEB_LOGIN_ACTIONS = ("auth_login", "auth_login_failed")

ACTOR_LABELS = {"admin": "مدير", "subscriber": "مشترك", "card": "كرت"}
SOURCE_LABELS = {"panel": "لوحة الإدارة", "portal": "بوابة المشتركين", "network": "شبكة المصادقة"}

# ترجمة أسباب رفض RADIUS الشائعة (من policy_engine._MSG)
REASON_LABELS = {
    "expired": "اشتراك منتهٍ",
    "disabled": "حساب معطّل",
    "no_plan": "بدون باقة",
    "out_of_schedule": "خارج وقت السماح",
    "mac_locked": "عنوان الجهاز غير مطابق",
    "mac_mismatch": "عنوان الجهاز غير مطابق",
    "random_mac_blocked": "عنوان MAC عشوائي/خاص ممنوع",
    "quota_exceeded": "تجاوز الحصة",
    "concurrency": "تجاوز عدد الأجهزة",
    "bad_password": "كلمة مرور خاطئة",
    "unknown_user": "مستخدم غير معروف",
    "not_found": "غير موجود",
}


# ───────────────────────── التسجيل ─────────────────────────

def record_login_event(*, actor_type: str, username: str, success: bool,
                        reason: str = "", actor_id: Any = None,
                        tenant_id: int | None = None) -> None:
    """يسجّل محاولة دخول ويب (لوحة/بوابة). محصّنة بالكامل — لا ترمي استثناءات."""
    try:
        if tenant_id is not None:
            try:
                from flask import g
                g.tenant_id = int(tenant_id)
            except Exception:
                pass
        get_audit_service().record(
            actor=(username or "unknown"),
            action="auth_login" if success else "auth_login_failed",
            target_type=actor_type,
            target_id=str(actor_id or username or ""),
            result_status="success" if success else "failed",
            severity="info" if success else "warning",
            error_message="" if success else (reason or ""),
            payload={"kind": "login_event", "actor_type": actor_type, "reason": reason or ""},
        )
    except Exception:
        pass


# ───────────────────────── محلّل User-Agent ─────────────────────────

def parse_user_agent(ua: str) -> tuple[str, str, str]:
    """يرجّع (نظام التشغيل، المتصفح، نوع الجهاز) — بدون مكتبات خارجية."""
    s = ua or ""
    low = s.lower()
    if not s.strip():
        return "", "", ""

    if "windows" in low:
        os_name = "ويندوز"
    elif "android" in low:
        os_name = "أندرويد"
    elif "iphone" in low or "ipad" in low or "ipod" in low:
        os_name = "آيفون/آيباد"
    elif "mac os x" in low or "macintosh" in low:
        os_name = "ماك"
    elif "cros" in low:
        os_name = "كروم أو إس"
    elif "linux" in low:
        os_name = "لينكس"
    else:
        os_name = "غير معروف"

    if "edg" in low:
        browser = "إيدج"
    elif "opr/" in low or "opera" in low:
        browser = "أوبرا"
    elif "samsungbrowser" in low:
        browser = "سامسونج إنترنت"
    elif "firefox/" in low or "fxios" in low:
        browser = "فايرفوكس"
    elif "chrome/" in low or "crios" in low:
        browser = "كروم"
    elif "safari/" in low and "version/" in low:
        browser = "سفاري"
    elif "curl" in low or "wget" in low or "python-requests" in low or "httpx" in low:
        browser = "أداة/سكربت"
    else:
        browser = "غير معروف"

    if "ipad" in low or ("tablet" in low and "mobile" not in low):
        device = "جهاز لوحي"
    elif "mobi" in low or "iphone" in low or "android" in low:
        device = "موبايل"
    else:
        device = "حاسوب"
    return os_name, browser, device


# ───────────────────────── القراءة الموحّدة ─────────────────────────

def _card_username_set(conn, tid: int) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT username FROM cards WHERE tenant_id = ?", (tid,)
        ).fetchall()
        return {str(r["username"]) for r in rows}
    except Exception:
        return set()


def _card_mac_map(conn, tid: int) -> dict[str, str]:
    try:
        rows = conn.execute(
            "SELECT username, used_by_mac FROM cards WHERE tenant_id = ? AND used_by_mac != ''",
            (tid,),
        ).fetchall()
        return {str(r["username"]): str(r["used_by_mac"]) for r in rows}
    except Exception:
        return {}


def _bound(dt_from: str, dt_to: str) -> tuple[str, str]:
    df = (dt_from or "").strip()
    dt = (dt_to or "").strip()
    return (f"{df} 00:00:00" if df else ""), (f"{dt} 23:59:59" if dt else "")


def _collect_rows(tenant_id: int, *, actor: str = "", source: str = "",
                  date_from: str = "", date_to: str = "") -> list[dict]:
    """يجمع الصفوف الخام من المصدرين مع دفع فلتر «الفاعل» إلى مستوى الاستعلام:

      • actor=admin     → استعلام الويب فقط مقيّدًا بـ target_type='admin'
                          (شبكة المصادقة لا تنتج مدراء أصلًا فتُتخطّى كليًا).
      • actor=subscriber → الويب مقيّد بـ target_type='subscriber'، والشبكة
                          مقيّدة بـ username NOT IN (أسماء الكروت).
      • actor=card      → الويب مقيّد بـ target_type='card'، والشبكة مقيّدة
                          بـ username IN (أسماء الكروت).

    هكذا الفرز دقيق من قاعدة البيانات نفسها — لا اعتماد على غربلة لاحقة.
    """
    conn = db()
    lo, hi = _bound(date_from, date_to)
    rows: list[dict] = []

    # 1) دخول الويب (لوحة + بوابة) من audit_log
    if source in ("", "panel", "portal"):
        sql = (
            "SELECT id, actor, target_type, target_id, result_status, error_message, "
            "ip_address, user_agent, created_at, payload_json FROM audit_log "
            "WHERE tenant_id = ? AND action IN ('auth_login','auth_login_failed')"
        )
        vals: list = [tenant_id]
        # فلتر الفاعل على مستوى SQL — target_type يحمل نوع الفاعل عند التسجيل
        if actor in ("admin", "subscriber", "card"):
            sql += " AND target_type = ?"; vals.append(actor)
        if lo:
            sql += " AND created_at >= ?"; vals.append(lo)
        if hi:
            sql += " AND created_at <= ?"; vals.append(hi)
        sql += " ORDER BY id DESC LIMIT 2000"
        for r in conn.execute(sql, vals).fetchall():
            at = (r["target_type"] or "subscriber")
            src = "panel" if at == "admin" else "portal"
            os_name, browser, device = parse_user_agent(r["user_agent"] or "")
            payload = json_load(r["payload_json"], default={}) or {}
            reason_code = payload.get("reason") or r["error_message"] or ""
            rows.append({
                "when": r["created_at"] or "",
                "actor_type": at,
                "username": r["actor"] or r["target_id"] or "—",
                "success": (r["result_status"] == "success"),
                "reason": REASON_LABELS.get(reason_code, reason_code),
                "reason_code": reason_code,
                "ip": r["ip_address"] or "",
                "mac": "",
                "nas": "",
                "os": os_name, "browser": browser, "device": device,
                "source": src,
            })

    # 2) مصادقة الشبكة (RADIUS) من radpostauth — لا تنتج مدراء فتُتخطّى عند actor=admin
    if source in ("", "network") and actor != "admin":
        sql = ("SELECT id, username, reply, class, nas, authdate FROM radpostauth "
               "WHERE tenant_id = ?")
        vals = [tenant_id]
        # فلتر الفاعل على مستوى SQL — التصنيف مشترك/كرت عبر جدول الكروت نفسه
        if actor == "card":
            sql += " AND username IN (SELECT username FROM cards WHERE tenant_id = ?)"
            vals.append(tenant_id)
        elif actor == "subscriber":
            sql += " AND username NOT IN (SELECT username FROM cards WHERE tenant_id = ?)"
            vals.append(tenant_id)
        if lo:
            sql += " AND authdate >= ?"; vals.append(lo)
        if hi:
            sql += " AND authdate <= ?"; vals.append(hi)
        sql += " ORDER BY id DESC LIMIT 2000"
        net = conn.execute(sql, vals).fetchall()
        if net:
            card_set = _card_username_set(conn, tenant_id)
            card_mac = _card_mac_map(conn, tenant_id)
            for r in net:
                uname = str(r["username"] or "")
                at = "card" if uname in card_set else "subscriber"
                reason_code = (r["class"] or "")
                rows.append({
                    "when": r["authdate"] or "",
                    "actor_type": at,
                    "username": uname or "—",
                    "success": (r["reply"] == "Access-Accept"),
                    "reason": REASON_LABELS.get(reason_code, reason_code),
                    "reason_code": reason_code,
                    "ip": "",
                    "mac": card_mac.get(uname, ""),
                    "nas": r["nas"] or "",
                    "os": "", "browser": "", "device": "",
                    "source": "network",
                })

    # السبب يعني فقط للمحاولات الفاشلة
    for r in rows:
        if r["success"]:
            r["reason"] = ""
            r["reason_code"] = ""
    return rows


def _today_prefix() -> str:
    """بادئة تاريخ اليوم (UTC) لمطابقة الطوابع الزمنية المخزّنة نصيًا."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fetch_login_events(tenant_id: int, *, actor: str = "", result: str = "",
                       source: str = "", q: str = "",
                       date_from: str = "", date_to: str = "",
                       limit: int = 600) -> dict:
    """يبني خط زمني موحّد لحالات الدخول + إحصاءات. يرجّع dict فيه rows + stats."""
    rows = _collect_rows(tenant_id, actor=actor, source=source,
                         date_from=date_from, date_to=date_to)
    ql = q.strip().lower()

    # فلاتر بايثون المتبقية (النتيجة / البحث) — الفاعل صار على مستوى SQL
    def _keep(row: dict) -> bool:
        if actor in ("admin", "subscriber", "card") and row["actor_type"] != actor:
            return False  # صمّام أمان إضافي فوق فلتر SQL
        if result == "success" and not row["success"]:
            return False
        if result == "fail" and row["success"]:
            return False
        if ql:
            blob = f"{row['username']} {row['ip']} {row['mac']} {row['nas']}".lower()
            if ql not in blob:
                return False
        return True

    rows = [r for r in rows if _keep(r)]
    rows.sort(key=lambda r: r["when"], reverse=True)
    total_matched = len(rows)

    # الإحصاءات تُحسب على كامل المطابق (قبل القصّ) — أرقام دقيقة لا «أرقام المعروض»
    today = _today_prefix()
    stats = {
        "total": total_matched,
        "ok": sum(1 for r in rows if r["success"]),
        "fail": sum(1 for r in rows if not r["success"]),
        "admins": sum(1 for r in rows if r["actor_type"] == "admin"),
        "subs": sum(1 for r in rows if r["actor_type"] == "subscriber"),
        "cards": sum(1 for r in rows if r["actor_type"] == "card"),
        "ips": len({r["ip"] for r in rows if r["ip"]}),
        "today": sum(1 for r in rows if (r["when"] or "").startswith(today)),
        "today_ok": sum(1 for r in rows if r["success"] and (r["when"] or "").startswith(today)),
        "today_fail": sum(1 for r in rows if (not r["success"]) and (r["when"] or "").startswith(today)),
        "uniq_users": len({r["username"] for r in rows if r["username"] and r["username"] != "—"}),
    }
    rows = rows[:limit]
    return {"rows": rows, "stats": stats, "shown": len(rows), "matched": total_matched}


def login_states_overview(tenant_id: int) -> dict:
    """ملخّص الصفحة الرئيسية لحالات الدخول: عدّادات مصغّرة لكل نوع فاعل.

    يرجّع dict بالشكل:
        {'admin': {'total':…, 'ok':…, 'fail':…, 'today':…}, 'subscriber': {…}, 'card': {…}}
    """
    rows = _collect_rows(tenant_id)
    today = _today_prefix()
    out = {k: {"total": 0, "ok": 0, "fail": 0, "today": 0} for k in ("admin", "subscriber", "card")}
    for r in rows:
        bucket = out.get(r["actor_type"])
        if bucket is None:
            continue
        bucket["total"] += 1
        if r["success"]:
            bucket["ok"] += 1
        else:
            bucket["fail"] += 1
        if (r["when"] or "").startswith(today):
            bucket["today"] += 1
    return out
