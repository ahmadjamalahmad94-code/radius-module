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

# ── كلمة المرور المُحاوَلة على المحاولات الفاشلة (تشخيص حسّاس) ──
# نلتقط النصّ الصريح للمحاولة الفاشلة فقط (لا للناجحة أبدًا)، ونعرضه للمدير
# الرئيسي فقط (بوّابة في القالب). الاحتفاظ قصير: بعد انقضاء المدّة لا نُظهر
# القيمة حتى لو بقيت مخزّنة (pw_status='expired').
#   • الويب (لوحة/بوابة/متجر): نخزّن النصّ في جدول login_attempt_passwords
#     الخاصّ (لا في payload — audit يُقنّع أي مفتاح "password").
#   • الشبكة (RADIUS): نُعيد استخدام radpostauth.pass — يحمل النصّ الصريح تحت
#     PAP، ويكون فارغًا تحت CHAP (هوتسبوت ميكروتك) فنعرض «غير متاح — تشفير CHAP».
PW_RETENTION_DAYS = 7


def _pw_cutoff_day() -> str:
    """بادئة تاريخ حدّ الاحتفاظ (YYYY-MM-DD). المقارنة على البادئة فقط لتكون
    مستقلّة عن صيغة الطابع (T...Z مقابل مسافة)."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            - timedelta(days=PW_RETENTION_DAYS)).strftime("%Y-%m-%d")


def _pw_fields(*, success: bool, source: str, raw: str, when: str,
               cutoff_day: str) -> dict:
    """يحدّد حقول «كلمة المرور المُحاوَلة» لصفّ واحد:
      • none    → لا نُظهر شيئًا (نجاح، أو لا قيمة).
      • shown   → لدينا نصّ المحاولة الفاشلة.
      • chap    → محاولة شبكة فاشلة بلا نصّ ⇒ تشفير CHAP غير قابل للاسترجاع.
      • expired → انقضت مدّة الاحتفاظ."""
    if success:
        return {"attempted_password": "", "pw_status": "none"}
    val = (raw or "").strip()
    if source == "network":
        if not val or val == "***":
            return {"attempted_password": "", "pw_status": "chap"}
        if when and when[:10] < cutoff_day:
            return {"attempted_password": "", "pw_status": "expired"}
        return {"attempted_password": val, "pw_status": "shown"}
    # الويب: النصّ من login_attempt_passwords إن وُجد.
    if not val:
        return {"attempted_password": "", "pw_status": "none"}
    if when and when[:10] < cutoff_day:
        return {"attempted_password": "", "pw_status": "expired"}
    return {"attempted_password": val, "pw_status": "shown"}


ACTOR_LABELS = {"admin": "مدير", "subscriber": "مشترك", "card": "كرت"}
SOURCE_LABELS = {"panel": "لوحة الإدارة", "portal": "بوابة المشتركين", "network": "شبكة المصادقة"}

# ───────────── خريطة أسباب رفض الدخول → عربي (مصدر موحّد) ─────────────
# المصدر الأساسي للرموز: policy_engine._reject(reason) الذي يكتب الرمز في
# عمود radpostauth.class عبر _log_attempt. نُغطّي رموز policy_engine._MSG
# الحالية + المرادفات/الرموز التاريخية التي قد تبقى في صفوف قديمة، كي لا
# يظهر أي كود إنجليزي خام في عمود «السبب». أي رمز غير معروف → تأنيس عربي
# آمن عبر reason_label() (لا snake_case خام أبدًا).
REASON_LABELS = {
    # رموز policy_engine._MSG (مسار المصادقة الحالي)
    "user_not_found":     "اسم المستخدم غير موجود",
    "password_wrong":     "كلمة المرور غير صحيحة",
    "disabled":           "الحساب معطَّل",
    "expired":            "انتهت صلاحية الاشتراك",
    "outside_hours":      "خارج أوقات الدوام المسموحة",
    "outside_days":       "خارج أيام الدوام المسموحة",
    "quota_exhausted":    "نفدت الكوتا — يلزم تجديد",
    "mac_mismatch":       "عنوان الجهاز (MAC) غير مطابق",
    "random_mac_blocked": "عنوان MAC عشوائي/خاص ممنوع",
    "concurrent_limit":   "تجاوز الحد الأقصى للجلسات المتزامنة",
    # رموز policy_engine الإضافية (كانت تظهر إنجليزيّة خامًا في «السبب»)
    "time_daily_exhausted": "انتهى الوقت اليومي",
    "time_total_exhausted": "انتهى إجمالي الوقت المسموح",
    "card_time_exhausted":  "انتهى وقت البطاقة",
    "provider_active_cap":  "بلغ سقف الجلسات المتزامنة للمزوّد",
    "access_blocked":       "الوصول محظور",
    "access_suspended":     "الحساب موقوف",
    "mac_clone_detected":   "اكتشاف تكرار عنوان الجهاز (استنساخ MAC)",
    "stepup_required":      "مطلوب تحقّق إضافيّ",
    "allow_mode_at_capacity":    "بلغ السعة القصوى (وضع السماح)",
    "allow_mode_bind_failed":    "تعذّر ربط الجهاز (وضع السماح)",
    "allow_mode_unknown_device": "جهاز غير معروف (وضع السماح)",
    # مرادفات/رموز تاريخية قد تبقى في صفوف radpostauth قديمة
    "bad_password":       "كلمة مرور خاطئة",
    "password_mismatch":  "كلمة المرور غير صحيحة",
    "unknown_user":       "مستخدم غير معروف",
    "user_mismatch":      "عدم تطابق المستخدم",
    "not_found":          "غير موجود",
    "account_disabled":   "حساب موقوف",
    "account_expired":    "انتهت صلاحية الحساب",
    "no_plan":            "بدون باقة",
    "out_of_schedule":    "خارج وقت السماح",
    "outside_schedule":   "خارج وقت السماح",
    "mac_locked":         "عنوان الجهاز غير مطابق",
    "quota_exceeded":     "تجاوز الحصة",
    "concurrency":        "تجاوز عدد الأجهزة المتزامنة",
    "concurrent":         "تجاوز عدد الأجهزة المتزامنة",
    "no_active":          "لا جلسة نشطة",
    "no_active_session":  "لا جلسة نشطة",
    "shared_blocked":     "مشاركة الحساب ممنوعة",
    "reject":             "مرفوض",
    "rejected":           "مرفوض",
}


# الرموز التي يكون فيها الرفض بسبب كلمة المرور نفسها — وحدها تُظهر رقاقة
# «كلمة المرور المُحاوَلة (خاطئة)». أي سبب آخر (معطَّل/منتهٍ/خارج الجدول/
# مستخدم غير موجود…) يحجب الرقاقة: الكلمة لم تُقيَّم فلا معنى لوصفها «خاطئة».
_PW_REASON_CODES = {
    "password_wrong", "bad_password", "password_mismatch", "wrong_password",
}


def reason_label(code: str | None) -> str:
    """التسمية العربية لرمز سبب الرفض. أي رمز غير معروف يُؤنَّس (يُستبدل
    «_» بمسافة) فلا يظهر snake_case إنجليزي خام في عمود «السبب»."""
    raw = (code or "").strip()
    if not raw:
        return ""
    hit = REASON_LABELS.get(raw) or REASON_LABELS.get(raw.lower())
    if hit:
        return hit
    return raw.replace("_", " ").strip()


# ───────────────────────── التسجيل ─────────────────────────

def record_login_event(*, actor_type: str, username: str, success: bool,
                        reason: str = "", actor_id: Any = None,
                        tenant_id: int | None = None,
                        attempted_password: str = "") -> None:
    """يسجّل محاولة دخول ويب (لوحة/بوابة). محصّنة بالكامل — لا ترمي استثناءات.

    ``attempted_password``: النصّ الصريح الذي حاوله المستخدم. يُخزَّن **فقط**
    عند الفشل (لا للناجحة أبدًا) في جدول login_attempt_passwords ليظهر لاحقًا
    للمدير الرئيسي في «حالات الدخول». تشخيص حسّاس — انظر PW_RETENTION_DAYS."""
    try:
        if tenant_id is not None:
            try:
                from flask import g
                g.tenant_id = int(tenant_id)
            except Exception:
                pass
        # لا نضع كلمة المرور في payload — audit_repo يُقنّع أي مفتاح "password".
        # نخزّن التشخيص الحسّاس في جدوله الخاصّ login_attempt_passwords.
        entry = get_audit_service().record(
            actor=(username or "unknown"),
            action="auth_login" if success else "auth_login_failed",
            target_type=actor_type,
            target_id=str(actor_id or username or ""),
            result_status="success" if success else "failed",
            severity="info" if success else "warning",
            error_message="" if success else (reason or ""),
            payload={"kind": "login_event", "actor_type": actor_type, "reason": reason or ""},
        )
        # للفاشلة فقط — لا نخزّن كلمة مرور صحيحة إطلاقًا.
        if (not success) and attempted_password:
            _store_attempt_password(tenant_id, getattr(entry, "id", None),
                                    username or "", attempted_password)
    except Exception:
        pass


def _store_attempt_password(tenant_id: int | None, audit_id: Any,
                            username: str, attempted_password: str) -> None:
    """يخزّن النصّ المُحاوَل لمحاولة ويب فاشلة في جدوله الخاصّ، ويكنس الأقدم من
    مدّة الاحتفاظ (تطهير انتهازي عند الكتابة). محصّن — لا يكسر الدخول أبدًا."""
    try:
        from datetime import datetime, timedelta, timezone

        from ..db.connection import transaction
        from ..db.helpers import now_iso
        tid = int(tenant_id) if tenant_id is not None else 1
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=PW_RETENTION_DAYS)).isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                "INSERT INTO login_attempt_passwords"
                "(tenant_id, audit_id, username, attempted_password, created_at)"
                " VALUES(?,?,?,?,?)",
                (tid, audit_id, username, attempted_password, now_iso()))
            conn.execute(
                "DELETE FROM login_attempt_passwords"
                " WHERE tenant_id = ? AND created_at < ?", (tid, cutoff))
    except Exception:  # noqa: BLE001
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


def _attempt_pw_map(conn, tid: int, audit_ids: list) -> dict:
    """يربط audit_id → النصّ المُحاوَل من login_attempt_passwords (الويب فقط).
    محصّن: لو الجدول غير موجود (قاعدة قديمة) يُرجِع خريطة فارغة."""
    ids = [i for i in audit_ids if i is not None]
    if not ids:
        return {}
    qmarks = ",".join("?" * len(ids))
    try:
        rows = conn.execute(
            f"SELECT audit_id, attempted_password FROM login_attempt_passwords "
            f"WHERE tenant_id = ? AND audit_id IN ({qmarks})",
            [tid, *ids]).fetchall()
        return {r["audit_id"]: (r["attempted_password"] or "") for r in rows}
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
    cutoff_day = _pw_cutoff_day()
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
        web_rows = conn.execute(sql, vals).fetchall()
        # النصّ المُحاوَل للفاشلة من الجدول الخاصّ، مربوطًا بـ audit_id.
        pw_map = _attempt_pw_map(conn, tenant_id, [r["id"] for r in web_rows])
        for r in web_rows:
            at = (r["target_type"] or "subscriber")
            src = "panel" if at == "admin" else "portal"
            os_name, browser, device = parse_user_agent(r["user_agent"] or "")
            payload = json_load(r["payload_json"], default={}) or {}
            reason_code = payload.get("reason") or r["error_message"] or ""
            _success = (r["result_status"] == "success")
            row = {
                "when": r["created_at"] or "",
                "actor_type": at,
                "username": r["actor"] or r["target_id"] or "—",
                "success": _success,
                "reason": reason_label(reason_code),
                "reason_code": reason_code,
                "ip": r["ip_address"] or "",
                "mac": "",
                "nas": "",
                "os": os_name, "browser": browser, "device": device,
                "source": src,
            }
            row.update(_pw_fields(
                success=_success, source=src, raw=pw_map.get(r["id"], ""),
                when=row["when"], cutoff_day=cutoff_day))
            rows.append(row)

    # 2) مصادقة الشبكة (RADIUS) من radpostauth — لا تنتج مدراء فتُتخطّى عند actor=admin
    if source in ("", "network") and actor != "admin":
        sql = ("SELECT id, username, reply, class, nas, authdate, pass "
               "FROM radpostauth WHERE tenant_id = ?")
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
                _success = (r["reply"] == "Access-Accept")
                # رفض شبكة بلا class: كل رفوض policy_engine تكتب رمزها في
                # class، فالرفض الصامت الوحيد هو فشل مطابقة كلمة المرور في
                # نواة FreeRADIUS (rlm_pap/CHAP) — سمّه صراحةً بدل «—»
                # (طلب المالك: «خطأ كلمة المرور يجي بالسبب»).
                if not _success and not reason_code.strip():
                    reason_code = "password_wrong"
                row = {
                    "when": r["authdate"] or "",
                    "actor_type": at,
                    "username": uname or "—",
                    "success": _success,
                    "reason": reason_label(reason_code),
                    "reason_code": reason_code,
                    "ip": "",
                    "mac": card_mac.get(uname, ""),
                    "nas": r["nas"] or "",
                    "os": "", "browser": "", "device": "",
                    "source": "network",
                }
                row.update(_pw_fields(
                    success=_success, source="network", raw=(r["pass"] or ""),
                    when=row["when"], cutoff_day=cutoff_day))
                rows.append(row)

    # السبب يعني فقط للمحاولات الفاشلة
    for r in rows:
        if r["success"]:
            r["reason"] = ""
            r["reason_code"] = ""
        else:
            # رقاقة «كلمة المرور المُحاوَلة (خاطئة)» تُعرض فقط حين يكون
            # الرفض بسبب كلمة المرور فعلًا. «الحساب معطَّل» (أو منتهٍ/خارج
            # الجدول…) مع «(خاطئة)» تناقُض: كلمة المرور لم تُقيَّم أصلًا —
            # الرفض وقع للسبب المذكور (طلب المالك). ويحجب أيضًا نصّ
            # المحاولة عن رفوض user_not_found (قد يكون كلمةَ مرور صحيحة
            # ليوزر آخر أخطأ صاحبُه بالاسم — تسريب لا داعي له).
            rc = (r.get("reason_code") or "").strip().lower()
            if rc not in _PW_REASON_CODES:
                r["attempted_password"] = ""
                r["pw_status"] = "none"
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
