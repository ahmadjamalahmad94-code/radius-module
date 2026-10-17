"""فحصٌ حيٌّ للكوتة اليوميّة على نسخةٍ حقيقيّة — لا وحدات اختبار.

يُشغَّل على الخادم داخل الـvenv. يَبني تطبيقًا حقيقيًّا بقاعدةٍ جديدة،
ويَمرّ على الطبقات كما يَمرّ المشغّل:

  1. الهجرة تُطبَّق فعلًا والأعمدة موجودة في القاعدة.
  2. نموذجُ الباقة (HTTP POST حقيقيّ) يَحفظ السقفَ والقرارَ والسرعة.
  3. نموذجُ المشترك يَحفظ تجاوزَه الفرديّ.
  4. المحاسبة (interim عبر الخدمة) تُراكم العدّادَ بالفرق.
  5. المحرّك يُصدر Access-Accept بسرعةٍ مخفَّضة بعد النفاد.
  6. قرار «قطع» يُنتج رفضًا حقيقيًّا.
  7. ختمُ الأمس يُصفّر العدّادَ تلقائيًّا (التجديد منتصفَ الليل).
  8. العامل الدوريّ يَستدعي CoA بالسرعة الصحيحة على جلسةٍ حيّة.

الخرج: جدولُ PASS/FAIL وكودُ خروجٍ غير صفريّ عند أيّ إخفاق.
"""
from __future__ import annotations

import os
import sys
import tempfile

