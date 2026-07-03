# -*- coding: utf-8 -*-
"""policy_reconciler — «أي عملية حفظ يصير إعادة مطابقة وأي شي مش مطابق طرد».

الخطّاف المركزيّ الواحد لإنفاذ تغييرات قواعد الوصول على الجلسات الحيّة فورًا:
بعد أيّ حفظ يغيّر القواعد (تعطيل مشترك/بطاقة، تعديل عرض — أيام/ساعات الدوام،
الكوتا، حدّ الأجهزة، قفل MAC، حظر «التحكم بالدخول»…) نُعيد فحص الجلسات النشطة
في النطاق المتأثّر بنفس فحوصات مسار التفويض (policy_engine)، ونُرسل RADIUS
Disconnect (PoD) لكلّ جلسة صارت مخالفة — بدل انتظار إعادة المصادقة التالية
(«عملت تعطيل ليوزر ما أرسل أمر قطع، ظل متصل»).

العقد:
  • لا يرمي أبدًا نحو مسار الحفظ — أيّ فشل يُسجَّل ويُبتلع.
  • راوتر غير قابل للوصول / لا جلسات = نجاح صامت.
  • مقيَّد: سقف جلسات لكلّ استدعاء + مهلة PoD القصيرة القائمة في radius_coa.
  • ``background=True`` (الافتراضيّ في مواقع الحفظ) ينفّذ في خيط خلفيّ فلا
    يُبطئ استجابة الحفظ؛ الاختبارات تستدعي بـ``background=False``.

الفحوصات المُعاد تشغيلها (قراءة فقط — نفس دوالّ policy_engine):
  الحظر (_check_blocks)، الحالة (_check_status)، الانتهاء، جدول الدوام
  (_check_schedule: أيام/ساعات الباقة + جدول المشترك)، الكوتا (_check_quota)،
  رصيد وقت البطاقة، حدود وقت الاتصال، قفل MAC (_check_mac)، وحدّ الأجهزة
  (device_limit.effective_limit — تُطرد الجلسات الأحدث الزائدة عن الحدّ).
تُتخطّى عمدًا: كلمة المرور (الجلسة مصادَق عليها سلفًا)، random-MAC/allow-mode/
anti-mac-clone (تحتاج سياق طلب حقيقيًّا وقد تكتب حالة)، وسقوف التزامن/المزوّد
(فحوصات سعة عند القبول لا أسباب طرد).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Iterable, Optional

_LOG = logging.getLogger(__name__)

# سقف أمان لكل استدعاء — يمنع عاصفة PoD على حفظٍ يمسّ آلاف الجلسات.
_MAX_SESSIONS_PER_RUN = 500


# ── جمع الجلسات النشطة في النطاق ─────────────────────────────────────


def _live_rows(tenant_id: int, *, usernames: Optional[Iterable[str]] = None,
               plan_id: Optional[int] = None,
               batch_id: Optional[int] = None,
               group_id: Optional[int] = None) -> list[dict]:
    """صفوف radacct المفتوحة الحيّة (ضمن نافذة الحياة) في النطاق المطلوب.

    النطاق: أسماء محدّدة، أو مستخدمو عرضٍ بعينه (مشتركين + بطاقات)، أو بطاقات
    حزمة، أو أعضاء مجموعة، أو كلّ المستأجر. تُرشَّح بالوقت في بايثون
    (_is_live) كنمط live_sessions."""
    from ..db.connection import db
    from . import live_sessions as ls

    where = "r.tenant_id=? AND (r.acctstoptime IS NULL OR r.acctstoptime='')"
    args: list[Any] = [int(tenant_id)]
    names = sorted({str(u).strip() for u in (usernames or []) if str(u or "").strip()})
    if not names and group_id is not None:
        # أعضاء المجموعة كأسماء صريحة (حفظ مجموعة/تعليق نطاقه مجموعة).
        try:
            from ..db.repos import subscriber_groups_repo
            names = sorted({
                str(u).strip() for u in
                subscriber_groups_repo.list_member_usernames(
                    int(tenant_id), int(group_id))
                if str(u or "").strip()})
        except Exception:  # noqa: BLE001
            names = []
        if not names:
            return []
    if names:
        ph = ",".join("?" * len(names))
        where += f" AND r.username IN ({ph})"
        args += names
    elif batch_id is not None:
        # بطاقات هذه الحزمة (حفظ دفعة: حدّ أجهزة/كوتا/أعلام سلوك…).
        where += (" AND r.username IN (SELECT username FROM cards "
                  "WHERE tenant_id=? AND batch_id=?)")
        args += [int(tenant_id), int(batch_id)]
    elif plan_id is not None:
        # مستخدمو هذا العرض: مشتركون مباشرة + بطاقات (عبر جدول cards).
        where += (" AND (r.username IN (SELECT username FROM subscribers "
                  "WHERE tenant_id=? AND plan_id=?) "
                  "OR r.username IN (SELECT username FROM cards "
                  "WHERE tenant_id=? AND plan_id=?))")
        args += [int(tenant_id), int(plan_id), int(tenant_id), int(plan_id)]
    rows = db().execute(
        "SELECT r.username, r.acctsessionid, r.nasipaddress, "
        "       r.callingstationid, r.acctstarttime, r.acctupdatetime "
        "  FROM radacct r WHERE " + where,
        args,
    ).fetchall()
    cutoff = ls._cutoff_dt(None)
    out = [dict(r) for r in rows if ls._is_live(dict(r), cutoff)]
    if len(out) > _MAX_SESSIONS_PER_RUN:
        _LOG.warning(
            "policy_reconciler: scope has %d live sessions — capping at %d "
            "(the rest are enforced at next re-auth)",
            len(out), _MAX_SESSIONS_PER_RUN)
        out = out[:_MAX_SESSIONS_PER_RUN]
    return out


# ── فحص امتثال جلسة واحدة ────────────────────────────────────────────


def _resolve(tenant_id: int, username: str):
    """(sub, plan, source) بنفس مسار authorize — أو (None, None, '') لمجهول."""
    from ..db.repos import cards_repo, plans_repo, subscribers_repo
    from . import policy_engine as pe

    sub = subscribers_repo.get_subscriber(int(tenant_id), username)
    source = "subscriber"
    if sub and (sub.user_type == pe.USER_TYPE_CARD or sub.card_batch_id):
        card = cards_repo.get_card_by_username(int(tenant_id), username)
        if card:
            sub = pe._card_to_subscriber(card)
            source = "card"
    elif not sub:
        card = cards_repo.get_card_by_username(int(tenant_id), username)
        if card:
            sub = pe._card_to_subscriber(card)
            source = "card"
    if not sub:
        return None, None, ""
    plan = None
    if sub.plan_id:
        try:
            plan = plans_repo.get_plan(int(tenant_id), sub.plan_id)
        except Exception:  # noqa: BLE001
            plan = None
    return sub, plan, source


def check_session_compliance(tenant_id: int, row: dict,
                             now: Optional[datetime] = None) -> Optional[str]:
    """سبب المخالفة لهذه الجلسة الحيّة وفق القواعد الحالية، أو None لممتثلة.

    يعيد تشغيل نفس فحوصات policy_engine القرائية على sub/plan الحاليّين مع
    طلبٍ اصطناعيّ من بيانات الجلسة (بلا كلمة مرور — الجلسة مصادَق عليها).
    ملاحظة: مستخدم لم يعد موجودًا (حُذف) = مخالفة "user_deleted"."""
    from . import policy_engine as pe

    now = now or datetime.utcnow()
    username = str(row.get("username") or "").strip()
    if not username:
        return None
    sub, plan, source = _resolve(int(tenant_id), username)
    if sub is None:
        return "user_deleted"
    req = pe.AuthRequest(
        username=username,
        tenant_id=int(tenant_id),
        nas_ip=str(row.get("nasipaddress") or ""),
        calling_station_id=str(row.get("callingstationid") or ""),
    )
    checks = (
        ("blocks", lambda: pe._check_blocks(sub, plan, req, source, now)),
        ("status", lambda: pe._check_status(sub)),
        ("expiry", lambda: pe._check_expiry_captive(sub)),
        ("schedule", lambda: pe._check_schedule(sub, plan, now)),
        ("quota", lambda: pe._check_quota(sub, plan)),
        ("card_time", lambda: pe._check_card_time_budget(sub, plan)),
        ("conn_time", lambda: pe._check_connection_time(sub, plan, req)),
        ("mac", lambda: pe._check_mac(sub, req)),
    )
    for label, fn in checks:
        try:
            bad = fn()
        except Exception:  # noqa: BLE001 — فحص واحد يفشل ≠ طرد ولا انهيار
            _LOG.warning("policy_reconciler: check %s failed for %r",
                         label, username, exc_info=True)
            continue
        if bad is None:
            continue
        # ‏_check_expiry_captive قد يعيد قبولًا مبكرًا (ok=True) لمنتهٍ يُحال
        # لبوّابة التجديد — جلسته الحيّة صارت غير ممتثلة أيضًا: نطرده فيُعاد
        # توجيهه عند إعادة المصادقة إلى الحديقة المسوّرة (السلوك المقصود).
        return bad.reason or label
    return None


def _device_limit_violations(tenant_id: int, rows: list[dict]) -> dict[str, str]:
    """جلسات تتجاوز حدّ الأجهزة الفعّال: تُبقى الأقدم ضمن الحدّ وتُطرد الأحدث.
    يعيد {acctsessionid: reason}."""
    from . import device_limit as dl
    from . import live_sessions as ls

    out: dict[str, str] = {}
    by_user: dict[str, list[dict]] = {}
    for r in rows:
        u = str(r.get("username") or "").strip()
        if u:
            by_user.setdefault(u, []).append(r)
    for username, sess in by_user.items():
        try:
            sub, plan, _src = _resolve(int(tenant_id), username)
            if sub is None:
                continue
            # effective_limit → (الحدّ، mac_aware)؛ صفر = بلا حدّ. للطرد نعدّ
            # الجلسات الحيّة خامًا (كلّ جلسة زائدة عن الحدّ مخالفة، بصرف النظر
            # عن وعي الـMAC — ذاك يخصّ عدّ «الأجهزة» عند القبول).
            limit, _mac_aware = dl.effective_limit(sub, plan)
            if int(limit) <= 0 or len(sess) <= int(limit):
                continue
            # الأقدم يبقى — رتّب بآخر دليل حياة تصاعديًّا واطرد ما بعد الحدّ.
            sess_sorted = sorted(
                sess, key=lambda r: (ls._row_last_dt(r) or datetime.min))
            for r in sess_sorted[int(limit):]:
                sid = str(r.get("acctsessionid") or "")
                if sid:
                    out[sid] = "device_limit_exceeded"
        except Exception:  # noqa: BLE001
            _LOG.warning("policy_reconciler: device-limit check failed for %r",
                         username, exc_info=True)
    return out


# ── التنفيذ ──────────────────────────────────────────────────────────


def _run(tenant_id: int, *, usernames=None, plan_id=None, batch_id=None,
         group_id=None, reason: str = "save") -> dict:
    from . import live_session_control as lsc

    stats = {"checked": 0, "violations": 0, "disconnected": 0, "failed": 0}
    rows = _live_rows(int(tenant_id), usernames=usernames, plan_id=plan_id,
                      batch_id=batch_id, group_id=group_id)
    # مخالفات حدّ الأجهزة تُحسب على مستوى المستخدم (تحتاج كلّ جلساته معًا).
    dl_viol = _device_limit_violations(int(tenant_id), rows)
    now = datetime.utcnow()
    for row in rows:
        stats["checked"] += 1
        sid = str(row.get("acctsessionid") or "")
        why = dl_viol.get(sid) or check_session_compliance(
            int(tenant_id), row, now)
        if not why:
            continue
        stats["violations"] += 1
        username = str(row.get("username") or "")
        try:
            outcome = lsc.disconnect_live(
                tenant_id=int(tenant_id), username=username, session_id=sid)
            ok = bool(getattr(outcome, "ok", False))
        except Exception:  # noqa: BLE001 — NAS غير قابل للوصول/خطأ شبكة
            ok = False
        if ok:
            stats["disconnected"] += 1
            _LOG.info(
                "policy_reconciler[%s]: disconnected %r session=%s reason=%s",
                reason, username, sid, why)
        else:
            stats["failed"] += 1
            _LOG.warning(
                "policy_reconciler[%s]: PoD failed/undeliverable for %r "
                "session=%s reason=%s (سيُرفض عند إعادة المصادقة)",
                reason, username, sid, why)
    return stats


def reconcile_active_sessions_against_policy(
        tenant_id: int, *,
        usernames: Optional[Iterable[str]] = None,
        plan_id: Optional[int] = None,
        batch_id: Optional[int] = None,
        group_id: Optional[int] = None,
        reason: str = "save",
        background: bool = True) -> Optional[dict]:
    """الخطّاف المركزيّ — يُستدعى بعد أيّ حفظ يغيّر قواعد الوصول.

    النطاق: ``usernames`` (حفظ مشترك/بطاقة/تبديل حالة)، أو ``plan_id`` (حفظ
    عرض)، أو ``batch_id`` (حفظ دفعة بطاقات)، أو ``group_id`` (مجموعة)، أو لا
    شيء = كلّ جلسات المستأجر الحيّة (حظر عامّ). لا يرمي أبدًا.
    ``background=True`` ينفّذ في خيط خلفيّ ويعيد None فورًا؛ ``False`` ينفّذ
    متزامنًا ويعيد الإحصاءات (للاختبارات/الاستدعاء الصريح)."""
    names = list(usernames) if usernames else None
    if not background:
        try:
            return _run(int(tenant_id), usernames=names, plan_id=plan_id,
                        batch_id=batch_id, group_id=group_id, reason=reason)
        except Exception:  # noqa: BLE001 — الحفظ لا يفشل بسبب الإنفاذ
            _LOG.exception("policy_reconciler[%s]: run failed", reason)
            return None

    def _bg():
        try:
            _run(int(tenant_id), usernames=names, plan_id=plan_id,
                 batch_id=batch_id, group_id=group_id, reason=reason)
        except Exception:  # noqa: BLE001
            _LOG.exception("policy_reconciler[%s]: background run failed",
                           reason)

    try:
        threading.Thread(
            target=_bg, name=f"policy-reconcile-{reason}", daemon=True,
        ).start()
    except Exception:  # noqa: BLE001
        _LOG.exception("policy_reconciler[%s]: could not spawn thread", reason)
    return None
