#!/usr/bin/env python3
"""
probe_super_sidebar — تشخيص جذر «تجميد كل أقسام السايدبار للمدير الرئيسي»
على الإنتاج، عبر التطبيق الحقيقي (نفس create_app + سياق طلب فعلي).

يُجاب بوضوح عن الأسئلة الأربعة الحاسمة:
  1) هل طبقة الصلاحيات app/radius/auth/ui_permissions.py موجودة وقابلة
     للاستيراد في هذا البناء؟  (السبب الأشيع: الملف مُتجاهَل في git فيغيب
     من صورة الإنتاج — نمط .gitignore «????/» يبتلع مجلد auth ذا الأربعة أحرف.)
  2) بعد set_current_admin لمدير رئيسي، هل session['is_super_admin'] == True؟
  3) هل can / perm_for_endpoint محقونتان فعلاً في سياق القوالب؟
  4) ناتج can('users.view') و can(None) لجلسة سوبر — يجب أن يكونا True.

التشغيل داخل الحاوية:
    docker compose exec <web-service> python tools/probe_super_sidebar.py
أو محليًا:
    python tools/probe_super_sidebar.py

لا يكتب أي شيء في قاعدة البيانات (جلسة وهمية في سياق اختبار فقط).
"""
from __future__ import annotations

import os
import sys

# جذر المشروع على المسار حتى يُستورَد حزمة app عند التشغيل المباشر من tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# لا نشغّل خيوط الخلفية ولا البذر أثناء الفحص.
os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")

# إخراج UTF-8 مهما كانت locale الحاوية (نتجنّب رموز emoji أصلاً للسلامة).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


def _ok(b: bool) -> str:
    return "[OK]" if b else "[FAIL]"


def main() -> int:
    print("=" * 64)
    print("probe_super_sidebar — تشخيص تجميد السايدبار للمدير الرئيسي")
    print("=" * 64)

    # ── 1) هل طبقة ui_permissions موجودة في البناء؟ ──
    layer_ok = False
    try:
        from app.radius.auth import ui_permissions as _uip  # noqa: F401
        layer_ok = True
        print(f"[1] استيراد app.radius.auth.ui_permissions : {_ok(True)}")
        print(f"    المسار: {getattr(_uip, '__file__', '?')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[1] استيراد app.radius.auth.ui_permissions : {_ok(False)}")
        print(f"    السبب: {type(exc).__name__}: {exc}")
        print("    ⇒ هذا هو الجذر الأرجح: الملف غائب عن البناء (مُتجاهَل في git).")
        print("      الأغلفة القديمة كانت تبتلع هذا الخطأ وتُرجِع False لكل")
        print("      مفتاح حتى للسوبر ⇒ تجميد كامل. الإصلاح: تتبُّع الملف +")
        print("      حقن مشروط fail-open.")

    # ── بناء التطبيق ──
    from app import create_app
    app = create_app()

    # ── 2) is_super_admin بعد set_current_admin ──
    from app.radius.auth.session_helpers import set_current_admin
    from app.radius.core.types import Admin

    fake_super = Admin(
        id=999999, username="__probe_super__", password_hash="x",
        full_name="Probe Super", role_id=None, is_super_admin=True, enabled=True,
    )

    with app.test_request_context("/admin/radius/dashboard"):
        from flask import session
        try:
            set_current_admin(fake_super, tenant_id=1)
        except Exception as exc:  # noqa: BLE001
            # set_current_admin يلمس permissions_of عبر DB؛ إن فشل نضبط يدويًا.
            print(f"[!] set_current_admin أطلق {type(exc).__name__}: {exc} "
                  f"— نضبط الجلسة يدويًا للمتابعة.")
            session["is_super_admin"] = True
            session["tenant_id"] = 1
            session["permissions"] = []
        sess_super = bool(session.get("is_super_admin"))
        print(f"[2] session['is_super_admin'] بعد الدخول : {sess_super}  {_ok(sess_super)}")

        # ── 3) هل can/perm_for_endpoint محقونتان في سياق القوالب؟ ──
        # update_template_context يملأ القاموس مكانه ويُرجِع None.
        ctx: dict = {}
        app.update_template_context(ctx)
        has_can = "can" in ctx
        has_pfe = "perm_for_endpoint" in ctx
        has_iss = "is_super_admin" in ctx
        print(f"[3] محقون في القوالب: can={has_can} perm_for_endpoint={has_pfe} "
              f"is_super_admin={has_iss}  {_ok(has_can and has_pfe and has_iss)}")
        # المحاكاة الحرفية لمنطق السايدبار:
        _rbac_ui = has_can and has_pfe and not ctx.get("is_super_admin")
        print(f"    _rbac_ui (منطق السايدبار) = {_rbac_ui}  "
              f"(للسوبر يجب أن يكون False ⇒ عرض الكل)")

        # ── 4) ناتج can للسوبر ──
        if has_can:
            try:
                c_view = ctx["can"]("users.view")
                c_none = ctx["can"](None)
                print(f"[4] can('users.view')={c_view}  can(None)={c_none}  "
                      f"{_ok(c_view is True and c_none is True)}")
            except Exception as exc:  # noqa: BLE001
                print(f"[4] can(...) أطلق استثناء: {type(exc).__name__}: {exc}  [FAIL]")
        else:
            print("[4] can غير محقونة ⇒ السايدبار يرتدّ لعرض الكل (fail-open).  [OK]")

    # ── الخلاصة ──
    print("-" * 64)
    if not layer_ok:
        print("الخلاصة: طبقة الصلاحيات غائبة عن البناء. بعد دمج فرع")
        print("fix/super-admin-sidebar-unlock (يتتبّع الملف + fail-open) ثم")
        print("git pull && docker compose up -d --build، يجب أن يختفي العطل.")
        print("ملاحظة: إعادة بناء --no-cache من main وحدها لا تكفي — الملف غير")
        print("موجود في main أصلاً، فلا بناء يضيفه حتى يُدمج هذا الفرع.")
    else:
        print("الخلاصة: الطبقة موجودة. راجع البندين [2] و[4]: إن كان")
        print("is_super_admin=False في الجلسة فالجذر مسار الدخول/البيانات؛ وإن")
        print("كان can('users.view')=False رغم سوبر=True فهناك خطأ داخل can().")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
