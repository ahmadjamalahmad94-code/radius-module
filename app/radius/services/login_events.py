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
from app.i18n_text import N_

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


ACTOR_LABELS = {"admin": N_("مدير"), "subscriber": N_("مشترك"), "card": N_("كرت")}
SOURCE_LABELS = {"panel": N_("لوحة الإدارة"), "portal": N_("بوابة المشتركين"), "network": N_("شبكة المصادقة")}

# ───────────── خريطة أسباب رفض الدخول → عربي (مصدر موحّد) ─────────────
# المصدر الأساسي للرموز: policy_engine._reject(reason) الذي يكتب الرمز في
# عمود radpostauth.class عبر _log_attempt. نُغطّي رموز policy_engine._MSG
# الحالية + المرادفات/الرموز التاريخية التي قد تبقى في صفوف قديمة، كي لا
# يظهر أي كود إنجليزي خام في عمود «السبب». أي رمز غير معروف → تأنيس عربي
# آمن عبر reason_label() (لا snake_case خام أبدًا).
REASON_LABELS = {
    # رموز policy_engine._MSG (مسار المصادقة الحالي)
    "user_not_found":     N_("اسم المستخدم غير موجود"),
    "password_wrong":     N_("كلمة المرور غير صحيحة"),
    "disabled":           N_("الحساب معطَّل"),
    "expired":            N_("انتهت صلاحية الاشتراك"),
    "outside_hours":      N_("خارج أوقات الدوام المسموحة"),
    "outside_days":       N_("خارج أيام الدوام المسموحة"),
    "quota_exhausted":    N_("نفدت الكوتا — يلزم تجديد"),
    "mac_mismatch":       N_("عنوان الجهاز (MAC) غير مطابق"),
    "random_mac_blocked": N_("عنوان MAC عشوائي/خاص ممنوع"),
    "concurrent_limit":   N_("تجاوز الحد الأقصى للجلسات المتزامنة"),
    # رموز policy_engine الإضافية (كانت تظهر إنجليزيّة خامًا في «السبب»)
    "time_daily_exhausted": N_("انتهى الوقت اليومي"),
    "time_total_exhausted": N_("انتهى إجمالي الوقت المسموح"),
    "card_time_exhausted":  N_("انتهى وقت البطاقة"),
    "provider_active_cap":  N_("بلغ سقف الجلسات المتزامنة للمزوّد"),
    "access_blocked":       N_("الوصول محظور"),
    "access_suspended":     N_("الحساب موقوف"),
    "mac_clone_detected":   N_("اكتشاف تكرار عنوان الجهاز (استنساخ MAC)"),
    "stepup_required":      N_("مطلوب تحقّق إضافيّ"),
    "allow_mode_at_capacity":    N_("بلغ السعة القصوى (وضع السماح)"),
    "allow_mode_bind_failed":    N_("تعذّر ربط الجهاز (وضع السماح)"),
    "allow_mode_unknown_device": N_("جهاز غير معروف (وضع السماح)"),
    # مرادفات/رموز تاريخية قد تبقى في صفوف radpostauth قديمة
    "bad_password":       N_("كلمة مرور خاطئة"),
    "password_mismatch":  N_("كلمة المرور غير صحيحة"),
    "unknown_user":       N_("مستخدم غير معروف"),
    "user_mismatch":      N_("عدم تطابق المستخدم"),
    "not_found":          N_("غير موجود"),
    "account_disabled":   N_("حساب موقوف"),
    "account_expired":    N_("انتهت صلاحية الحساب"),
    "no_plan":            N_("بدون باقة"),
    "out_of_schedule":    N_("خارج وقت السماح"),
    "outside_schedule":   N_("خارج وقت السماح"),
    "out_of_window":      N_("خارج وقت السماح"),
    "mac_locked":         N_("عنوان الجهاز غير مطابق"),
    "quota_exceeded":     N_("تجاوز الحصة"),
    "concurrency":        N_("تجاوز عدد الأجهزة المتزامنة"),
    "concurrent":         N_("تجاوز عدد الأجهزة المتزامنة"),
    "no_active":          N_("لا جلسة نشطة"),
    "no_active_session":  N_("لا جلسة نشطة"),
    "shared_blocked":     N_("مشاركة الحساب ممنوعة"),
    "reject":             N_("مرفوض"),
    "rejected":           N_("مرفوض"),
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
        os_name = N_("ويندوز")
    elif "android" in low:
        os_name = N_("أندرويد")
    elif "iphone" in low or "ipad" in low or "ipod" in low:
        os_name = N_("آيفون/آيباد")
    elif "mac os x" in low or "macintosh" in low:
        os_name = N_("ماك")
    elif "cros" in low:
        os_name = N_("كروم أو إس")
    elif "linux" in low:
        os_name = N_("لينكس")
    else:
        os_name = N_("غير معروف")

    if "edg" in low:
        browser = N_("إيدج")
    elif "opr/" in low or "opera" in low:
        browser = N_("أوبرا")
    elif "samsungbrowser" in low:
        browser = N_("سامسونج إنترنت")
    elif "firefox/" in low or "fxios" in low:
        browser = N_("فايرفوكس")
    elif "chrome/" in low or "crios" in low:
        browser = N_("كروم")
    elif "safari/" in low and "version/" in low:
        browser = N_("سفاري")
    elif "curl" in low or "wget" in low or "python-requests" in low or "httpx" in low:
        browser = N_("أداة/سكربت")
    else:
        browser = N_("غير معروف")

    if "ipad" in low or ("tablet" in low and "mobile" not in low):
        device = N_("جهاز لوحي")
    elif "mobi" in low or "iphone" in low or "android" in low:
        device = N_("موبايل")
    else:
        device = N_("حاسوب")
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
    # zero-w2: يوم المشغّل المحلّيّ (فلسطين) شاملًا عبر report_dates — كان
    # _bound يقارن نصّيًّا على يوم UTC ('… 23:59:59'): طابع ISO بـ'T' يخرج من
    # «to» ويوم «اليوم» يقطع عند منتصف ليل UTC. قيمة غير صالحة → بلا فلترة
    # (كصفحات تقارير الويب؛ الـAPI يرفضها 422 قبل الوصول هنا).
    from .report_dates import ReportDateError, local_bounds, range_sql
    try:
        lo, hi, hi_excl = local_bounds(date_from, date_to, tenant_id)
    except ReportDateError:
        lo, hi, hi_excl = None, None, True
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
        rw, rp = range_sql("created_at", lo, hi, hi_excl)
        sql += "".join(f" AND {w}" for w in rw); vals.extend(rp)
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
        sql = ("SELECT id, username, reply, class, nas, authdate, pass, "
               "calling_station FROM radpostauth WHERE tenant_id = ?")
        vals = [tenant_id]
        # فلتر الفاعل على مستوى SQL — التصنيف مشترك/كرت عبر جدول الكروت نفسه
        if actor == "card":
            sql += " AND username IN (SELECT username FROM cards WHERE tenant_id = ?)"
            vals.append(tenant_id)
        elif actor == "subscriber":
            sql += " AND username NOT IN (SELECT username FROM cards WHERE tenant_id = ?)"
            vals.append(tenant_id)
        rw, rp = range_sql("authdate", lo, hi, hi_excl)
        sql += "".join(f" AND {w}" for w in rw); vals.extend(rp)
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
                    # الماك المُحاوِل الحقيقيّ (Calling-Station-Id) أوّلًا — هو
                    # المطلوب لتشخيص «MAC غير مطابق» (نشوف الجهاز الذي حاول)؛
                    # وإلا نرجع لماك الكرت المُسجَّل، وإلا فارغ.
                    "mac": (str(r["calling_station"] or "").strip()
                            or card_mac.get(uname, "")),
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
    return _scoped_rows(tenant_id, rows)


def _scoped_rows(tenant_id: int, rows: list[dict]) -> list[dict]:
    """fix3 (F02 H2): a manager without «عرض كل المشتركين» sees the logins of
    HIS subscribers/cards and his own panel logins — not the network's."""
    from .subscriber_scope import (accessible_usernames, admin_actor_labels,
                                   current_scope_admin_id)
    scope = current_scope_admin_id(tenant_id=int(tenant_id))
    if scope is None:
        return rows
    mine = set(admin_actor_labels(int(scope)))
    allowed = accessible_usernames(int(scope), tenant_id=int(tenant_id))
    out = []
    for r in rows:
        name = str(r.get("username") or "")
        if r.get("actor_type") == "admin":
            if name in mine:
                out.append(r)
        elif name in allowed:
            out.append(r)
    return out


def _today_check(tenant_id: int | None = None):
    """دالّة: هل الطابع (UTC مخزَّن، بمسافة أو 'T') ضمن **اليوم المحلّيّ**؟
    zero-w2: كانت بادئة يوم UTC — «اليوم» يبدأ الثالثة فجرًا بتوقيت فلسطين."""
    from ..core.system_config import local_period_utc_range, local_today
    start, end = local_period_utc_range(
        "daily", local_today(tenant_id).isoformat(), tenant_id)

    def _is_today(when) -> bool:
        ts = str(when or "")[:19].replace("T", " ")
        return bool(ts) and start <= ts < end
    return _is_today


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
    # طابع مطبَّع للفرز: audit_log يكتب ‎…T…‎ وradpostauth بمسافة — الفرز
    # النصّيّ الخامّ يضع كل صفوف 'T' قبل صفوف اليوم نفسه بالمسافة.
    rows.sort(key=lambda r: str(r["when"] or "")[:19].replace("T", " "), reverse=True)
    total_matched = len(rows)

    # الإحصاءات تُحسب على كامل المطابق (قبل القصّ) — أرقام دقيقة لا «أرقام المعروض»
    is_today = _today_check(tenant_id)
    stats = {
        "total": total_matched,
        "ok": sum(1 for r in rows if r["success"]),
        "fail": sum(1 for r in rows if not r["success"]),
        "admins": sum(1 for r in rows if r["actor_type"] == "admin"),
        "subs": sum(1 for r in rows if r["actor_type"] == "subscriber"),
        "cards": sum(1 for r in rows if r["actor_type"] == "card"),
        "ips": len({r["ip"] for r in rows if r["ip"]}),
        "today": sum(1 for r in rows if is_today(r["when"])),
        "today_ok": sum(1 for r in rows if r["success"] and is_today(r["when"])),
        "today_fail": sum(1 for r in rows if (not r["success"]) and is_today(r["when"])),
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
    is_today = _today_check(tenant_id)
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
        if is_today(r["when"]):
            bucket["today"] += 1
    return out
