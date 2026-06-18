# -*- coding: utf-8 -*-
"""hotspot_addons — إطار «الإضافات الاختيارية» لمصمّم صفحة الدخول (P1).

الفكرة
------
كل فكرة تصميمية (ساعة حيّة، شريط أخبار، لافتة طوارئ، روابط تواصل،
مؤقّت وصول، راعٍ، نموذج جمع بيانات...) تُمثَّل كـ«إضافة» (add-on):
مفتاح + تصنيف + سطح (pre/post) + حقول إعداد + (اختياري) نطاقات
walled-garden يحتاجها + دالة توليد جزء HTML. كلها قابلة للتشغيل/
الإيقاف من المصمّم، وكلها تُخزَّن في عمود addons_json (migration 128).

النموذج ذو السطحين (TWO-SURFACE)
--------------------------------
* SURFACE_PRELOGIN: يُحقن في login.html (السبلاش قبل الدخول). يجب أن
  يعمل **بلا إنترنت** — لذا كل محتواه يُخبَز خادميًّا وقت التوليد
  (server-side injection): لا fetch خارجي، لا اعتماد على whitelist،
  تحميل فوري. أي نص مستخدم يُهرَّب HTML هنا.
* SURFACE_POSTLOGIN: يُركَّب في صفحة ما بعد الدخول (redirect) المستضافة
  على خادم اللوحة — الإنترنت يعمل، فيمكن للودجت أن تكون حيّة (روابط
  خارجية، بثّ، خرائط...).
* SURFACE_BOTH: متاح في السطحين (مثل لافتة الطوارئ).

walled-garden التلقائي
----------------------
الإضافة قد تعلن نطاقات خارجية تحتاجها (`walled_garden_domains`). عند
النشر يجمع المحرّك نطاقات كل الإضافات المفعّلة ويضيفها لقائمة
walled-garden في المايكروتيك تلقائيًّا (انظر hotspot_store_page.
ensure_walled_garden_hosts). الإضافات المخبوزة خادميًّا (server_side)
لا تحتاج أي نطاق غالبًا — هذا هو الفارق التجاري في منطقة MENA.

الأمان
------
كل قيمة نصّية من المستخدم تمرّ بـ`_esc` (هروب HTML) قبل دخولها الـ
HTML المولَّد، وروابط الـURL تُفحص بنمط صارم. التوليد لا يثق بإدخال.

هذا الملف هو P1 (الإطار + التشغيل/الإيقاف + السطحان + جامع
walled-garden) مع حفنة إضافات مرجعية تثبت كل آلية. تُضاف بقية كتالوج
الإضافات (محتوى/ثيمات/ربح/دخول/تفاعل) في المراحل التالية بنفس النمط:
عرّف AddonSpec وسجّله — لا تغيير في المحرّك.
"""
from __future__ import annotations

import html as _html
import json as _json
import re as _re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

# ─── السطوح ─────────────────────────────────────────────────────
SURFACE_PRELOGIN = "pre"
SURFACE_POSTLOGIN = "post"
SURFACE_BOTH = "both"
_SURFACES = {SURFACE_PRELOGIN, SURFACE_POSTLOGIN, SURFACE_BOTH}

# ─── التصنيفات ──────────────────────────────────────────────────
CAT_CONTENT = "content"
CAT_MONETIZATION = "monetization"
CAT_THEME = "theme"
CAT_LOGIN = "login"
CAT_ENGAGEMENT = "engagement"

CATEGORY_LABELS: dict[str, str] = {
    CAT_CONTENT: "المحتوى",
    CAT_MONETIZATION: "الربح والتسويق",
    CAT_THEME: "الثيمات والمظهر",
    CAT_LOGIN: "أنماط الدخول",
    CAT_ENGAGEMENT: "التفاعل والولاء",
}
# ترتيب عرض التصنيفات في المصمّم.
CATEGORY_ORDER: tuple[str, ...] = (
    CAT_THEME, CAT_CONTENT, CAT_LOGIN, CAT_MONETIZATION, CAT_ENGAGEMENT,
)