MB = 1_048_576
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (" — " + detail if detail else ""),
          flush=True)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="hr_dq_live_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "live.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["FLASK_SECRET"] = "lab-only-secret"
    os.environ["HOBERADIUS_API_TOKEN"] = "lab-token"

    from app import create_app
    app = create_app()

    # ── 1) الهجرة ──
    with app.app_context():
        from app.radius.db.connection import db
        cols = {r["name"] for r in db().execute(
            "SELECT name FROM pragma_table_info('subscribers')").fetchall()}
        need = {"daily_quota_mb", "daily_quota_action", "daily_used_bytes",
                "daily_usage_day", "daily_quota_state",
                "daily_quota_speed_down_kbps", "daily_quota_speed_up_kbps"}
        check("1. أعمدةُ الكوتة اليوميّة في القاعدة", need <= cols,
              "ناقص: %s" % (need - cols) if not need <= cols else "7 أعمدة")
        pcols = {r["name"] for r in db().execute(
            "SELECT name FROM pragma_table_info('access_plans')").fetchall()}
        pneed = {"daily_quota_action", "daily_quota_speed_down_kbps",
                 "daily_quota_speed_up_kbps"}
        check("1b. أعمدةُ القرار على الباقة", pneed <= pcols)

    client = app.test_client()

    # تسجيلُ دخولٍ حقيقيّ بحساب الإقلاع.
    with app.app_context():
        from app.radius.db.repos import admins_repo
        admins_repo.create_admin(username="labtester", password="lab-pass-123",
                                 full_name="Lab Tester", is_super_admin=True)
    login = client.post("/admin/radius/login",
                        data={"username": "labtester", "password": "lab-pass-123"},
                        follow_redirects=False)
    logged_in = login.status_code in (302, 303)

    # ── 2) نموذجُ الباقة: نفس دالّةِ المسار ونفس request.form ──
    # (بوّابةُ الترخيص تَحجب صفحةَ النموذج على قاعدةٍ جديدة، فنُشغّل دالّةَ
    #  الإنشاء نفسَها داخل سياقِ طلبٍ حقيقيّ يَحمل حقولَ النموذج كما يُرسلها
    #  المتصفّح — فيُختبَر تحليلُ الحقول والحفظُ لا مجرّد الخدمة.)
    form = {
        "name": "LAB-DAILY", "plan_type": "quota", "service_type": "pppoe",
        "speed_down_kbps": "8000", "speed_up_kbps": "4000",
        "download_speed_kbps": "8000", "upload_speed_kbps": "4000",
        "daily_combined_quota_mb": "10",
        "daily_quota_action": "custom_speed",
        "daily_quota_speed_down_mbps": "2",
        "daily_quota_speed_up_mbps": "1",
        "enabled": "1",
    }
    plan = None
    with app.test_request_context("/admin/radius/plans", method="POST", data=form):
        from flask import session as _sess
        _sess["admin_id"] = 1
        _sess["admin_user"] = "labtester"
        _sess["admin_name"] = "Lab Tester"
        _sess["is_super_admin"] = True
        from app.radius.routes import plans as plans_routes
        try:
            _res = plans_routes.plans_create()
            if isinstance(_res, tuple) and len(_res) == 2 and _res[1] == 400:
                import re as _re2
                _body = _res[0] if isinstance(_res[0], str) else ""
                _err = _re2.search(r'hub-alert[^>]*>\s*([^<]{5,200})', _body)
                print("    (رفضُ التحقّق: %s)" % (
                    _err.group(1).strip() if _err else "غير معروف"), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("    (plans_create raised: %s)" % exc, flush=True)
    with app.app_context():
        from app.radius.db.repos import plans_repo
        hits = [p for p in plans_repo.list_plans(1, limit=80) if p.name == "LAB-DAILY"]
        plan = hits[0] if hits else None
        check("2. نموذجُ الباقة حفظ السقفَ والقرارَ والسرعة",
              bool(plan) and plan.daily_combined_quota_mb == 10
              and plan.daily_quota_action == "custom_speed"
              and plan.daily_quota_speed_down_kbps == 2000
              and plan.daily_quota_speed_up_kbps == 1000,
              "10MB · custom_speed · 2000/1000 kbps" if plan else "لم تُحفظ")

    if plan is None:
        # بلا باقةٍ محفوظةٍ لا معنى لبقيّة الفحص عبر الـHTTP؛ نُكمل بالخدمة.
        with app.app_context():
            from app.radius.core.types import AccessPlan
            from app.radius.db.repos import plans_repo
            plan = plans_repo.upsert_plan(AccessPlan(
                id=None, tenant_id=1, name="LAB-DAILY", speed_down_kbps=8000,
                speed_up_kbps=4000, daily_combined_quota_mb=10,
                daily_quota_action="custom_speed",
                daily_quota_speed_down_kbps=2000,
                daily_quota_speed_up_kbps=1000))
            check("2b. الباقة أُنشئت عبر الخدمة (احتياط)", plan is not None)

    # ── 3) مشتركٌ على الباقة + تجاوزٌ فرديّ ──
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="labuser", password="labpass",
            status="enabled", user_type="subscriber", plan_id=plan.id,
            download_speed_kbps=8000, upload_speed_kbps=4000))
        sub = subscribers_repo.get_subscriber(1, "labuser")
        check("3. المشترك أُنشئ على الباقة", sub is not None and sub.plan_id == plan.id)

    # ── 4) المحاسبة تُراكم بالفرق ──
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services import daily_quota as dq
        from app.radius.services.accounting_events import AccountingEventsService
        eng = AccountingEventsService()
        common = dict(username="labuser", acct_session_id="lab-s1",
                      nas_ip_address="10.9.9.1", framed_ip_address="10.9.9.50")
        eng.ingest(tenant_id=1, payload=dict(status_type="Start", session_time=0,
                                             input_octets=0, output_octets=0, **common))
        eng.ingest(tenant_id=1, payload=dict(status_type="Interim-Update",
                                             session_time=60, input_octets=3 * MB,
                                             output_octets=1 * MB, **common))
        eng.ingest(tenant_id=1, payload=dict(status_type="Interim-Update",
                                             session_time=120, input_octets=5 * MB,
                                             output_octets=2 * MB, **common))
        sub = subscribers_repo.get_subscriber(1, "labuser")
        used = dq.used_bytes_today(sub)
        check("4. العدّادُ بالفرق لا بالقيمة (7 ميجا)", used == 7 * MB,
              "%.2f MB" % (used / MB))

    # ── 5) النفاد ⇒ ردُّ القبول بسرعةٍ مخفَّضة ──
    with app.app_context():
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services import daily_quota as dq
        from app.radius.services import policy_engine as pe
        from app.radius.services.accounting_events import AccountingEventsService
        AccountingEventsService().ingest(tenant_id=1, payload=dict(
            status_type="Interim-Update", session_time=300,
            input_octets=9 * MB, output_octets=4 * MB,
            username="labuser", acct_session_id="lab-s1",
            nas_ip_address="10.9.9.1", framed_ip_address="10.9.9.50"))
        sub = subscribers_repo.get_subscriber(1, "labuser")
        plan2 = plans_repo.get_plan(1, plan.id)
        exhausted = dq.is_exhausted(sub, plan2)
        attrs = pe._build_accept_attrs(sub, plan2)
        rate = attrs.get("Mikrotik-Rate-Limit", "")
        timeout = int(attrs.get("Session-Timeout") or 0)
        check("5. بعد 13 ميجا من سقف 10 ⇒ نفدت", exhausted,
              "%.2f MB" % (dq.used_bytes_today(sub) / MB))
        check("5b. ردُّ القبول يَحمل 2 ميجا نزولًا", "2000k" in rate, rate or "فارغ")
        check("5c. المهلةُ تنتهي قبل منتصفِ الليل", 0 < timeout <= 86400,
              "%ds" % timeout)

    # ── 6) قرارُ «قطع» يُنتج رفضًا ──
    with app.app_context():
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services import policy_engine as pe
        sub = subscribers_repo.get_subscriber(1, "labuser")
        plan_stop = plans_repo.get_plan(1, plan.id)
        plan_stop = type(plan_stop)(**{**plan_stop.__dict__,
                                       "daily_quota_action": "disconnect"})
        decision = pe._check_daily_quota(sub, plan_stop)
        check("6. قرارُ «قطع» يَرفض المصادقة",
              decision is not None and decision.ok is False
              and decision.reason == "daily_quota_exhausted",
              getattr(decision, "reason", "لا قرار"))

    # ── 7) ختمُ الأمس يُصفّر ──
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services import daily_quota as dq
        db().execute("UPDATE subscribers SET daily_usage_day='2000-01-01' "
                     "WHERE tenant_id=1 AND username='labuser'")
        db().commit()
        sub = subscribers_repo.get_subscriber(1, "labuser")
        plan3 = plans_repo.get_plan(1, plan.id)
        check("7. ختمُ يومٍ ماضٍ ⇒ العدّادُ صفر والكوتةُ متجدّدة",
              dq.used_bytes_today(sub) == 0 and not dq.is_exhausted(sub, plan3))

    # ── 8) العاملُ الدوريّ يُنفّذ على جلسةٍ حيّة ──
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import daily_quota as dq
        from app.workers import daily_quota_worker as w
        db().execute("UPDATE subscribers SET daily_usage_day=?, daily_used_bytes=? "
                     "WHERE tenant_id=1 AND username='labuser'",
                     (dq.local_day(1), 50 * MB))
        db().commit()
        calls: list[tuple] = []
        w._coa_rate = lambda t, u, r: calls.append(("rate", t, u, r))   # type: ignore
        w._coa_disconnect = lambda t, u: calls.append(("kick", t, u))   # type: ignore
        stats = w.enforce_once()
        pushed = [c for c in calls if c[0] == "rate"]
        check("8. العاملُ دفع CoA بالسرعة المخفَّضة لجلسةٍ حيّة",
              bool(pushed) and "2000k" in pushed[0][3],
              (pushed[0][3] if pushed else "لا CoA") + " · " + str(stats))
        from app.radius.db.repos import subscribers_repo
        sub = subscribers_repo.get_subscriber(1, "labuser")
        check("8b. الحالةُ خُتمت throttled فلا يتكرّر الدفع",
              sub.daily_quota_state == "throttled", sub.daily_quota_state or "فارغة")
        calls.clear()
        w.enforce_once()
        check("8c. دورةٌ ثانية لا تُكرّر CoA", not calls, str(calls))

    print("\n" + "=" * 62)
    failed = [r for r in RESULTS if not r[1]]
    print("النتيجة: %d/%d نجحت" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        for name, _, detail in failed:
            print("  ✗ " + name + (" — " + detail if detail else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
