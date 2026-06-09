"""تحقّق رندر: يعرض القوالب التجريبية بالعربية والإنجليزية ويؤكد:
- اتجاه <html> يتبدّل (rtl/ltr).
- النص الإنجليزي يظهر بالإنجليزية، والعربي بالعربية.
- صفر تسريب: لا عربي محدّد بالإنجليزية، ولا إنجليزي محدّد بالعربية.
"""
import os
os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"

from app import create_app

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

# عيّنات نتحقق منها (نص عربي مغلّف ↔ ترجمته الإنجليزية)
CHECKS = {
    "login": {
        "url": "/admin/radius/login",
        "auth": False,
        "ar_must": ["تسجيل الدخول", "اسم المستخدم", "كلمة المرور"],
        "en_must": ["Sign in", "Username", "Password"],
    },
    "dashboard": {
        "url": "/admin/radius/",
        "auth": True,
        # نصوص من الهيكل/السايدبار/الداشبورد
        "ar_must": ["المشتركون", "البطاقات", "تسجيل الخروج"],
        "en_must": ["Subscribers", "Cards", "Sign out"],
    },
}

failures = []


def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "admin"
        s["admin_name"] = "admin"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["permissions"] = ["*"]


def _set_locale(client, locale):
    with client.session_transaction() as s:
        s["locale"] = locale


def _get_html(name, spec, locale):
    with app.test_client() as c:
        if spec["auth"]:
            _login(c)
        _set_locale(c, locale)
        r = c.get(spec["url"], follow_redirects=True)
        return r.get_data(as_text=True)


import re


def _strip_noise(html):
    """يزيل ما لا يراه المستخدم قبل فحص التسريب: تعليقات HTML وكتل <style>
    (تحوي تعليقات CSS عربية مقصودة). التعليقات العربية في الكود *مطلوبة*."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    return html


for name, spec in CHECKS.items():
    ar = _strip_noise(_get_html(name, spec, "ar"))
    en = _strip_noise(_get_html(name, spec, "en"))

    # اتجاه html
    ar_dir = 'dir="rtl"' in ar
    en_dir = 'dir="ltr"' in en
    print(f"[{name}] ar dir=rtl: {ar_dir} | en dir=ltr: {en_dir}")
    if not ar_dir:
        failures.append(f"{name}: العربية ليست rtl")
    if not en_dir:
        failures.append(f"{name}: الإنجليزية ليست ltr")

    # العربية تعرض العربي
    for s in spec["ar_must"]:
        if s not in ar:
            failures.append(f"{name}/ar: مفقود «{s}»")
    # الإنجليزية تعرض الإنجليزي
    for s in spec["en_must"]:
        if s not in en:
            failures.append(f"{name}/en: missing «{s}»")
    # تسريب: العربي المحدّد يجب ألا يظهر بالإنجليزية
    for s in spec["ar_must"]:
        if s in en:
            failures.append(f"{name}/en: تسريب عربي «{s}»")
    # تسريب عكسي: الإنجليزي المحدّد يجب ألا يظهر بالعربية
    for s in spec["en_must"]:
        if s in ar:
            failures.append(f"{name}/ar: تسريب إنجليزي «{s}»")

    print(f"[{name}] ar len={len(ar)} en len={len(en)}")

print("\n=== النتيجة ===")
if failures:
    for f in failures:
        print("✗", f)
    raise SystemExit(1)
print("✓ كل الفحوصات نجحت — تبديل سليم، صفر تسريب باتجاهين.")