# ─── حقل إعداد إضافة ────────────────────────────────────────────
@dataclass(frozen=True)
class AddonField:
    """حقل إعداد واحد داخل إضافة. `kind` يقود رسم المصمّم والتحقّق."""
    key: str
    label_ar: str
    kind: str = "text"        # text|textarea|bool|url|color|number|select
    default: str = ""
    placeholder: str = ""
    help_ar: str = ""
    options: tuple[tuple[str, str], ...] = ()   # (value, label) لـselect
    max_len: int = 200
    min_num: int = 0
    max_num: int = 1_000_000


@dataclass(frozen=True)
class AddonSpec:
    """تعريف إضافة. التوليد عبر pre_fragment/post_widget — دوال نقيّة
    تستقبل (config: dict, ctx: dict) وتعيد HTML آمنًا (نصوص مهرَّبة)."""
    key: str
    category: str
    label_ar: str
    desc_ar: str
    surface: str
    icon: str = "puzzle-piece"
    fields: tuple[AddonField, ...] = ()
    walled_garden_domains: tuple[str, ...] = ()
    server_side: bool = False
    default_on: bool = False
    pre_fragment: Optional[Callable[[dict, dict], str]] = None
    post_widget: Optional[Callable[[dict, dict], str]] = None

    def runs_prelogin(self) -> bool:
        return self.surface in (SURFACE_PRELOGIN, SURFACE_BOTH)

    def runs_postlogin(self) -> bool:
        return self.surface in (SURFACE_POSTLOGIN, SURFACE_BOTH)


# ════════════════════════════════════════════════════════════════
# السجل
# ════════════════════════════════════════════════════════════════
ADDONS: dict[str, AddonSpec] = {}


def register(spec: AddonSpec) -> AddonSpec:
    if spec.surface not in _SURFACES:
        raise ValueError(f"surface غير صالح للإضافة {spec.key}: {spec.surface}")
    if spec.category not in CATEGORY_LABELS:
        raise ValueError(f"تصنيف غير معروف للإضافة {spec.key}: {spec.category}")
    if spec.key in ADDONS:
        raise ValueError(f"إضافة مكرّرة: {spec.key}")
    if spec.runs_prelogin() and spec.pre_fragment is None:
        raise ValueError(f"إضافة pre بلا pre_fragment: {spec.key}")
    if spec.runs_postlogin() and spec.post_widget is None:
        raise ValueError(f"إضافة post بلا post_widget: {spec.key}")
    ADDONS[spec.key] = spec
    return spec


def get(key: str) -> Optional[AddonSpec]:
    return ADDONS.get(key)


def all_addons() -> list[AddonSpec]:
    return list(ADDONS.values())


def by_category() -> dict[str, list[AddonSpec]]:
    """إضافات مجمّعة بالتصنيف بترتيب CATEGORY_ORDER — للمصمّم."""
    out: dict[str, list[AddonSpec]] = {c: [] for c in CATEGORY_ORDER}
    for spec in ADDONS.values():
        out.setdefault(spec.category, []).append(spec)
    return {c: out[c] for c in CATEGORY_ORDER if out.get(c)}


# ════════════════════════════════════════════════════════════════
# أدوات الأمان والتطبيع
# ════════════════════════════════════════════════════════════════
def _esc(s: object) -> str:
    """هروب HTML لأي نص مستخدم قبل دخوله الصفحة المولَّدة."""
    return _html.escape(str(s if s is not None else ""), quote=True)


_SAFE_URL_RE = _re.compile(
    r"^(https?://[A-Za-z0-9\.\-_/:%?=&#~+]+|/[A-Za-z0-9\.\-_/?=&#%]*)$")
_DOMAIN_RE = _re.compile(r"^[A-Za-z0-9\.\-\*]{1,253}$")


def safe_url(u: object, *, default: str = "") -> str:
    """يعيد رابطًا آمنًا (http/https أو مسار محلّي) أو default."""
    s = str(u or "").strip()
    return s if _SAFE_URL_RE.match(s) else default


