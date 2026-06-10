"""One-shot edit: remove the floating blue store button from
hotspot_templates.py. Owner-decision: a single store entry (the
native green card) — the duplicate floating pill was a fallback
for the pre-ES5-rewrite era and is no longer needed."""
from __future__ import annotations
import sys
from pathlib import Path

EDITS = [
    # (1) Drop the leading-section bullet that promised the
    # floating button.
    (
        "# ─── كتلة الإضافات الموحّدة (زر المتجر / التجربة / إخفاء كلمة المرور)\n"
        "#\n"
        "# بدل تكرار CSS وJS في كل قالب من العشرة، تُحقن كتلة واحدة مكتفية\n"
        "# ذاتيًا قبل </body> في render() — فتعمل الإضافات على *كل* تصميم\n"
        "# في المعرض بما فيها القوالب القديمة الخمسة:\n"
        "#\n"
        "#   • زر المتجر: يُحقن فقط إن كان STORE_ENABLED=yes والقالب لا\n"
        "#     يملك زر متجر أصليًا (عائلة التدرج/بوابة المتجر/الدخول السريع\n"
        "#     تملك أزرارها — تُكتفى بتفعيلها عبر صنف hr-store-on).\n"
        "#   • زر التجربة المجانية: رابط RouterOS القياسي\n"
        "#       $(link-login-only)?dst=$(link-orig-esc)&username=T-$(mac-esc)\n"
        "#     يُدرج عبر JS بعد نموذج الدخول مباشرة في أي تصميم.\n"
        "#   • إخفاء كلمة المرور: JS يخفي حاوية حقل password ويزيل required\n"
        "#     فيُرسل النموذج باسم المستخدم فقط (دخول MikroTik «يوزر فقط»؛\n"
        "#     مع CHAP يُهشَّر النص الفارغ بشكل صحيح فلا يتعطل doLogin).\n",

        "# ─── كتلة الإضافات الموحّدة (التجربة / إخفاء كلمة المرور)\n"
        "#\n"
        "# بدل تكرار CSS وJS في كل قالب من العشرة، تُحقن كتلة واحدة مكتفية\n"
        "# ذاتيًا قبل </body> في render() — فتعمل الإضافات على *كل* تصميم\n"
        "# في المعرض بما فيها القوالب القديمة الخمسة:\n"
        "#\n"
        "#   • زر التجربة المجانية: رابط RouterOS القياسي\n"
        "#       $(link-login-only)?dst=$(link-orig-esc)&username=T-$(mac-esc)\n"
        "#     يُدرج عبر JS بعد نموذج الدخول مباشرة في أي تصميم.\n"
        "#   • إخفاء كلمة المرور: JS يخفي حاوية حقل password ويزيل required\n"
        "#     فيُرسل النموذج باسم المستخدم فقط (دخول MikroTik «يوزر فقط»؛\n"
        "#     مع CHAP يُهشَّر النص الفارغ بشكل صحيح فلا يتعطل doLogin).\n"
        "#\n"
        "# ‏ملحوظة: «زر المتجر العائم» أُزيل (قرار المالك: مدخل واحد فقط).\n"
        "# البطاقة الخضراء الأصلية في القالب هي المدخل الوحيد للمتجر — تُكشف\n"
        "# بصنف hr-store-on الذي يضيفه سكربت STORE_ENABLED المعزول في رأس\n"
        "# كل قالب من عائلة «التدرج الاحترافي». مدخل واحد واضح أفضل من اثنين.\n",
    ),
    # (2) Delete the _store_button_html function entirely.
    (
        'def _store_button_html(store_url: str) -> str:\n'
        '    """زر متجر عائم سفلي — تصميم محايد يعمل فوق أي قالب.\n'
        '\n'
        '    مدخل البوابة (login.html) إلى المتجر/المحفظة: المتجر (store.html)\n'
        '    يحوي التدفّق الكامل — تسجيل ذاتي فوري (اسم ثلاثي + جوال + كلمة\n'
        '    مرور)، دخول، شحن المحفظة، الإيداع بتحويل، السحب، طلباتي والشات.\n'
        '    لذا نوضّح في الزر أنه طريق «التسجيل والشحن» لا مجرد متجر."""\n'
        '    return (\n'
        '        "\\n<!-- HR add-on: زر المتجر الإلكتروني (يُحقن من render) -->\\n"\n'
        '        "<style>\\n"\n'
        '        ".hr-addon-store{position:fixed;bottom:14px;left:50%;"\n'
        '        "transform:translateX(-50%);z-index:9000;display:flex;"\n'
        '        "align-items:center;gap:10px;background:#ffffff;color:#0f172a;"\n'
        '        "border:1.5px solid {{ACCENT_COLOR}};border-radius:999px;"\n'
        '        "padding:9px 20px;font-family:\'Almarai\',Tahoma,Arial,sans-serif;"\n'
        '        "text-decoration:none;box-shadow:0 10px 28px rgba(15,23,42,.25)}\\n"\n'
        '        ".hr-addon-store .hr-as-ico{width:30px;height:30px;flex-shrink:0;"\n'
        '        "border-radius:50%;background:{{ACCENT_COLOR}};color:#fff;"\n'
        '        "display:flex;align-items:center;justify-content:center;"\n'
        '        "font-size:15px}\\n"\n'
        '        ".hr-addon-store .hr-as-txt{display:flex;flex-direction:column;"\n'
        '        "line-height:1.3;text-align:start}\\n"\n'
        '        ".hr-addon-store .hr-as-txt b{font-size:13px;font-weight:800}\\n"\n'
        '        ".hr-addon-store .hr-as-txt small{font-size:10px;color:#64748b;"\n'
        '        "font-weight:700}\\n"\n'
        '        "</style>\\n"\n'
        '        \'<a class="hr-addon-store" href="\' + store_url + \'">\'\n'
        '        \'<span class="hr-as-ico">🛒</span>\'\n'
        '        \'<span class="hr-as-txt"><b>متجر البطاقات</b>\'\n'
        '        "<small>سجّل · اشحن رصيدك · طلباتي</small></span></a>\\n"\n'
        '    )\n'
        '\n'
        '\n',
        '',
    ),
    # (3) Stop calling _store_button_html in _inject_addons; replace
    # with a comment that documents the new single-entry model.
    (
        '    blocks = ""\n'
        '    if safe.get("STORE_ENABLED") == "yes":\n'
        '        # ⚠️ إصلاح «زر المتجر يختفي على الراوتر»: نحقن زرًّا ثابتًا\n'
        '        # (HTML خالص بلا اعتماد على JS القالب) دائمًا عند تفعيل المتجر —\n'
        '        # حتى للقوالب ذات الزر الأصلي (hr-store-on). سبب الاختفاء أن\n'
        '        # الزر الأصلي مرتبط بـJS القالب، فإن تعطّل سكربت القالب على\n'
        '        # الراوتر (لأي سبب) اختفى الزر؛ الزر الثابت المحقون لا يعتمد على\n'
        '        # أي سكربت فيظهر دائمًا. في القوالب السليمة قد يظهر مدخلان\n'
        '        # للمتجر (أعلى + عائم سفلي) وكلاهما يعمل — وجودٌ مضمون أهمّ من\n'
        '        # تكرار نادر.\n'
        '        blocks += _store_button_html(safe.get("STORE_URL", "#"))\n'
        '    hide_pw = safe.get("PASSWORD_FIELD") == "no"\n',

        '    blocks = ""\n'
        '    # ‏قرار المالك: مدخل متجر واحد فقط — البطاقة الخضراء الأصلية في\n'
        '    # القالب (تظهر بصنف body.hr-store-on الذي يضيفه سكربت\n'
        '    # STORE_ENABLED المعزول في عائلة «التدرج الاحترافي»). أُزيل الزر\n'
        '    # العائم الأزرق الذي كان يُحقن هنا كـfallback في حقبة ما قبل\n'
        '    # إعادة الكتابة ES5 — لم يعد ضروريًا بعد أن صار السكربت موثوقًا.\n'
        '    hide_pw = safe.get("PASSWORD_FIELD") == "no"\n',
    ),
    # (4) Update _inject_addons docstring.
    (
        'def _inject_addons(html: str, safe: dict[str, str]) -> str:\n'
        '    """يحقن كتلة الإضافات قبل </body> حسب القيم المفحوصة.\n'
        '\n'
        '    زر المتجر العائم وقسم «الجلسات المحفوظة» يُتخطيان للقوالب التي\n'
        '    تملك نسخة أصلية منهما (تُكشف بصنف التفعيل hr-store-on /\n'
        '    hr-saved-on في القالب) فلا يتكرّر الحقن."""\n',

        'def _inject_addons(html: str, safe: dict[str, str]) -> str:\n'
        '    """يحقن كتلة الإضافات قبل </body> حسب القيم المفحوصة.\n'
        '\n'
        '    الإضافات حاليًا: زر التجربة المجانية، إخفاء كلمة المرور، وقسم\n'
        '    «الجلسات المحفوظة». لم يعد يُحقن «زر المتجر العائم» — البطاقة\n'
        '    الخضراء الأصلية في القالب هي مدخل المتجر الوحيد (تُكشف بصنف\n'
        '    hr-store-on). قسم الجلسات يُتخطى للقوالب التي تملك نسخة أصلية\n'
        '    منه (fiber_glow) المعلَّمة بصنف hr-saved-on."""\n',
    ),
]


def rewrite(path: Path) -> None:
    src = path.read_text(encoding='utf-8')
    out = src
    missed = []
    for old, new in EDITS:
        if old in out:
            out = out.replace(old, new, 1)
        else:
            missed.append(old.splitlines()[0][:80])
    if missed:
        print('MISSED EDITS:', missed)
        raise SystemExit(2)
    if out == src:
        print('NO-OP — file is already in target state')
        return
    path.write_text(out, encoding='utf-8')
    print(f'rewrote {path}')


if __name__ == '__main__':
    target = Path(sys.argv[1] if len(sys.argv) > 1
                  else 'app/radius/services/hotspot_templates.py')
    rewrite(target)