def _domain_of(u: str) -> str:
    """يستخرج النطاق من رابط كامل (للـwalled-garden)."""
    s = str(u or "").strip()
    if "://" in s:
        host = urlsplit(s).hostname or ""
        return host.lower()
    # قد يكون نطاقًا مجرّدًا
    s = s.split("/", 1)[0].lower()
    return s if _DOMAIN_RE.match(s) else ""


# ════════════════════════════════════════════════════════════════
# تطبيع الإعداد المخزَّن (addons_json)
# ════════════════════════════════════════════════════════════════
def normalize_config(raw: object) -> dict[str, dict]:
    """يطبّع خريطة الإضافات المخزَّنة إلى شكل موثوق:
        { key: {"enabled": bool, "config": {field: value, ...}} }
    يُسقِط المفاتيح المجهولة والحقول المجهولة، ويقصّ/يقيّد القيم حسب
    تعريف كل حقل. لا يرفع استثناء — الإدخال السيّئ يُهمَل بأمان."""
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, dict] = {}
    for key, spec in ADDONS.items():
        entry = raw.get(key) if isinstance(raw.get(key), dict) else {}
        enabled = bool(entry.get("enabled", spec.default_on))
        cfg_in = entry.get("config") if isinstance(entry.get("config"), dict) else {}
        cfg_out: dict[str, str] = {}
        for f in spec.fields:
            cfg_out[f.key] = _normalize_field(f, cfg_in.get(f.key))
        out[key] = {"enabled": enabled, "config": cfg_out}
    return out


def _normalize_field(f: AddonField, val: object) -> str:
    """يحوّل قيمة حقل واحدة إلى نص مطبَّع آمن حسب نوعه."""
    if f.kind == "bool":
        if isinstance(val, bool):
            return "yes" if val else "no"
        return "yes" if str(val).strip().lower() in ("yes", "true", "1", "on") else "no"
    s = "" if val is None else str(val)
    s = s.strip()
    if not s:
        return f.default
    if f.kind == "color":
        return s if _re.match(r"^#[0-9A-Fa-f]{6}$", s) else (f.default or "#2563EB")
    if f.kind == "number":
        try:
            n = int(float(s))
        except (TypeError, ValueError):
            return f.default or "0"
        n = max(f.min_num, min(f.max_num, n))
        return str(n)
    if f.kind == "url":
        return safe_url(s, default=f.default)
    if f.kind == "select":
        allowed = {o[0] for o in f.options}
        return s if s in allowed else (f.default or (f.options[0][0] if f.options else ""))
    # text / textarea — قصّ الطول، وإزالة محارف التحكّم.
    s = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s[: f.max_len]


def serialize_config(cfg: dict[str, dict]) -> str:
    return _json.dumps(cfg, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════
# الاستعلام عن المفعّل + جامع walled-garden
# ════════════════════════════════════════════════════════════════
def enabled_specs(cfg: dict[str, dict]) -> list[tuple[AddonSpec, dict]]:
    """قائمة (spec, config) للإضافات المفعّلة فقط، بترتيب التصنيف."""
    norm = normalize_config(cfg)
    ordered: list[tuple[AddonSpec, dict]] = []
    for cat in CATEGORY_ORDER:
        for spec in ADDONS.values():
            if spec.category != cat:
                continue
            entry = norm.get(spec.key) or {}
            if entry.get("enabled"):
                ordered.append((spec, entry.get("config") or {}))
    return ordered


def collect_walled_garden_domains(cfg: dict[str, dict]) -> list[str]:
    """نطاقات walled-garden المطلوبة لكل الإضافات المفعّلة — موحَّدة
    ومرتّبة. تشمل النطاقات الثابتة في التعريف + أي نطاق مشتقّ من حقل
    رابط في الإعداد (مثل رابط بثّ راديو أو راعٍ).

    الإضافات المخبوزة خادميًّا (server_side) لا تُضيف نطاقًا من حقولها
    تلقائيًّا — محتواها داخل الصفحة، فلا تحتاج إنترنت قبل الدخول."""
    domains: set[str] = set()
    for spec, config in enabled_specs(cfg):
        for d in spec.walled_garden_domains:
            d = str(d).strip().lower()
            if d and _DOMAIN_RE.match(d):
                domains.add(d)
        if spec.server_side:
            continue
        # نطاقات مشتقّة من حقول الروابط (للسطح ما بعد الدخول غالبًا،
        # لكن نجمعها لأي إضافة غير مخبوزة تحتاج موردًا خارجيًّا).
        for f in spec.fields:
            if f.kind == "url":
                host = _domain_of(config.get(f.key, ""))
                if host:
                    domains.add(host)
    return sorted(domains)


# ════════════════════════════════════════════════════════════════
# توليد أجزاء السطحين
# ════════════════════════════════════════════════════════════════
def render_prelogin_fragments(cfg: dict[str, dict], ctx: Optional[dict] = None) -> str:
    """HTML أجزاء كل الإضافات المفعّلة ذات السطح pre — مخبوزة خادميًّا
    لتعمل قبل الدخول. يُحقن قبل </body> في login.html."""
    ctx = ctx or {}
    parts: list[str] = []
    for spec, config in enabled_specs(cfg):
        if not spec.runs_prelogin() or spec.pre_fragment is None:
            continue
        try:
            frag = spec.pre_fragment(config, ctx)
        except Exception:  # noqa: BLE001 — إضافة معطوبة لا تُسقِط الصفحة
            frag = ""
        if frag:
            parts.append(
                f"<!-- hr-addon:{spec.key} -->\n{frag}")
    return "\n".join(parts)


def render_postlogin_widgets(cfg: dict[str, dict], ctx: Optional[dict] = None) -> str:
    """HTML ودجت كل الإضافات المفعّلة ذات السطح post — لصفحة ما بعد
    الدخول المستضافة (الإنترنت يعمل)."""
    ctx = ctx or {}
    parts: list[str] = []
    for spec, config in enabled_specs(cfg):
        if not spec.runs_postlogin() or spec.post_widget is None:
            continue
        try:
            w = spec.post_widget(config, ctx)
        except Exception:  # noqa: BLE001
            w = ""
        if w:
            parts.append(
                f'<section class="hr-widget hr-widget--{spec.key}">{w}</section>')
    return "\n".join(parts)


def has_postlogin(cfg: dict[str, dict]) -> bool:
    """هل توجد أي إضافة post مفعّلة؟ (يقرّر بناء صفحة ما بعد الدخول)."""
    return any(spec.runs_postlogin() for spec, _ in enabled_specs(cfg))


# ════════════════════════════════════════════════════════════════
# الإضافات المرجعية (P1) — تثبت كل آلية؛ الكتالوج الكامل لاحقًا
# ════════════════════════════════════════════════════════════════

# ── 1) ساعة حيّة (محتوى، pre، CSS/JS فقط، بلا نطاقات) ──
def _frag_live_clock(cfg: dict, ctx: dict) -> str:
    fmt = cfg.get("format", "24h")
    h12 = "true" if fmt == "12h" else "false"
    accent = ctx.get("accent", "#2563EB")
    return (
        '<div id="hr-clock" dir="ltr" style="text-align:center;margin:14px auto;'
        'font-weight:800;letter-spacing:.5px;color:' + _esc(accent) + ';'
        'font-variant-numeric:tabular-nums">--:--</div>'
        "<script>(function(){var e=document.getElementById('hr-clock');"
        "if(!e)return;var H12=" + h12 + ";function t(){var d=new Date(),"
        "h=d.getHours(),m=d.getMinutes(),s=d.getSeconds(),ap='';"
        "if(H12){ap=h<12?' AM':' PM';h=h%12;if(h===0)h=12;}"
        "function p(n){return(n<10?'0':'')+n;}"
        "e.textContent=p(h)+':'+p(m)+':'+p(s)+ap;}"
        "t();setInterval(t,1000);})();</script>")


register(AddonSpec(
    key="live_clock",
    category=CAT_CONTENT,
    label_ar="ساعة حيّة",
    desc_ar="ساعة رقمية تعمل لحظيًّا على صفحة الدخول — بلا إنترنت.",
    surface=SURFACE_PRELOGIN,
    icon="clock",
    server_side=True,
    fields=(
        AddonField(key="format", label_ar="صيغة الوقت", kind="select",
                   default="24h",
                   options=(("24h", "٢٤ ساعة"), ("12h", "١٢ ساعة (AM/PM)"))),
    ),
    pre_fragment=_frag_live_clock,
))


# ── 2) لوحة إعلانات (محتوى، pre، مخبوزة خادميًّا) ──
def _frag_announcements(cfg: dict, ctx: dict) -> str:
    title = _esc(cfg.get("title") or "إعلانات")
    body = _esc(cfg.get("body") or "")
    if not body:
        return ""
    accent = _esc(ctx.get("accent", "#2563EB"))
    # نخبز النص حرفيًّا (server-side) فيظهر فورًا بلا أي اتصال.
    lines = "".join(
        f"<li>{_esc(ln)}</li>" for ln in cfg.get("body", "").split("\n") if ln.strip())
    return (
        '<div class="hr-board" style="margin:14px auto;max-width:520px;'
        'border:1px solid #e6eaf2;border-radius:14px;overflow:hidden;'
        'background:#fff;text-align:right">'
        '<div style="background:' + accent + ';color:#fff;padding:9px 14px;'
        'font-weight:800">' + title + "</div>"
        '<ul style="margin:0;padding:12px 26px 12px 14px;line-height:1.9">'
        + lines + "</ul></div>")


register(AddonSpec(
    key="announcements",
    category=CAT_CONTENT,
    label_ar="لوحة إعلانات",
    desc_ar="إعلانات تُخبَز داخل الصفحة وتظهر فورًا قبل الدخول (سطر لكل نقطة).",
    surface=SURFACE_PRELOGIN,
    icon="bullhorn",
    server_side=True,
    fields=(
        AddonField(key="title", label_ar="العنوان", default="إعلانات",
                   max_len=60),
        AddonField(key="body", label_ar="النص (سطر لكل نقطة)", kind="textarea",
                   placeholder="ساعات العمل ٨ص–١٢م\nصيانة الجمعة ٢ظهرًا",
                   max_len=1000),
    ),
    pre_fragment=_frag_announcements,
))


# ── 3) لافتة طوارئ/إشعار عاجل (محتوى، both، مخبوزة) ──
def _emergency_html(cfg: dict, ctx: dict) -> str:
    text = cfg.get("text", "")
    if not str(text).strip():
        return ""
    return (
        '<div class="hr-emergency" role="alert" style="background:#b91c1c;'
        'color:#fff;text-align:center;padding:10px 14px;font-weight:800;'
        'position:relative;z-index:9999">'
        '<i class="fa-solid fa-triangle-exclamation"></i> '
        + _esc(text) + "</div>")


register(AddonSpec(
    key="emergency_notice",
    category=CAT_CONTENT,
    label_ar="إشعار طوارئ",
    desc_ar="شريط أحمر عاجل أعلى الصفحة — قبل الدخول وبعده.",
    surface=SURFACE_BOTH,
    icon="triangle-exclamation",
    server_side=True,
    fields=(
        AddonField(key="text", label_ar="نص الإشعار", kind="text",
                   placeholder="انقطاع مجدول للصيانة الليلة ١٢–٢ص", max_len=160),
    ),
    pre_fragment=_emergency_html,
    post_widget=_emergency_html,
))


# ── 4) روابط التواصل (محتوى/تفاعل، post، تحتاج نطاقات walled-garden) ──
_SOCIAL = (
    ("facebook", "فيسبوك", "facebook.com", "f"),
    ("instagram", "إنستغرام", "instagram.com", "in"),
    ("whatsapp", "واتساب", "wa.me", "wa"),
    ("telegram", "تلجرام", "t.me", "tg"),
)


def _widget_social(cfg: dict, ctx: dict) -> str:
    btns: list[str] = []
    for key, label, _dom, _abbr in _SOCIAL:
        url = safe_url(cfg.get(key, ""))
        if not url:
            continue
        btns.append(
            f'<a href="{_esc(url)}" target="_blank" rel="noopener" '
            f'class="hr-soc hr-soc--{key}" '
            'style="display:inline-flex;align-items:center;gap:6px;margin:4px;'
            'padding:8px 14px;border-radius:10px;background:#f1f5f9;'
            'text-decoration:none;color:#1e293b;font-weight:700">'
            f"{_esc(label)}</a>")
    if not btns:
        return ""
    return ('<h3 style="margin:6px 0;font-size:15px">تابعنا</h3>'
            '<div style="display:flex;flex-wrap:wrap;justify-content:center">'
            + "".join(btns) + "</div>")


register(AddonSpec(
    key="social_links",
    category=CAT_ENGAGEMENT,
    label_ar="روابط التواصل",
    desc_ar="أزرار صفحاتك (فيسبوك/إنستغرام/واتساب/تلجرام) على صفحة ما بعد الدخول.",
    surface=SURFACE_POSTLOGIN,
    icon="share-nodes",
    walled_garden_domains=tuple(dom for _k, _l, dom, _a in _SOCIAL),
    fields=(
        AddonField(key="facebook", label_ar="رابط فيسبوك", kind="url",
                   placeholder="https://facebook.com/yourpage"),
        AddonField(key="instagram", label_ar="رابط إنستغرام", kind="url"),
        AddonField(key="whatsapp", label_ar="رابط واتساب", kind="url",
                   placeholder="https://wa.me/9665XXXXXXXX"),
        AddonField(key="telegram", label_ar="رابط تلجرام", kind="url"),
    ),
    post_widget=_widget_social,
))


# ── 5) مؤقّت العدّ التنازلي للوصول (تفاعل، pre) ──
def _frag_countdown(cfg: dict, ctx: dict) -> str:
    try:
        secs = int(cfg.get("seconds", "10") or "10")
    except (TypeError, ValueError):
        secs = 10
    secs = max(1, min(600, secs))
    label = _esc(cfg.get("label") or "يبدأ الوصول خلال")
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<div class="hr-countdown" style="text-align:center;margin:12px auto;'
        'font-weight:700;color:#475569">' + label
        + ' <span id="hr-cd" dir="ltr" style="color:' + accent
        + ';font-weight:900">' + str(secs) + '</span></div>'
        "<script>(function(){var n=" + str(secs) + ",e=document.getElementById"
        "('hr-cd');if(!e)return;var t=setInterval(function(){n--;"
        "if(n<=0){clearInterval(t);e.textContent='0';"
        "var b=document.querySelector('button[type=submit],input[type=submit]');"
        "if(b){b.disabled=false;}}else{e.textContent=n;}},1000);})();</script>")


register(AddonSpec(
    key="countdown_access",
    category=CAT_ENGAGEMENT,
    label_ar="مؤقّت الوصول",
    desc_ar="عدّ تنازلي قصير قبل تفعيل زر الدخول — يعمل بلا إنترنت.",
    surface=SURFACE_PRELOGIN,
    icon="hourglass-half",
    server_side=True,
    fields=(
        AddonField(key="seconds", label_ar="عدد الثواني", kind="number",
                   default="10", min_num=1, max_num=600),
        AddonField(key="label", label_ar="النص قبل العدّاد",
                   default="يبدأ الوصول خلال", max_len=40),
    ),
    pre_fragment=_frag_countdown,
))


__all__ = [
    "SURFACE_PRELOGIN", "SURFACE_POSTLOGIN", "SURFACE_BOTH",
    "CAT_CONTENT", "CAT_MONETIZATION", "CAT_THEME", "CAT_LOGIN", "CAT_ENGAGEMENT",
    "CATEGORY_LABELS", "CATEGORY_ORDER",
    "AddonField", "AddonSpec", "ADDONS",
    "register", "get", "all_addons", "by_category",
    "normalize_config", "serialize_config", "enabled_specs",
    "collect_walled_garden_domains",
    "render_prelogin_fragments", "render_postlogin_widgets", "has_postlogin",
    "safe_url",
]
