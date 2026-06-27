"""hotspot_templates_pro — عائلة «التدرج الاحترافي».

ثلاثة قوالب فاخرة لصفحة دخول الهوت سبوت مبنية على تصميم واحد:
تطبيق جوال كامل داخل صفحة واحدة (شاشة افتتاحية، تبويبات سفلية:
الرئيسية / الباقات / الموزعون / الدعم / معلومات)، وضع ليلي،
بطاقات متدرّجة بدوائر زخرفية، فاحص شبكة، وأسئلة شائعة.

نقاط العقد المهمة:

  - صفحة واحدة مكتفية ذاتيًا: كل الأيقونات SVG مضمّنة كـ data-URI
    داخل CSS، والصور الرمزية SVG مضمّنة، وخريطة الموزعين رسم CSS —
    لا CDN ولا ملفات خارجية إجبارية. خط المراعي (Almarai) المعتمد
    يُحمَّل اختياريًا من مسار نسبي `fonts/Almarai-*.woff2` (وزنان
    عادي + عريض، يشحنان مع المشروع في app/static/hotspot/fonts/)
    ويسقط بأمان إلى خطوط النظام إن غابت الملفات على الراوتر.

  - متغيّرات Hoberadius مربوطة تمامًا كبقية القوالب عبر str.replace:
    TENANT_NAME / TENANT_LOGO_URL / WELCOME_TEXT / ACCENT_COLOR /
    BG_COLOR / SUPPORT_PHONE.

  - placeholders راوتر أو إس محفوظة حرفيًا:
    $(link-login-only) $(link-orig) $(error) $(username) $(ip)
    $(mac) $(chap-id) $(chap-challenge) — مع نموذج sendin مخفي
    ودالة doLogin (MD5 مضمّن) لدعم CHAP، تمامًا كقالب MikroTik
    الرسمي، فيمرّ فحص ROUTEROS_REQUIRED ويُقبل في deploy_login.

  - النسخ الثلاث تشترك في الهيكل وتختلف في كتلة الألوان (tokens)
    فقط — الاستبدال يتم هنا وقت الاستيراد فتبقى LoginTemplate.html
    سلسلة ثابتة كاملة كبقية الكتالوج.
"""
from __future__ import annotations


# ─── الصور الرمزية — SVG مضمّنة (بديل img/1..5.jpg الأصلية) ────
# خمس صور رمزية بتدرّجات مختلفة؛ قِصر السلاسل مقصود حتى لا تتضخم
# الصفحة. تُستخدم من JS عبر الخريطة HR_AVATARS ومن وسم <img> الأول.


def _avatar(c1: str, c2: str) -> str:
    """يبني data-URI لصورة رمزية SVG بتدرّج لوني معيّن."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='{c1}'/>"
        f"<stop offset='1' stop-color='{c2}'/></linearGradient></defs>"
        "<rect width='64' height='64' fill='url(%23g)'/>"
        "<circle cx='32' cy='24' r='11' fill='rgba(255,255,255,0.92)'/>"
        "<path d='M12 58c2-13 10-19 20-19s18 6 20 19z' "
        "fill='rgba(255,255,255,0.92)'/></svg>"
    )
    return "data:image/svg+xml," + svg.replace("#", "%23").replace(
        "<", "%3C").replace(">", "%3E").replace("'", "%27")


_AVATARS = [
    _avatar("#22d3ee", "#3b82f6"),
    _avatar("#a855f7", "#6366f1"),
    _avatar("#f59e0b", "#ef4444"),
    _avatar("#10b981", "#0d9488"),
    _avatar("#ec4899", "#8b5cf6"),
]

_AVATARS_JS = "{" + ",".join(
    f"'{i + 1}':\"{u}\"" for i, u in enumerate(_AVATARS)) + "}"


# ─── كتلة أيقونات النظام (SVG mask مضمّنة — من التصميم الأصلي) ──

_ICONS_CSS = """.ico{display:inline-block;width:1em;height:1em;background-color:currentColor;-webkit-mask-size:contain;mask-size:contain;-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;-webkit-mask-position:center;mask-position:center;vertical-align:middle}
.ico-home{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z'/></svg>")}.ico-cubes{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M11.99 18.54l-7.37-5.73L3 14.07l9 7 9-7-1.63-1.27-7.38 5.74zM12 16l7.36-5.73L21 9l-9-7-9 7 1.63 1.27L12 16zM11.99 2.54L3 9.58l9 7.04 9-7.04z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M11.99 18.54l-7.37-5.73L3 14.07l9 7 9-7-1.63-1.27-7.38 5.74zM12 16l7.36-5.73L21 9l-9-7-9 7 1.63 1.27L12 16zM11.99 2.54L3 9.58l9 7.04 9-7.04z'/></svg>")}.ico-map{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5zM15 19l-6-2.11V5l6 2.11V19z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20.5 3l-.16.03L15 5.1 9 3 3.36 4.9c-.21.07-.36.25-.36.48V20.5c0 .28.22.5.5.5l.16-.03L9 18.9l6 2.1 5.64-1.9c.21-.07.36-.25.36-.48V3.5c0-.28-.22-.5-.5-.5zM15 19l-6-2.11V5l6 2.11V19z'/></svg>")}.ico-headset{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 1a9 9 0 0 0-9 9v7c0 1.66 1.34 3 3 3h3v-8H5v-2c0-3.87 3.13-7 7-7s7 3.13 7 7v2h-4v8h3c1.66 0 3-1.34 3-3v-7a9 9 0 0 0-9-9z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 1a9 9 0 0 0-9 9v7c0 1.66 1.34 3 3 3h3v-8H5v-2c0-3.87 3.13-7 7-7s7 3.13 7 7v2h-4v8h3c1.66 0 3-1.34 3-3v-7a9 9 0 0 0-9-9z'/></svg>")}.ico-info{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z'/></svg>")}.ico-moon{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M9.37 5.51A7.35 7.35 0 0 0 9.1 7.5c0 4.08 3.32 7.4 7.4 7.4.68 0 1.35-.09 1.99-.27A7.014 7.014 0 0 1 12 21c-5.52 0-10-4.48-10-10 0-3.21 1.56-6.05 3.97-7.87.56 1.05 1.76 1.83 3.4 2.38z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M9.37 5.51A7.35 7.35 0 0 0 9.1 7.5c0 4.08 3.32 7.4 7.4 7.4.68 0 1.35-.09 1.99-.27A7.014 7.014 0 0 1 12 21c-5.52 0-10-4.48-10-10 0-3.21 1.56-6.05 3.97-7.87.56 1.05 1.76 1.83 3.4 2.38z'/></svg>")}.ico-sun{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z'/></svg>")}.ico-calendar{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-1.99.9-1.99 2L3 19a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11zM7 10h5v5H7z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-1.99.9-1.99 2L3 19a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11zM7 10h5v5H7z'/></svg>")}.ico-clock{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z'/></svg>")}.ico-shield{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm-2 16l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z'/></svg>")}.ico-lock{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3 3.1-3 1.71 0 3.1 1.29 3.1 3v2z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3 3.1-3 1.71 0 3.1 1.29 3.1 3v2z'/></svg>")}.ico-arrow-left{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z'/></svg>")}.ico-bolt{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M11 21h-1l1-7H7.5c-.58 0-.57-.32-.38-.66.19-.34.05-.08.07-.12C8.48 10.94 10.42 7.54 13 3h1l-1 7h3.5c.49 0 .56.33.47.51l-.07.15C12.96 17.55 11 21 11 21z'/></svg>")}.ico-mobile{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17 1.01L7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17 1.01L7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14z'/></svg>")}.ico-fingerprint{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17.81 4.47c-.08 0-.16-.02-.23-.06C15.66 3.42 14 3 12.01 3c-1.98 0-3.86.47-5.57 1.41-.24.13-.54.04-.68-.2-.13-.24-.04-.55.2-.68C7.82 2.52 9.86 2 12.01 2c2.13 0 3.99.47 6.03 1.52.25.13.34.43.21.67-.09.18-.26.28-.44.28zM3.5 9.72c-.1 0-.2-.03-.29-.09-.23-.16-.28-.47-.12-.7.99-1.4 2.25-2.5 3.75-3.27C9.98 4.04 14 4.03 17.15 5.65c1.5.77 2.76 1.86 3.75 3.25.16.22.11.54-.12.7-.23.16-.54.11-.7-.12-.9-1.26-2.04-2.25-3.39-2.94-2.87-1.47-6.54-1.47-9.4.01-1.36.7-2.5 1.7-3.4 2.96-.08.14-.23.21-.39.21zm6.25 12.07c-.13 0-.26-.05-.35-.15-.87-.87-1.34-1.43-2.01-2.64-.69-1.23-1.05-2.73-1.05-4.34 0-2.97 2.54-5.39 5.66-5.39s5.66 2.42 5.66 5.39c0 .28-.22.5-.5.5s-.5-.22-.5-.5c0-2.42-2.09-4.39-4.66-4.39-2.57 0-4.66 1.97-4.66 4.39 0 1.44.32 2.77.93 3.85.64 1.15 1.08 1.64 1.85 2.42.19.2.19.51 0 .71-.11.1-.24.15-.37.15zm7.17-1.85c-1.19 0-2.24-.3-3.1-.89-1.49-1.01-2.38-2.65-2.38-4.39 0-.28.22-.5.5-.5s.5.22.5.5c0 1.41.72 2.74 1.94 3.56.71.48 1.54.71 2.54.71.24 0 .64-.03 1.04-.1.27-.05.53.13.58.41.05.27-.13.53-.41.58-.57.11-1.07.12-1.21.12zM14.91 22c-.04 0-.09-.01-.13-.02-1.59-.44-2.63-1.03-3.72-2.1-1.4-1.39-2.17-3.24-2.17-5.22 0-1.62 1.38-2.94 3.08-2.94 1.7 0 3.08 1.32 3.08 2.94 0 1.07.93 1.94 2.08 1.94.17 0 .32.02.48.05.27.05.45.31.4.58-.05.27-.3.45-.58.4-.29-.05-.56-.09-.85-.09-1.7 0-3.08-1.27-3.08-2.83 0-1.07-.88-1.94-1.97-1.94-1.09 0-1.97.87-1.97 1.94 0 1.71.66 3.31 1.87 4.51.95.94 1.86 1.46 3.27 1.85.27.07.42.35.35.61-.05.23-.26.38-.47.38z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17.81 4.47c-.08 0-.16-.02-.23-.06C15.66 3.42 14 3 12.01 3c-1.98 0-3.86.47-5.57 1.41-.24.13-.54.04-.68-.2-.13-.24-.04-.55.2-.68C7.82 2.52 9.86 2 12.01 2c2.13 0 3.99.47 6.03 1.52.25.13.34.43.21.67-.09.18-.26.28-.44.28zM3.5 9.72c-.1 0-.2-.03-.29-.09-.23-.16-.28-.47-.12-.7.99-1.4 2.25-2.5 3.75-3.27C9.98 4.04 14 4.03 17.15 5.65c1.5.77 2.76 1.86 3.75 3.25.16.22.11.54-.12.7-.23.16-.54.11-.7-.12-.9-1.26-2.04-2.25-3.39-2.94-2.87-1.47-6.54-1.47-9.4.01-1.36.7-2.5 1.7-3.4 2.96-.08.14-.23.21-.39.21zm6.25 12.07c-.13 0-.26-.05-.35-.15-.87-.87-1.34-1.43-2.01-2.64-.69-1.23-1.05-2.73-1.05-4.34 0-2.97 2.54-5.39 5.66-5.39s5.66 2.42 5.66 5.39c0 .28-.22.5-.5.5s-.5-.22-.5-.5c0-2.42-2.09-4.39-4.66-4.39-2.57 0-4.66 1.97-4.66 4.39 0 1.44.32 2.77.93 3.85.64 1.15 1.08 1.64 1.85 2.42.19.2.19.51 0 .71-.11.1-.24.15-.37.15zm7.17-1.85c-1.19 0-2.24-.3-3.1-.89-1.49-1.01-2.38-2.65-2.38-4.39 0-.28.22-.5.5-.5s.5.22.5.5c0 1.41.72 2.74 1.94 3.56.71.48 1.54.71 2.54.71.24 0 .64-.03 1.04-.1.27-.05.53.13.58.41.05.27-.13.53-.41.58-.57.11-1.07.12-1.21.12zM14.91 22c-.04 0-.09-.01-.13-.02-1.59-.44-2.63-1.03-3.72-2.1-1.4-1.39-2.17-3.24-2.17-5.22 0-1.62 1.38-2.94 3.08-2.94 1.7 0 3.08 1.32 3.08 2.94 0 1.07.93 1.94 2.08 1.94.17 0 .32.02.48.05.27.05.45.31.4.58-.05.27-.3.45-.58.4-.29-.05-.56-.09-.85-.09-1.7 0-3.08-1.27-3.08-2.83 0-1.07-.88-1.94-1.97-1.94-1.09 0-1.97.87-1.97 1.94 0 1.71.66 3.31 1.87 4.51.95.94 1.86 1.46 3.27 1.85.27.07.42.35.35.61-.05.23-.26.38-.47.38z'/></svg>")}.ico-check{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z'/></svg>")}.ico-leaf{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17 8C8 10 5.9 16.17 3.82 21.34 5.71 20.73 7.5 20 9 20c2.5 0 6-1.5 8-6 4-9-6-13-6-13s.7 5.07-2 8.5c-.86-3.23.15-7.5-.5-9.5-3.01 1-6.5 5.5-6.5 5.5s1.25-.97 2.47-1.33C9.36 6.3 6.94 7.97 5.5 11c-2 4.19-2.5 7.5-2.5 7.5s3.5-1.5 7.5-2.5c4.78-1.2 8.5-5 6.5-8z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17 8C8 10 5.9 16.17 3.82 21.34 5.71 20.73 7.5 20 9 20c2.5 0 6-1.5 8-6 4-9-6-13-6-13s.7 5.07-2 8.5c-.86-3.23.15-7.5-.5-9.5-3.01 1-6.5 5.5-6.5 5.5s1.25-.97 2.47-1.33C9.36 6.3 6.94 7.97 5.5 11c-2 4.19-2.5 7.5-2.5 7.5s3.5-1.5 7.5-2.5c4.78-1.2 8.5-5 6.5-8z'/></svg>")}.ico-store{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20 4H4v2h16V4zm1 10v-2l-1-5H4l-1 5v2h1v6h10v-6h4v6h2v-6h1zm-9 4H6v-4h6v4z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20 4H4v2h16V4zm1 10v-2l-1-5H4l-1 5v2h1v6h10v-6h4v6h2v-6h1zm-9 4H6v-4h6v4z'/></svg>")}.ico-phone{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z'/></svg>")}.ico-location-arrow{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z'/></svg>")}.ico-speed{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20.38 8.57l-1.23 1.85a8 8 0 0 1-.22 7.58H5.07A8 8 0 0 1 15.58 6.85l1.85-1.23A10 10 0 0 0 3.35 19a2 2 0 0 0 1.72 1h13.85a2 2 0 0 0 1.74-1 10 10 0 0 0-.27-10.44zm-9.79 6.84a2 2 0 0 0 2.83 0l5.66-8.49-8.49 5.66a2 2 0 0 0 0 2.83z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20.38 8.57l-1.23 1.85a8 8 0 0 1-.22 7.58H5.07A8 8 0 0 1 15.58 6.85l1.85-1.23A10 10 0 0 0 3.35 19a2 2 0 0 0 1.72 1h13.85a2 2 0 0 0 1.74-1 10 10 0 0 0-.27-10.44zm-9.79 6.84a2 2 0 0 0 2.83 0l5.66-8.49-8.49 5.66a2 2 0 0 0 0 2.83z'/></svg>")}.ico-refresh{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z'/></svg>")}.ico-chevron-left{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z'/></svg>")}.ico-chevron-down{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z'/></svg>")}.ico-network{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20 18h-3V6.83l-2 2V21h-2V8.83l-2 2V21h-2V13.85l-5.74 5.75c-.78.78-2.05.78-2.83 0l-.06-.06c-.78-.78-.78-2.05 0-2.83L12 5l1.41 1.41L4.83 15H10v-3h2v3h2v-7h2v7h3z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M20 18h-3V6.83l-2 2V21h-2V8.83l-2 2V21h-2V13.85l-5.74 5.75c-.78.78-2.05.78-2.83 0l-.06-.06c-.78-.78-.78-2.05 0-2.83L12 5l1.41 1.41L4.83 15H10v-3h2v3h2v-7h2v7h3z'/></svg>")}.ico-save{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z'/></svg>")}.ico-wifi_tethering{-webkit-mask-image:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 11c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm6 2c0-3.31-2.69-6-6-6s-6 2.69-6 6c0 2.22 1.21 4.15 3 5.19l1.42-1.42c-1.17-.68-1.97-1.95-1.97-3.42 0-2.21 1.79-4 4-4s4 1.79 4 4c0 1.47-.8 2.74-1.97 3.43l1.42 1.42C20.79 17.15 22 15.22 22 13c0-3.31-2.69-6-6-6zm-8.5 7.5h5v2h-5z"/></svg>');mask-image:url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 11c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm6 2c0-3.31-2.69-6-6-6s-6 2.69-6 6c0 2.22 1.21 4.15 3 5.19l1.42-1.42c-1.17-.68-1.97-1.95-1.97-3.42 0-2.21 1.79-4 4-4s4 1.79 4 4c0 1.47-.8 2.74-1.97 3.43l1.42 1.42C20.79 17.15 22 15.22 22 13c0-3.31-2.69-6-6-6zm-8.5 7.5h5v2h-5z"/></svg>')}.ico-alert{-webkit-mask-image:url('data:image/svg+xml;utf8,<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>');mask-image:url('data:image/svg+xml;utf8,<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>')}
.ico-home,.ico-cubes,.ico-map,.ico-headset,.ico-info{width:22px;height:22px}"""


# ─── سكربت MD5 المضمّن لدعم CHAP (نفس مصدر قالب MikroTik) ──────

_MD5_JS = """function safe_add(x,y){var lsw=(x&0xFFFF)+(y&0xFFFF);var msw=(x>>16)+(y>>16)+(lsw>>16);return(msw<<16)|(lsw&0xFFFF)}
function rol(num,cnt){return(num<<cnt)|(num>>>(32-cnt))}
function cmn(q,a,b,x,s,t){return safe_add(rol(safe_add(safe_add(a,q),safe_add(x,t)),s),b)}
function ff(a,b,c,d,x,s,t){return cmn((b&c)|((~b)&d),a,b,x,s,t)}
function gg(a,b,c,d,x,s,t){return cmn((b&d)|(c&(~d)),a,b,x,s,t)}
function hh(a,b,c,d,x,s,t){return cmn(b^c^d,a,b,x,s,t)}
function ii(a,b,c,d,x,s,t){return cmn(c^(b|(~d)),a,b,x,s,t)}
function coreMD5(x){
var a=1732584193,b=-271733879,c=-1732584194,d=271733878;
for(var i=0;i<x.length;i+=16){
var olda=a,oldb=b,oldc=c,oldd=d;
a=ff(a,b,c,d,x[i+0],7,-680876936);d=ff(d,a,b,c,x[i+1],12,-389564586);c=ff(c,d,a,b,x[i+2],17,606105819);b=ff(b,c,d,a,x[i+3],22,-1044525330);
a=ff(a,b,c,d,x[i+4],7,-176418897);d=ff(d,a,b,c,x[i+5],12,1200080426);c=ff(c,d,a,b,x[i+6],17,-1473231341);b=ff(b,c,d,a,x[i+7],22,-45705983);
a=ff(a,b,c,d,x[i+8],7,1770035416);d=ff(d,a,b,c,x[i+9],12,-1958414417);c=ff(c,d,a,b,x[i+10],17,-42063);b=ff(b,c,d,a,x[i+11],22,-1990404162);
a=ff(a,b,c,d,x[i+12],7,1804603682);d=ff(d,a,b,c,x[i+13],12,-40341101);c=ff(c,d,a,b,x[i+14],17,-1502002290);b=ff(b,c,d,a,x[i+15],22,1236535329);
a=gg(a,b,c,d,x[i+1],5,-165796510);d=gg(d,a,b,c,x[i+6],9,-1069501632);c=gg(c,d,a,b,x[i+11],14,643717713);b=gg(b,c,d,a,x[i+0],20,-373897302);
a=gg(a,b,c,d,x[i+5],5,-701558691);d=gg(d,a,b,c,x[i+10],9,38016083);c=gg(c,d,a,b,x[i+15],14,-660478335);b=gg(b,c,d,a,x[i+4],20,-405537848);
a=gg(a,b,c,d,x[i+9],5,568446438);d=gg(d,a,b,c,x[i+14],9,-1019803690);c=gg(c,d,a,b,x[i+3],14,-187363961);b=gg(b,c,d,a,x[i+8],20,1163531501);
a=gg(a,b,c,d,x[i+13],5,-1444681467);d=gg(d,a,b,c,x[i+2],9,-51403784);c=gg(c,d,a,b,x[i+7],14,1735328473);b=gg(b,c,d,a,x[i+12],20,-1926607734);
a=hh(a,b,c,d,x[i+5],4,-378558);d=hh(d,a,b,c,x[i+8],11,-2022574463);c=hh(c,d,a,b,x[i+11],16,1839030562);b=hh(b,c,d,a,x[i+14],23,-35309556);
a=hh(a,b,c,d,x[i+1],4,-1530992060);d=hh(d,a,b,c,x[i+4],11,1272893353);c=hh(c,d,a,b,x[i+7],16,-155497632);b=hh(b,c,d,a,x[i+10],23,-1094730640);
a=hh(a,b,c,d,x[i+13],4,681279174);d=hh(d,a,b,c,x[i+0],11,-358537222);c=hh(c,d,a,b,x[i+3],16,-722521979);b=hh(b,c,d,a,x[i+6],23,76029189);
a=hh(a,b,c,d,x[i+9],4,-640364487);d=hh(d,a,b,c,x[i+12],11,-421815835);c=hh(c,d,a,b,x[i+15],16,530742520);b=hh(b,c,d,a,x[i+2],23,-995338651);
a=ii(a,b,c,d,x[i+0],6,-198630844);d=ii(d,a,b,c,x[i+7],10,1126891415);c=ii(c,d,a,b,x[i+14],15,-1416354905);b=ii(b,c,d,a,x[i+5],21,-57434055);
a=ii(a,b,c,d,x[i+12],6,1700485571);d=ii(d,a,b,c,x[i+3],10,-1894986606);c=ii(c,d,a,b,x[i+10],15,-1051523);b=ii(b,c,d,a,x[i+1],21,-2054922799);
a=ii(a,b,c,d,x[i+8],6,1873313359);d=ii(d,a,b,c,x[i+15],10,-30611744);c=ii(c,d,a,b,x[i+6],15,-1560198380);b=ii(b,c,d,a,x[i+13],21,1309151649);
a=ii(a,b,c,d,x[i+4],6,-145523070);d=ii(d,a,b,c,x[i+11],10,-1120210379);c=ii(c,d,a,b,x[i+2],15,718787259);b=ii(b,c,d,a,x[i+9],21,-343485551);
a=safe_add(a,olda);b=safe_add(b,oldb);c=safe_add(c,oldc);d=safe_add(d,oldd);
}
return[a,b,c,d];
}
function binl2hex(b){var t="0123456789abcdef",s="";for(var i=0;i<b.length*4;i++){s+=t.charAt((b[i>>2]>>((i%4)*8+4))&0xF)+t.charAt((b[i>>2]>>((i%4)*8))&0xF)}return s}
function str2binl(s){var n=((s.length+8)>>6)+1,r=new Array(n*16),i;for(i=0;i<n*16;i++)r[i]=0;for(i=0;i<s.length;i++)r[i>>2]|=(s.charCodeAt(i)&0xFF)<<((i%4)*8);r[i>>2]|=0x80<<((i%4)*8);r[n*16-2]=s.length*8;return r}
function hexMD5(s){return binl2hex(coreMD5(str2binl(s)))}"""


# ─── الهيكل الأساس المشترك ──────────────────────────────────────
# %%THEME_TOKENS%% — كتلة ألوان النسخة (:root + body.dark-mode).
# %%BODY_CLASS%%   — "dark-mode" للنسخة الليلية الافتراضية.
# %%ICONS%% / %%MD5%% / %%AVATARS_JS%% — تُحقن وقت الاستيراد.
#
# تذكير صارم على هذه السلسلة (متصفح بوابة الهوت سبوت محدود):
#   - التبويبات السفلية تُبدَّل عبر CSS خالص (radio + :checked
#     + الشقيق ~). لا onclick يبدّل العرض، فلا تتعطّل عند فشل JS.
#   - JS ES5 فقط: var/function، بلا arrow أو template literals أو
#     async/await أو محلّلات URL الحديثة. كل سكربت في كتلة <script>
#     منفصلة وبـ try/catch داخلي حتى لا يُسقط خطأ في تحسين واحد
#     بقية الصفحة (الساعة/التاريخ/الحالة الحيّة كلها تحسينات).
#   - الشعار يتراجع بأمان عبر onerror إن فشل تحميله؛ لا /img/logo.png
#     مطلق يعطي 404 — المصمّم يعطينا data: أو رابطًا نسبيًا أو URL.
#   - بلا fetch على login.html (لا حاجة لها قبل تسجيل الدخول).
#   - الزر الذي يبني الفروقات: bottom-nav صار <label for=...> يُفعّل
#     <input type="radio"> مخفي، وكل قسم يُعرض بقاعدة الشقيق ~.

_BASE_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta http-equiv="pragma" content="no-cache">
<meta http-equiv="expires" content="-1">
<title>{{TENANT_NAME}}</title>
<style>
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Regular.woff2') format('woff2');font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Bold.woff2') format('woff2');font-weight:700;font-style:normal;font-display:swap}
%%THEME_TOKENS%%
/* ─── أساسيات ─── */
*{margin:0;padding:0;box-sizing:border-box;font-family:var(--font-stack)!important;outline:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg-gradient);min-height:100vh;display:block;background-attachment:fixed;color:var(--text-main)}
.mobile-container{width:100%;max-width:560px;margin:0 auto;min-height:100vh;display:flex;flex-direction:column;position:relative}

/* ─── ‏تبديل التبويبات: CSS خالص عبر radio + :checked ─── */
/* الراديوهات مخفيّة بصريًا لكنها تبقى قابلة للتفعيل عبر <label for>.
   لا نستخدم left:-9999px لأنه يوسّع الشجرة أفقيًا في RTL — نستخدم
   نمط «الإخفاء البصري» القياسي بدون تأثير على التخطيط. */
.hr-nav-r{position:absolute;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none;margin:0;padding:0;border:0;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}
/* بلا fadeIn — على المتصفحات البطيئة/المعطّلة قد تُلتقط لقطة في
   منتصف الأنيمشن فيبدو القسم باهتًا. الإظهار الفوري أسلم للقطعة. */
.view-section{display:none}
#hr-nav-home:checked ~ .content-scroll #home-view,
#hr-nav-packages:checked ~ .content-scroll #packages-view,
#hr-nav-distributors:checked ~ .content-scroll #distributors-view,
#hr-nav-support:checked ~ .content-scroll #support-view,
#hr-nav-info:checked ~ .content-scroll #info-view{display:block}
.bottom-nav label.nav-item{cursor:pointer}
#hr-nav-home:checked ~ .bottom-nav label[for="hr-nav-home"],
#hr-nav-packages:checked ~ .bottom-nav label[for="hr-nav-packages"],
#hr-nav-distributors:checked ~ .bottom-nav label[for="hr-nav-distributors"],
#hr-nav-support:checked ~ .bottom-nav label[for="hr-nav-support"],
#hr-nav-info:checked ~ .bottom-nav label[for="hr-nav-info"]{color:var(--primary-accent);font-weight:700}
#hr-nav-home:checked ~ .bottom-nav label[for="hr-nav-home"]::after,
#hr-nav-packages:checked ~ .bottom-nav label[for="hr-nav-packages"]::after,
#hr-nav-distributors:checked ~ .bottom-nav label[for="hr-nav-distributors"]::after,
#hr-nav-support:checked ~ .bottom-nav label[for="hr-nav-support"]::after,
#hr-nav-info:checked ~ .bottom-nav label[for="hr-nav-info"]::after{content:'';position:absolute;bottom:10px;width:4px;height:4px;background:var(--primary-accent);border-radius:50%}

/* ─── الشريط العلوي ─── */
.top-system-bar{height:35px;background:var(--top-bar-bg);display:flex;justify-content:space-between;align-items:center;padding:0 20px;font-size:11px;color:var(--top-bar-text);border-bottom:1px solid var(--border-color);position:sticky;top:0;z-index:100}
.ip-info{display:flex;align-items:center;gap:6px;font-family:monospace;letter-spacing:.5px}
.connection-dot{width:6px;height:6px;background:var(--pulse-color);border-radius:50%;box-shadow:0 0 5px var(--pulse-color)}
.brand-mini{font-weight:700}

/* ─── مساحة المحتوى ─── */
.content-scroll{flex:1;padding:20px 25px 90px 25px;overflow-x:hidden}

/* ─── رأس الترحيب + شارات الوقت ─── */
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
/* ‏مرساة حقن البطل تبقى فارغة (التحيّة المكرّرة أُزيلت) → نُخفيها فلا تترك فراغًا. */
.header:empty{display:none;margin:0;padding:0}
.greeting h2{font-size:17px;color:var(--text-main);font-weight:700}
.greeting p{font-size:13px;color:var(--text-sub)}
.date-time-pills{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;padding:0 5px}
/* ‏الشارة داخل بطاقة الدخول الآن (أعلى العنوان): إزالة الحشو الجانبيّ + فاصل سفليّ. */
.insurance-card .date-time-pills{margin:0 0 14px;padding:0;position:relative;z-index:2}
/* ‏تصغير بطاقة البطل (الرسمة العلويّة) لكل القوالب ~22% مع حفظ النسب (zoom يُعيد
   التدفّق فتُصبح الصفحة أكثر إحكامًا، بلا تشويه). البطل يُحقَن كأوّل ابن لـ#home-view
   وجذره دومًا صنفٌ ينتهي بـ"-hero". */
#home-view>[class$="-hero"]{zoom:.78}
.dt-pill{background:var(--pill-bg);border:1px solid var(--pill-border);padding:6px 12px;border-radius:20px;font-size:11px;font-weight:600;color:var(--text-sub);display:flex;align-items:center;gap:6px}
.dt-pill.time-pill{color:var(--primary-accent);font-family:monospace;letter-spacing:.5px;font-weight:700;direction:ltr}

/* ─── بطاقة الدخول الموحّدة ─── */
.unified-gradient-card{background:var(--main-gradient);border-radius:var(--card-radius);padding:22px;color:#fff;position:relative;overflow:hidden;margin-bottom:25px;box-shadow:0 15px 35px var(--main-shadow-color);border:1px solid rgba(255,255,255,0.15)}
.insurance-card{min-height:230px;display:flex;flex-direction:column;justify-content:space-between}
.circle-decor{position:absolute;border-radius:50%;background:rgba(255,255,255,0.1);pointer-events:none}
.c1{width:160px;height:160px;top:-60px;right:-30px}
.c2{width:220px;height:220px;bottom:-90px;left:30px}
.card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;z-index:2;position:relative}
.card-icon{display:flex;align-items:center;gap:12px}
.icon-box{background:rgba(255,255,255,0.2);width:40px;height:40px;border-radius:12px;display:flex;justify-content:center;align-items:center}
.top-arrow{background:rgba(255,255,255,0.2);width:30px;height:30px;border-radius:50%;display:flex;justify-content:center;align-items:center;font-size:12px}
.login-fields-container{display:flex;flex-direction:column;align-items:stretch;margin-bottom:15px;position:relative;z-index:2;gap:12px}
.field-group{flex:1;display:flex;flex-direction:column}
.field-label{opacity:0.9;font-weight:500;font-size:11px;margin-bottom:5px;color:#e0f2fe}
.custom-input{background:transparent;border:1px solid rgba(255,255,255,0.4);border-radius:25px;color:#fff;font-weight:600;font-size:14px;padding:8px 15px;width:100%;outline:0}
.custom-input:focus{border-color:#fff;box-shadow:0 0 10px rgba(255,255,255,0.2)}
.custom-input::placeholder{color:rgba(255,255,255,0.6);font-weight:400}
.card-footer{display:flex;justify-content:center;position:relative;z-index:2}
.login-btn{background:#fff;color:var(--primary-accent);padding:12px 28px;border-radius:20px;border:0;font-size:13px;font-weight:700;cursor:pointer;box-shadow:0 5px 15px rgba(0,0,0,.1);display:inline-flex;align-items:center;gap:8px}
.mikrotik-error{background:rgba(239,68,68,0.92);color:#fff;padding:10px;border-radius:12px;font-size:12px;margin-bottom:15px;text-align:center;display:flex;align-items:center;justify-content:center;gap:8px;box-shadow:0 5px 15px rgba(239,68,68,0.3);border:1px solid rgba(255,255,255,0.2);position:relative;z-index:5}

/* ─── زر «متجرك الإلكتروني» — يظهر فقط عند STORE_ENABLED=yes ─── */
.hr-store-card{display:none;align-items:center;gap:14px;background:var(--main-gradient);border-radius:18px;padding:16px 18px;color:#fff;text-decoration:none;margin-bottom:25px;box-shadow:0 12px 28px var(--main-shadow-color);border:1px solid rgba(255,255,255,0.18);position:relative;overflow:hidden}
body.hr-store-on .hr-store-card{display:flex}
.hr-store-icon{width:46px;height:46px;border-radius:14px;background:rgba(255,255,255,0.22);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.hr-store-text{flex:1;position:relative;z-index:2}
.hr-store-text h4{font-size:14px;font-weight:800;margin-bottom:3px}
.hr-store-text p{font-size:11px;opacity:.9}
.hr-store-arrow{width:30px;height:30px;border-radius:50%;background:rgba(255,255,255,0.2);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.ico-cart{-webkit-mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.14 0-.25-.11-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49c.08-.14.12-.31.12-.48 0-.55-.45-1-1-1H5.21l-.94-2H1zm16 16c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2z'/></svg>");mask-image:url("data:image/svg+xml;utf8,<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'><path d='M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.14 0-.25-.11-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49c.08-.14.12-.31.12-.48 0-.55-.45-1-1-1H5.21l-.94-2H1zm16 16c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2z'/></svg>")}

/* ─── بطاقة الدعم ─── */
.support-hero-card{text-align:center}
.support-icon-ring{width:70px;height:70px;background:rgba(255,255,255,0.2);border-radius:50%;display:flex;justify-content:center;align-items:center;margin:0 auto 15px auto;color:#fff;font-size:28px;box-shadow:0 0 0 8px rgba(255,255,255,0.1)}
.support-hero-card .support-title{font-size:18px;font-weight:800;color:#fff;margin-bottom:5px}
.support-hero-card .support-sub{font-size:12px;color:rgba(255,255,255,0.9);margin-bottom:20px}
.btn-call-main{background:#fff;color:var(--primary-accent);padding:12px 30px;border-radius:50px;text-decoration:none;font-weight:700;font-size:14px;display:inline-flex;align-items:center;gap:8px;box-shadow:0 5px 15px rgba(0,0,0,0.1);direction:ltr}
.server-status-box{background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.2);border-radius:12px;padding:12px 15px;display:flex;align-items:center;justify-content:space-between;margin-top:20px}
.server-status-box .status-label{font-size:11px;font-weight:700;color:#fff;display:flex;align-items:center;gap:6px}
.server-status-box .status-percent{font-size:14px;font-weight:800;color:#bfdbfe}

/* ─── بطاقة الحالة المباشرة + المعادل ─── */
.network-pulse-card{background:var(--card-bg);border-radius:20px;padding:15px;display:flex;align-items:center;gap:15px;margin-bottom:25px;box-shadow:var(--box-shadow);border:1px solid var(--border-color);position:relative;overflow:hidden}
.pulse-icon-area{position:relative;width:45px;height:45px;background:var(--pill-bg);border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;color:var(--primary-accent)}
.live-indicator{position:absolute;top:0;right:0;width:12px;height:12px;background:var(--pulse-color);border:2px solid var(--card-bg);border-radius:50%;display:flex;align-items:center;justify-content:center}
.blink-dot{width:100%;height:100%;background:var(--pulse-color);border-radius:50%;animation:ping 1.5s cubic-bezier(0,0,0.2,1) infinite;opacity:.75}
@keyframes ping{75%,100%{transform:scale(2);opacity:0}}
.pulse-content{flex:1;display:flex;flex-direction:column;justify-content:center}
.pulse-label{font-size:11px;color:var(--text-sub);font-weight:600;margin-bottom:2px}
.pulse-value{font-size:14px;font-weight:800;color:var(--text-main)}
.network-visualizer{display:flex;align-items:flex-end;gap:3px;height:20px;padding-bottom:3px}
.network-visualizer .bar{width:4px;background-color:var(--eq-1);border-radius:2px;animation:equalizer 1s ease-in-out infinite}
.network-visualizer .bar:nth-child(1){height:40%;animation-delay:0s}
.network-visualizer .bar:nth-child(2){height:80%;animation-delay:.2s;background-color:var(--eq-2)}
.network-visualizer .bar:nth-child(3){height:50%;animation-delay:.4s;background-color:var(--eq-3)}
.network-visualizer .bar:nth-child(4){height:70%;animation-delay:.1s}
@keyframes equalizer{0%,100%{transform:scaleY(1);opacity:1}50%{transform:scaleY(0.5);opacity:0.7}}

/* ─── العناوين الفرعية + التذييل ─── */
.section-title{font-size:16px;color:var(--text-main);font-weight:700;margin-bottom:15px;display:flex;align-items:center;justify-content:space-between}
.section-title span{font-size:12px;color:var(--primary-accent)}
.network-about-footer{text-align:center;padding:20px;background:var(--card-bg);border-radius:20px;border:1px solid var(--border-color);box-shadow:var(--box-shadow);margin-top:10px}
.footer-title{color:var(--primary-accent);font-size:16px;font-weight:800;margin-bottom:5px}
.footer-desc{font-size:11px;color:var(--text-sub);margin-bottom:8px}
.footer-copyright{font-size:10px;color:var(--text-sub);opacity:.7;border-top:1px solid var(--border-color);padding-top:8px;margin-top:8px}

/* ─── الباقات (لما يولّده _offers_html) ─── */
.packages-wrapper{display:flex;flex-direction:column;gap:12px;padding-bottom:20px}
.pkg-card-big{background:var(--card-gradient-1);border-radius:24px;padding:20px;color:#fff;position:relative;overflow:hidden;box-shadow:0 15px 35px var(--main-shadow-color);border:1px solid rgba(255,255,255,0.1)}
.glow-blob{position:absolute;border-radius:50%;filter:blur(40px);pointer-events:none}
.gb-1{top:-30px;left:-30px;width:120px;height:120px;background:rgba(255,255,255,0.2)}
.gb-2{bottom:0;right:0;width:100px;height:100px;background:var(--main-shadow-color)}
.pkg-badge-top{position:absolute;top:0;left:0;background:#fff;color:var(--primary-accent);font-size:11px;font-weight:800;padding:5px 15px;border-bottom-right-radius:15px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}
.pkg-header-row{display:flex;justify-content:space-between;align-items:flex-start;margin-top:10px;position:relative;z-index:2}
.pkg-icon-circle{width:45px;height:45px;background:rgba(255,255,255,0.2);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;color:#fff}
.pkg-price-row{display:flex;align-items:baseline;gap:5px;margin:15px 0;position:relative;z-index:2}
.pkg-big-price{font-size:32px;font-weight:800;line-height:1}
.pkg-card-medium{background:var(--card-gradient-2);border-radius:20px;padding:18px;color:#fff;position:relative;overflow:hidden;box-shadow:0 10px 25px var(--main-shadow-color);display:flex;flex-direction:column;justify-content:space-between;min-height:110px}
.medium-blob{position:absolute;top:-20px;right:-20px;width:80px;height:80px;background:rgba(255,255,255,0.1);border-radius:50%}
.medium-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;position:relative;z-index:2}
.medium-tags{display:flex;gap:5px;margin-top:5px}
.m-tag{font-size:10px;background:rgba(255,255,255,0.15);padding:2px 8px;border-radius:6px}
.medium-bottom{display:flex;justify-content:space-between;align-items:flex-end;position:relative;z-index:2}
.pkg-card-small{background:var(--card-bg);border:1px solid var(--border-color);border-radius:18px;padding:15px;display:flex;justify-content:space-between;align-items:center;box-shadow:var(--box-shadow)}
.small-info h4{font-size:14px;font-weight:700;color:var(--text-main);margin-bottom:4px}
.small-details{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-sub)}
.small-price{text-align:left}
.s-price-val{font-size:18px;font-weight:800;color:var(--text-main)}

/* ─── الموزعون + الخريطة ─── */
.map-wrapper{background:var(--card-bg);border-radius:20px;padding:5px;box-shadow:var(--box-shadow);border:1px solid var(--border-color);margin-bottom:20px;position:relative;overflow:hidden;height:200px}
.map-art{width:100%;height:100%;border-radius:16px;position:relative;overflow:hidden;background:var(--map-bg);background-image:linear-gradient(var(--map-grid) 1px,transparent 1px),linear-gradient(90deg,var(--map-grid) 1px,transparent 1px);background-size:34px 34px}
.map-art::before{content:'';position:absolute;inset:0;background:radial-gradient(circle at 50% 50%,var(--main-shadow-color) 0,transparent 55%)}
.map-road{position:absolute;background:var(--map-road);border-radius:8px}
.mr1{width:140%;height:14px;top:38%;left:-20%;transform:rotate(-7deg)}
.mr2{width:14px;height:140%;top:-20%;left:58%;transform:rotate(10deg)}
.map-pin-user{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:15px;height:15px;background:var(--primary-accent);border:3px solid #fff;border-radius:50%;box-shadow:0 0 15px var(--main-shadow-color);z-index:10}
.map-overlay-info{position:absolute;bottom:15px;right:15px;background:rgba(255,255,255,0.95);padding:6px 12px;border-radius:20px;font-size:10px;font-weight:700;color:var(--primary-accent)}
.distributor-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:16px;padding:15px;display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;box-shadow:var(--box-shadow)}
.dist-info{display:flex;align-items:center;gap:12px}
.dist-icon{width:45px;height:45px;background:var(--pill-bg);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--primary-accent);border:1px solid transparent}
.dist-text h4{font-size:13px;font-weight:700;color:var(--text-main);margin-bottom:3px}
.dist-text p{font-size:11px;color:var(--text-sub)}

/* ─── FAQ — تستخدم <details> الأصلي (لا JS) ─── */
.faq-item{background:var(--card-bg);border:1px solid var(--border-color);border-radius:14px;overflow:hidden;margin-bottom:10px;box-shadow:var(--box-shadow)}
.faq-header{padding:15px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;font-size:12px;font-weight:700;color:var(--text-main);list-style:none}
.faq-header::-webkit-details-marker{display:none}
.faq-body{background:var(--element-bg)}
.faq-content{padding:15px;font-size:11px;color:var(--text-sub);line-height:1.5;border-top:1px solid var(--border-color)}

/* ─── قسم «معلومات» ─── */
.decor-profile{position:absolute;border-radius:50%;background:rgba(255,255,255,0.1);pointer-events:none}
.dp1{width:200px;height:200px;top:-60px;right:-40px}
.dp2{width:100px;height:100px;bottom:10px;left:-20px;border:2px solid rgba(255,255,255,0.15);background:transparent}
.profile-header{display:flex;align-items:center;gap:15px;margin-bottom:20px;padding-bottom:20px;border-bottom:1px solid rgba(255,255,255,0.2);position:relative;z-index:2}
.tech-details-list{display:flex;flex-direction:column;gap:10px;position:relative;z-index:2}
.tech-row{display:flex;justify-content:space-between;align-items:center;background:rgba(255,255,255,0.15);padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,0.1)}
.tech-label{font-size:11px;color:rgba(255,255,255,0.95);display:flex;align-items:center;gap:8px;font-weight:600}
.tech-val{font-size:11px;font-weight:700;color:#fff;font-family:monospace}
.status-badge{background:#fff;color:var(--primary-accent);font-size:10px;padding:3px 8px;border-radius:6px;font-weight:800}

%%ICONS%%

/* ─── شريط التبويب السفلي (الآن labels لا divs) ─── */
.bottom-nav{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:100%;max-width:560px;height:70px;background:var(--card-bg);border-top:1px solid var(--border-color);display:flex;justify-content:space-around;align-items:center;border-top-left-radius:25px;border-top-right-radius:25px;box-shadow:0 -5px 20px rgba(0,0,0,0.05);z-index:1000}
.nav-item{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:var(--text-sub);font-size:10px;width:60px;height:100%;position:relative;user-select:none;-webkit-user-select:none;text-decoration:none}
.nav-item .ico{font-size:18px}
</style>
</head>
<body class="%%BODY_CLASS%%">

$(if chap-id)
<form name="sendin" action="$(link-login-only)" method="post" style="display:none">
<input type="hidden" name="username">
<input type="hidden" name="password">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
</form>
<script>
/* MD5 (Paul Johnston, BSD) — مضمّن لدعم CHAP بلا ملفات خارجية */
%%MD5%%
window.hrChap=function(p){return hexMD5('$(chap-id)'+p+'$(chap-challenge)')};
</script>
$(endif)

<div class="mobile-container">

  <!-- ‏مفاتيح التبويب المخفيّة (radio) — تتحكم بكل ما يليها عبر :checked ~ -->
  <input class="hr-nav-r" type="radio" name="hr-nav" id="hr-nav-home" checked>
  <input class="hr-nav-r" type="radio" name="hr-nav" id="hr-nav-packages">
  <input class="hr-nav-r" type="radio" name="hr-nav" id="hr-nav-distributors">
  <input class="hr-nav-r" type="radio" name="hr-nav" id="hr-nav-support">
  <input class="hr-nav-r" type="radio" name="hr-nav" id="hr-nav-info">

  <div class="top-system-bar">
    <div class="ip-info"><div class="connection-dot"></div><span>IP: <span id="user-ip">$(ip)</span></span></div>
    <div class="brand-mini">{{TENANT_NAME}}</div>
  </div>

  <div class="content-scroll">

    <!-- ===================== الرئيسية ===================== -->
    <div id="home-view" class="view-section">
      <!-- ‏مرساة حقن البطل لكل قالب (تبقى فارغة؛ تُخفى بـ.header:empty).
           ‏التحيّة المكرّرة أُزيلت — الترحيب يبقى في الفوتر (footer-desc). -->
      <header class="header"></header>
      <div class="insurance-card unified-gradient-card">
        <div class="circle-decor c1"></div><div class="circle-decor c2"></div>
        <!-- ‏شارة الوقت/اليوم/التاريخ — نُقلت إلى أعلى البطاقة فوق العنوان. -->
        <div class="date-time-pills">
          <div class="dt-pill"><span class="ico ico-calendar"></span> <span id="date-display">اليوم</span></div>
          <div class="dt-pill time-pill"><span class="ico ico-clock"></span> <span id="time-display">--:--</span></div>
        </div>
        <div class="card-header">
          <div class="card-icon">
            <div class="icon-box"><span class="ico ico-shield" style="font-size:20px;"></span></div>
            <div>
              <h3 style="font-size:18px;font-weight:700;">بوابة الدخول</h3>
              <p style="font-size:12px;opacity:.9;">سجّل بيانات البطاقة</p>
            </div>
          </div>
          <div class="top-arrow"><span class="ico ico-lock" style="font-size:12px;"></span></div>
        </div>
        $(if error)<div class="mikrotik-error"><span class="ico ico-alert" style="font-size:16px;"></span><span>$(error)</span></div>$(endif)
        <form name="login" action="$(link-login-only)" method="post" onsubmit="return hrSubmit()">
          <input type="hidden" name="dst" value="$(link-orig)">
          <input type="hidden" name="popup" value="true">
          <div class="login-fields-container">
            <div class="field-group">
              <label class="field-label">رقم البطاقة</label>
              <input type="text" name="username" class="custom-input user-field" placeholder="User ID" value="$(username)">
            </div>
            <div class="field-group">
              <label class="field-label">رمز المرور</label>
              <input type="password" name="password" class="custom-input pass-field" placeholder="••••••••">
            </div>
          </div>
          <div class="card-footer">
            <button type="submit" class="login-btn">تحقق ودخول <span class="ico ico-arrow-left"></span></button>
          </div>
        </form>
      </div>

      <!-- ‏زر متجرك الإلكتروني — يظهر فقط عند STORE_ENABLED=yes -->
      <a class="hr-store-card" href="{{STORE_URL}}">
        <div class="hr-store-icon"><span class="ico ico-cart" style="font-size:22px;"></span></div>
        <div class="hr-store-text"><h4>متجر البطاقات الإلكتروني</h4><p>اشحن رصيدك واشترِ بطاقتك مباشرة من هنا</p></div>
        <div class="hr-store-arrow"><span class="ico ico-arrow-left" style="font-size:13px;"></span></div>
      </a>

      <div class="network-pulse-card">
        <div class="pulse-icon-area"><div class="live-indicator"><span class="blink-dot"></span></div><span class="ico ico-wifi_tethering" style="font-size:20px;"></span></div>
        <div class="pulse-content"><span class="pulse-label">الحالة المباشرة</span><div class="pulse-value-wrapper"><span id="liveStatusText" class="pulse-value">إشارة مستقرة وممتازة</span></div></div>
        <div class="network-visualizer"><div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="bar"></div></div>
      </div>

      <div class="network-about-footer">
        <h4 class="footer-title">{{TENANT_NAME}}</h4>
        <p class="footer-desc">{{WELCOME_TEXT}}</p>
        <div class="footer-copyright">© {{TENANT_NAME}} — جميع الحقوق محفوظة</div>
      </div>
    </div><!-- /home-view -->

    <!-- ===================== الباقات ===================== -->
    <div id="packages-view" class="view-section">
      <div class="section-title" style="margin-top:10px;">
        <h3>بطاقات متنوعة</h3>
        <span>{{TENANT_NAME}}</span>
      </div>
      <div class="packages-wrapper">
        <!-- العروض القابلة للتحرير — تُولَّد من OFFERS_JSON عبر render() -->
        {{OFFERS_HTML}}
      </div>
    </div>

    <!-- ===================== الموزعون ===================== -->
    <div id="distributors-view" class="view-section">
      <div class="section-title" style="margin-top:10px;">
        <h3>أقرب النقاط</h3>
        <span>تغطية شاملة</span>
      </div>
      <div class="map-wrapper">
        <div class="map-art"><div class="map-road mr1"></div><div class="map-road mr2"></div></div>
        <div class="map-pin-user"></div>
        <div class="map-overlay-info"><span class="ico ico-location-arrow"></span> أنت هنا</div>
      </div>
      <div class="section-title"><h3>نقاط البيع المعتمدة</h3><span>عدد النقاط</span></div>
      <div class="distributors-list">
        <!-- الموزعون القابلون للتحرير — من DISTRIBUTORS_JSON -->
        {{DISTRIBUTORS_HTML}}
      </div>
    </div>

    <!-- ===================== الدعم ===================== -->
    <div id="support-view" class="view-section">
      <div class="section-title" style="margin-top:10px;"><h3>الدعم الفني</h3><span>نحن هنا لمساعدتك</span></div>
      <div class="support-hero-card unified-gradient-card">
        <div class="support-icon-ring"><span class="ico ico-headset" style="font-size:30px;"></span></div>
        <h2 class="support-title">هل تواجه مشكلة؟</h2>
        <p class="support-sub">فريق دعم {{TENANT_NAME}} جاهز لمساعدتك — اتصل على {{SUPPORT_PHONE}}</p>
        <a href="tel:{{SUPPORT_PHONE}}" class="btn-call-main"><span class="ico ico-phone"></span> {{SUPPORT_PHONE}}</a>
        <div class="server-status-box">
          <div class="status-label"><div style="width:8px;height:8px;background:#a5f3fc;border-radius:50%;margin-left:5px;"></div>الحالة العامة للنظام</div>
          <div class="status-percent">98%</div>
        </div>
      </div>
      <div class="section-title"><h3>الأسئلة الشائعة</h3></div>
      <!-- FAQ بـ <details> الأصلي — يفتح/يُغلق بلا JS -->
      <details class="faq-item">
        <summary class="faq-header"><span>الصفحة لا تفتح بعد الاتصال</span><span class="ico ico-chevron-down"></span></summary>
        <div class="faq-body"><div class="faq-content">قم بعمل "نسيان للشبكة" (Forget Network) من إعدادات الواي فاي في جهازك، ثم أعد الاتصال وأدخل كلمة المرور مرة أخرى.</div></div>
      </details>
      <details class="faq-item">
        <summary class="faq-header"><span>الإنترنت بطيء جداً</span><span class="ico ico-chevron-down"></span></summary>
        <div class="faq-body"><div class="faq-content">جودة الاتصال تعتمد على قوة الإشارة. كلما كنت أقرب من نقطة التوزيع (الراوتر) وبدون حواجز، كانت السرعة أفضل.</div></div>
      </details>
      <details class="faq-item">
        <summary class="faq-header"><span>خطأ: المستخدم مسجل الدخول بالفعل</span><span class="ico ico-chevron-down"></span></summary>
        <div class="faq-body"><div class="faq-content">هذا يعني أن حسابك نشط على جهاز آخر أو أن الجلسة السابقة لم تغلق بشكل صحيح. انتظر دقيقتين أو اتصل بالدعم لإنهاء الجلسة المعلقة.</div></div>
      </details>
    </div>

    <!-- ===================== معلومات ===================== -->
    <div id="info-view" class="view-section">
      <div class="section-title" style="margin-top:10px;"><h3>معلومات الجلسة</h3><span>بيانات الاتصال</span></div>
      <div class="profile-card-tech unified-gradient-card">
        <div class="decor-profile dp1"></div><div class="decor-profile dp2"></div>
        <div class="profile-header">
          <div class="profile-text"><h3 id="profile-view-name">$(username)</h3><span class="status-badge">مستخدم نشط</span></div>
        </div>
        <div class="tech-details-list">
          <div class="tech-row"><div class="tech-label"><span class="ico ico-network" style="color:white"></span> IP Address</div><div class="tech-val" dir="ltr">$(ip)</div></div>
          <div class="tech-row"><div class="tech-label"><span class="ico ico-fingerprint" style="color:white"></span> MAC Address</div><div class="tech-val" dir="ltr">$(mac)</div></div>
          <div class="tech-row"><div class="tech-label"><span class="ico ico-mobile" style="color:white"></span> الجهاز</div><div class="tech-val" id="info-device">جهازك</div></div>
        </div>
      </div>
    </div>

  </div><!-- /content-scroll -->

  <!-- ‏الشريط السفلي: labels تُفعّل الراديوهات أعلاه (CSS خالص). -->
  <nav class="bottom-nav">
    <label class="nav-item" for="hr-nav-home"><span class="ico ico-home"></span><span>الرئيسية</span></label>
    <label class="nav-item" for="hr-nav-packages"><span class="ico ico-cubes"></span><span>الباقات</span></label>
    <label class="nav-item" for="hr-nav-distributors"><span class="ico ico-map"></span><span>الموزعون</span></label>
    <label class="nav-item" for="hr-nav-support"><span class="ico ico-headset"></span><span>الدعم</span></label>
    <label class="nav-item" for="hr-nav-info"><span class="ico ico-info"></span><span>معلومات</span></label>
  </nav>
</div>

<!-- ─── SCRIPT 1: تسليم النموذج (CHAP أو PAP) — جوهري للدخول. ES5. ─── -->
<script>
function hrSubmit(){
  try{
    if(typeof window.hrChap==='function' && document.sendin){
      document.sendin.username.value=document.login.username.value;
      document.sendin.password.value=window.hrChap(document.login.password.value);
      document.sendin.submit();
      return false;
    }
  }catch(e){ /* اتركها تذهب لمسار PAP العادي */ }
  return true;
}
</script>

<!-- ─── SCRIPT 2: تفعيل صنف زر المتجر (مستقل) ─── -->
<script>
try{
  if('{{STORE_ENABLED}}'==='yes' && document.body && document.body.classList){
    document.body.classList.add('hr-store-on');
  }
}catch(e){}
</script>

<!-- ─── SCRIPT 3: الساعة + التاريخ (تحسين اختياري) ─── -->
<script>
(function(){
  try{
    var days=['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'];
    var months=['يناير','فبراير','مارس','أبريل','مايو','يونيو','يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر'];
    function pad(n){return n<10?'0'+n:''+n;}
    function tick(){
      try{
        var d=new Date(), h=d.getHours(), m=d.getMinutes();
        var ap=(h<12?'ص':'م'); var hh=h%12; if(hh===0) hh=12;
        var t=document.getElementById('time-display');
        if(t) t.firstChild?(t.firstChild.nodeValue=hh+':'+pad(m)+' '+ap):(t.innerHTML=hh+':'+pad(m)+' '+ap);
        var dd=document.getElementById('date-display');
        if(dd) dd.innerHTML=days[d.getDay()]+' '+d.getDate()+' '+months[d.getMonth()];
      }catch(e){}
    }
    tick();
    setInterval(tick,1000);
  }catch(e){}
})();
</script>

<!-- ─── SCRIPT 4: كشف الجهاز (تحسين اختياري) ─── -->
<script>
(function(){
  try{
    var u=navigator.userAgent||'', t='جهازك';
    if(/Android/i.test(u)) t='Android';
    else if(/iPhone|iPad|iPod/i.test(u)) t='Apple iOS';
    else if(/Windows/i.test(u)) t='Windows';
    else if(/Mac/i.test(u)) t='Macintosh';
    var el=document.getElementById('info-device');
    if(el) el.innerHTML=t;
  }catch(e){}
})();
</script>

<!-- ─── SCRIPT 5: تدوير رسائل الحالة (تحسين بصري) ─── -->
<script>
(function(){
  try{
    var el=document.getElementById('liveStatusText');
    if(!el) return;
    var msgs=['إشارة مستقرة وممتازة','جميع السيرفرات متصلة','فحص الأمان: آمن','زمن استجابة جيد'];
    var i=0;
    setInterval(function(){
      try{ i=(i+1)%msgs.length; el.innerHTML=msgs[i]; }catch(e){}
    },4000);
  }catch(e){}
})();
</script>

</body>
</html>"""


# ─── كتل الألوان الثلاث ─────────────────────────────────────────
# كل كتلة تعرّف نفس أسماء المتغيّرات؛ الاختلاف ألوان فقط.
# {{ACCENT_COLOR}} و {{BG_COLOR}} تبقيان للمشغّل عبر مصمّم الصفحة.

_TOKENS_GRADIENT_PRO = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --main-gradient: linear-gradient(135deg, #22d3ee 0%, #3b82f6 50%, #a855f7 100%);
    --card-gradient-1: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
    --card-gradient-2: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
    --main-shadow-color: rgba(59, 130, 246, 0.3);
    --primary-accent: {{ACCENT_COLOR}};
    --bg-gradient: linear-gradient(180deg, {{BG_COLOR}} 0%, #ffffff 100%);
    --text-main: #1e293b; --text-sub: #64748B; --card-bg: #ffffff; --element-bg: #F8FAFC;
    --border-color: #E2E8F0; --box-shadow: 0 10px 30px rgba(0,0,0,0.06);
    --top-bar-bg: rgba(255,255,255,0.9); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 24px;
    --pulse-color: #22d3ee;
    --pill-bg: rgba(59,130,246,0.1); --pill-border: rgba(59,130,246,0.2);
    --eq-1: #3b82f6; --eq-2: #8b5cf6; --eq-3: #06b6d4;
    --map-bg: #e0f2fe; --map-grid: rgba(59,130,246,0.12); --map-road: rgba(255,255,255,0.85);
}
body.dark-mode{--main-gradient:linear-gradient(135deg,#0e7490 0%,#1d4ed8 50%,#7e22ce 100%);--card-gradient-1:linear-gradient(135deg,#4338ca 0%,#7e22ce 100%);--card-gradient-2:linear-gradient(135deg,#0891b2 0%,#1d4ed8 100%);--main-shadow-color:rgba(59,130,246,0.4);--bg-gradient:linear-gradient(135deg,#0f172a 0%,#1e1b4b 100%);--text-main:#F1F5F9;--text-sub:#94A3B8;--card-bg:#1e293b;--element-bg:rgba(255,255,255,0.05);--border-color:rgba(59,130,246,0.2);--top-bar-bg:rgba(15,23,42,0.9);--top-bar-text:#818cf8;--pill-bg:rgba(255,255,255,0.05);--pill-border:rgba(255,255,255,0.1);--map-bg:#0f172a;--map-grid:rgba(129,140,248,0.15);--map-road:rgba(255,255,255,0.12)}"""

_TOKENS_ROYAL_NIGHT = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --main-gradient: linear-gradient(135deg, #818cf8 0%, #6366f1 50%, #8b5cf6 100%);
    --card-gradient-1: linear-gradient(135deg, #6d28d9 0%, #4f46e5 100%);
    --card-gradient-2: linear-gradient(135deg, #7c3aed 0%, #312e81 100%);
    --main-shadow-color: rgba(99, 102, 241, 0.3);
    --primary-accent: {{ACCENT_COLOR}};
    --bg-gradient: linear-gradient(180deg, {{BG_COLOR}} 0%, #ffffff 100%);
    --text-main: #1e1b4b; --text-sub: #6b7280; --card-bg: #ffffff; --element-bg: #F5F3FF;
    --border-color: #E9E5FF; --box-shadow: 0 10px 30px rgba(49,46,129,0.08);
    --top-bar-bg: rgba(255,255,255,0.9); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 24px;
    --pulse-color: #a78bfa;
    --pill-bg: rgba(99,102,241,0.1); --pill-border: rgba(99,102,241,0.2);
    --eq-1: #6366f1; --eq-2: #a78bfa; --eq-3: #f59e0b;
    --map-bg: #ede9fe; --map-grid: rgba(99,102,241,0.14); --map-road: rgba(255,255,255,0.85);
}
body.dark-mode{--main-gradient:linear-gradient(135deg,#312e81 0%,#4c1d95 55%,#1e1b4b 100%);--card-gradient-1:linear-gradient(135deg,#4c1d95 0%,#312e81 100%);--card-gradient-2:linear-gradient(135deg,#3730a3 0%,#1e1b4b 100%);--main-shadow-color:rgba(124,58,237,0.45);--bg-gradient:linear-gradient(135deg,#0c0a1f 0%,#1e1b4b 100%);--text-main:#EDE9FE;--text-sub:#A5B4FC;--card-bg:#181433;--element-bg:rgba(255,255,255,0.05);--border-color:rgba(139,92,246,0.25);--top-bar-bg:rgba(12,10,31,0.9);--top-bar-text:#c4b5fd;--pulse-color:#fbbf24;--pill-bg:rgba(255,255,255,0.06);--pill-border:rgba(196,181,253,0.18);--eq-1:#818cf8;--eq-2:#c4b5fd;--eq-3:#fbbf24;--map-bg:#13102b;--map-grid:rgba(196,181,253,0.14);--map-road:rgba(255,255,255,0.1)}"""

_TOKENS_EMERALD = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --main-gradient: linear-gradient(135deg, #34d399 0%, #0d9488 55%, #0e7490 100%);
    --card-gradient-1: linear-gradient(135deg, #059669 0%, #0d9488 100%);
    --card-gradient-2: linear-gradient(135deg, #14b8a6 0%, #0369a1 100%);
    --main-shadow-color: rgba(13, 148, 136, 0.3);
    --primary-accent: {{ACCENT_COLOR}};
    --bg-gradient: linear-gradient(180deg, {{BG_COLOR}} 0%, #ffffff 100%);
    --text-main: #134e4a; --text-sub: #5f6f6d; --card-bg: #ffffff; --element-bg: #F0FDF9;
    --border-color: #D6F5EC; --box-shadow: 0 10px 30px rgba(13,148,136,0.08);
    --top-bar-bg: rgba(255,255,255,0.9); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 24px;
    --pulse-color: #2dd4bf;
    --pill-bg: rgba(13,148,136,0.1); --pill-border: rgba(13,148,136,0.2);
    --eq-1: #0d9488; --eq-2: #34d399; --eq-3: #0ea5e9;
    --map-bg: #d8f7ef; --map-grid: rgba(13,148,136,0.14); --map-road: rgba(255,255,255,0.85);
}
body.dark-mode{--main-gradient:linear-gradient(135deg,#065f46 0%,#0f766e 55%,#155e75 100%);--card-gradient-1:linear-gradient(135deg,#047857 0%,#115e59 100%);--card-gradient-2:linear-gradient(135deg,#0f766e 0%,#0c4a6e 100%);--main-shadow-color:rgba(45,212,191,0.35);--bg-gradient:linear-gradient(135deg,#04201c 0%,#0f3d38 100%);--text-main:#ECFDF5;--text-sub:#99C9C0;--card-bg:#0e2b27;--element-bg:rgba(255,255,255,0.05);--border-color:rgba(45,212,191,0.22);--top-bar-bg:rgba(4,32,28,0.9);--top-bar-text:#5eead4;--pill-bg:rgba(255,255,255,0.06);--pill-border:rgba(94,234,212,0.18);--map-bg:#0a201c;--map-grid:rgba(94,234,212,0.14);--map-road:rgba(255,255,255,0.1)}"""


def _build(tokens: str, body_class: str) -> str:
    """يركّب السلسلة النهائية للقالب وقت الاستيراد."""
    return (_BASE_HTML
            .replace("%%THEME_TOKENS%%", tokens)
            .replace("%%BODY_CLASS%%", body_class)
            .replace("%%ICONS%%", _ICONS_CSS)
            .replace("%%MD5%%", _MD5_JS)
            .replace("%%AVATARS_JS%%", _AVATARS_JS))


# السلاسل النهائية الثلاث — كاملة وثابتة كبقية الكتالوج.
GRADIENT_PRO_HTML = _build(_TOKENS_GRADIENT_PRO, "")
ROYAL_NIGHT_HTML = _build(_TOKENS_ROYAL_NIGHT, "dark-mode")
EMERALD_HTML = _build(_TOKENS_EMERALD, "")


# ═══════════════════════════════════════════════════════════════
# تصميمان جديدان مستوحيان من «الصفحة المميزة» (حزمة المستخدم):
#
#   1) aurora_store «بوابة المتجر» — شريط أخبار متحرك (marquee)،
#      أشكال زخرفية مائلة خلف بطاقة الدخول، عرض باقات أفقي
#      (showcase)، زر متجر بارز وزر سلفة/اتصال بالدعم.
#
#   2) swift_login «الدخول السريع» — فكرة «يوزر فقط»: بطاقة واحدة
#      كبيرة بحقول ضخمة متراصّة وزر دخول مركزي، مع شرائح إجراءات
#      سريعة (متجر / أسعار / دعم) وقائمة أسعار منبثقة بسيطة.
#
# الأفكار فقط أُخذت من الحزمة (شريط أخبار، باقات، أزرار خدمات،
# دخول سريع) — كل الأكواد هنا مُعاد بناؤها من الصفر ومضمّنة ذاتيًا:
# لا JS خارجي، CHAP مدعوم بنفس كتلة MD5، ومتغيّرات Hoberadius
# نفسها بما فيها STORE_ENABLED/STORE_URL والشعار data URL.
# ═══════════════════════════════════════════════════════════════

_AURORA_STORE_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Almarai','Tajawal','Segoe UI',Tahoma,sans-serif;-webkit-tap-highlight-color:transparent}
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Regular.woff2') format('woff2');font-weight:400;font-display:swap}
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Bold.woff2') format('woff2');font-weight:700;font-display:swap}
body{min-height:100vh;background:linear-gradient(160deg,{{BG_COLOR}} 0%,#ffffff 55%,{{BG_COLOR}} 100%);display:flex;flex-direction:column;align-items:center;overflow-x:hidden}
/* شريط الأخبار المتحرك — فكرة news-line من الصفحة المميزة */
.news-bar{width:100%;background:{{ACCENT_COLOR}};color:#fff;font-size:12px;padding:8px 0;overflow:hidden;white-space:nowrap;position:relative}
.news-track{display:inline-block;padding-right:100%;animation:hrMarquee 18s linear infinite}
@keyframes hrMarquee{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
.phone{width:100%;max-width:420px;padding:18px 16px 30px;position:relative}
/* بطاقة الدخول فوق أشكال زخرفية مائلة — روح screen-background-shape */
.hero{position:relative;border-radius:24px;overflow:hidden;background:linear-gradient(135deg,{{ACCENT_COLOR}},#1e293b);box-shadow:0 18px 40px rgba(15,23,42,.25);padding:26px 22px;color:#fff}
.shape{position:absolute;border-radius:30px;transform:rotate(-45deg);pointer-events:none;opacity:.16;background:#fff}
.s1{width:220px;height:220px;top:-120px;left:-60px}
.s2{width:140px;height:140px;bottom:-70px;right:-40px;opacity:.1}
.s3{width:90px;height:300px;top:-40px;right:60px;opacity:.07}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:6px;position:relative;z-index:2}
.brand img{max-height:52px;max-width:120px;object-fit:contain;background:rgba(255,255,255,.14);border-radius:12px;padding:4px}
.brand h1{font-size:20px;font-weight:800}
.welcome{font-size:12.5px;opacity:.92;margin-bottom:18px;position:relative;z-index:2;line-height:1.7}
.err{background:rgba(239,68,68,.92);border:1px solid rgba(255,255,255,.25);color:#fff;border-radius:12px;padding:10px 12px;font-size:12px;margin-bottom:14px;text-align:center;position:relative;z-index:2}
/* الحقول متراصّة تحت بعض وزر الدخول في الوسط */
form{position:relative;z-index:2;display:flex;flex-direction:column;gap:12px}
.f label{display:block;font-size:11px;font-weight:700;margin-bottom:5px;color:#e2e8f0}
.f input{width:100%;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.35);border-radius:14px;color:#fff;font-size:15px;font-weight:600;padding:12px 16px;outline:0;transition:.25s}
.f input::placeholder{color:rgba(255,255,255,.55);font-weight:400}
.f input:focus{border-color:#fff;background:rgba(255,255,255,.18);box-shadow:0 0 0 3px rgba(255,255,255,.12)}
.btn-login{margin:6px auto 0;background:#fff;color:{{ACCENT_COLOR}};border:0;border-radius:999px;font-size:14px;font-weight:800;padding:13px 44px;cursor:pointer;box-shadow:0 8px 20px rgba(0,0,0,.18);transition:transform .15s}
.btn-login:active{transform:scale(.97)}
/* زر المتجر — يظهر فقط عند STORE_ENABLED=yes */
.store-cta{display:none;margin-top:16px;align-items:center;gap:12px;background:#fff;border:1.5px solid {{ACCENT_COLOR}};border-radius:18px;padding:14px 16px;text-decoration:none;box-shadow:0 10px 24px rgba(15,23,42,.08);transition:transform .15s}
body.hr-store-on .store-cta{display:flex}
.store-cta:active{transform:scale(.98)}
.store-ico{width:44px;height:44px;border-radius:14px;background:{{ACCENT_COLOR}};color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
.store-txt{flex:1}.store-txt b{display:block;font-size:13.5px;color:#0f172a;font-weight:800}
.store-txt span{font-size:11px;color:#64748b}
.store-arrow{color:{{ACCENT_COLOR}};font-weight:800}
/* عرض الباقات الأفقي — فكرة profiles من config.js */
.sec-title{display:flex;justify-content:space-between;align-items:center;margin:22px 4px 10px;font-size:14px;font-weight:800;color:#0f172a}
.sec-title small{font-size:11px;color:{{ACCENT_COLOR}};font-weight:700}
.pkgs{display:flex;gap:10px;overflow-x:auto;padding:4px 2px 12px;scroll-snap-type:x mandatory}
.pkg{scroll-snap-align:start;flex:0 0 130px;background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:14px 12px;text-align:center;box-shadow:0 8px 18px rgba(15,23,42,.05)}
.pkg .p-amt{font-size:20px;font-weight:800;color:{{ACCENT_COLOR}}}
.pkg .p-unit{font-size:10px;color:#94a3b8}
.pkg .p-name{font-size:12px;font-weight:700;color:#0f172a;margin:6px 0 2px}
.pkg .p-meta{font-size:10px;color:#64748b;line-height:1.6}
/* بطاقة الدعم */
.support{display:flex;align-items:center;gap:12px;background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:14px 16px;margin-top:6px;box-shadow:0 8px 18px rgba(15,23,42,.05)}
.support .sp-ico{width:40px;height:40px;border-radius:50%;background:rgba(37,99,235,.1);color:{{ACCENT_COLOR}};display:flex;align-items:center;justify-content:center;font-size:18px}
.support b{font-size:12.5px;color:#0f172a;display:block}
.support span{font-size:11px;color:#64748b}
.support a{margin-inline-start:auto;background:{{ACCENT_COLOR}};color:#fff;text-decoration:none;font-size:12px;font-weight:700;padding:8px 16px;border-radius:999px;direction:ltr}
.foot{text-align:center;font-size:10.5px;color:#94a3b8;margin-top:20px}
</style>
</head>
<body>

$(if chap-id)
<form name="sendin" action="$(link-login-only)" method="post" style="display:none">
<input type="hidden" name="username"><input type="hidden" name="password">
<input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true">
</form>
<script>
%%MD5%%
window.hrChap=function(p){return hexMD5('$(chap-id)'+p+'$(chap-challenge)')};
</script>
$(endif)

<div class="news-bar"><div class="news-track">‏{{WELCOME_TEXT}} — أهلًا بكم في {{TENANT_NAME}} — للدعم: {{SUPPORT_PHONE}}‏</div></div>

<div class="phone">
  <div class="hero">
    <div class="shape s1"></div><div class="shape s2"></div><div class="shape s3"></div>
    <div class="brand">
      <img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}" onerror="this.style.display='none'">
      <h1>{{TENANT_NAME}}</h1>
    </div>
    <p class="welcome">{{WELCOME_TEXT}}</p>
    $(if error)<div class="err">$(error)</div>$(endif)
    <form name="login" action="$(link-login-only)" method="post" onsubmit="return hrSubmit()">
      <input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true">
      <div class="f"><label>اسم المستخدم / رقم البطاقة</label>
        <input type="text" name="username" placeholder="User ID" value="$(username)" required></div>
      <div class="f"><label>كلمة المرور</label>
        <input type="password" name="password" placeholder="••••••••"></div>
      <button type="submit" class="btn-login">دخول الآن ←</button>
    </form>
  </div>

  <a class="store-cta" href="{{STORE_URL}}">
    <span class="store-ico">🛒</span>
    <span class="store-txt"><b>متجر البطاقات الإلكتروني</b><span>اشحن رصيدك واشترِ بطاقتك مباشرة</span></span>
    <span class="store-arrow">‹</span>
  </a>

  <div class="sec-title">باقاتنا المتوفرة <small>أسعار مميزة</small></div>
  <div class="pkgs">
    <!-- العروض القابلة للتحرير — من OFFERS_JSON عبر render() -->
    {{OFFERS_ROW_HTML}}
  </div>

  <div class="support">
    <span class="sp-ico">☎</span>
    <span><b>هل تواجه مشكلة؟</b><span>فريق الدعم جاهز لمساعدتك</span></span>
    <a href="tel:{{SUPPORT_PHONE}}">{{SUPPORT_PHONE}}</a>
  </div>

  <p class="foot">© {{TENANT_NAME}} — جميع الحقوق محفوظة</p>
</div>

<script>
/* تفعيل زر المتجر حسب قيمة STORE_ENABLED المستبدلة من المصمّم */
if('{{STORE_ENABLED}}'==='yes'){document.body.classList.add('hr-store-on');}
/* إرسال الدخول: CHAP إن توفّر، وإلا إرسال مباشر */
function hrSubmit(){
  if(typeof window.hrChap==='function'&&document.sendin){
    document.sendin.username.value=document.login.username.value;
    document.sendin.password.value=window.hrChap(document.login.password.value);
    document.sendin.submit();return false;
  }
  return true;
}
</script>
</body>
</html>"""


_SWIFT_LOGIN_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Almarai','Tajawal','Segoe UI',Tahoma,sans-serif;-webkit-tap-highlight-color:transparent}
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Regular.woff2') format('woff2');font-weight:400;font-display:swap}
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Bold.woff2') format('woff2');font-weight:700;font-display:swap}
body{min-height:100vh;background:linear-gradient(135deg,#0f172a 0%,{{ACCENT_COLOR}} 140%);display:flex;align-items:center;justify-content:center;padding:18px;overflow-x:hidden}
.card{width:100%;max-width:380px;background:{{BG_COLOR}};border-radius:28px;padding:30px 24px 24px;box-shadow:0 25px 70px rgba(0,0,0,.4);position:relative;overflow:hidden}
/* شكلان زخرفيان مائلان خلف الرأس — روح screen-background-shape */
.deco{position:absolute;transform:rotate(-45deg);border-radius:26px;pointer-events:none}
.d1{width:170px;height:170px;background:{{ACCENT_COLOR}};opacity:.12;top:-90px;left:-50px}
.d2{width:110px;height:110px;border:3px solid {{ACCENT_COLOR}};opacity:.15;top:-30px;right:-50px}
.logo-wrap{text-align:center;margin-bottom:8px;position:relative;z-index:2}
.logo-wrap img{max-height:64px;max-width:150px;object-fit:contain}
h1{font-size:20px;font-weight:800;text-align:center;color:#0f172a;position:relative;z-index:2}
.sub{font-size:12px;color:#64748b;text-align:center;margin:4px 0 20px;line-height:1.7;position:relative;z-index:2}
.err{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;border-radius:12px;padding:10px 12px;font-size:12px;margin-bottom:14px;text-align:center;position:relative;z-index:2}
/* دخول سريع: حقول ضخمة متراصّة + زر مركزي — فكرة «يوزر فقط» */
form{display:flex;flex-direction:column;gap:12px;position:relative;z-index:2}
.qf input{width:100%;border:0;border-bottom:2.5px solid #e2e8f0;background:transparent;font-size:17px;font-weight:700;text-align:center;color:#0f172a;padding:12px 8px;outline:0;transition:border-color .25s}
.qf input:focus{border-bottom-color:{{ACCENT_COLOR}}}
.qf input::placeholder{color:#94a3b8;font-weight:400;font-size:14px}
.btn{margin:14px auto 0;display:flex;align-items:center;gap:8px;background:{{ACCENT_COLOR}};color:#fff;border:0;border-radius:999px;font-size:15px;font-weight:800;padding:14px 52px;cursor:pointer;box-shadow:0 12px 26px rgba(0,0,0,.22);transition:transform .15s}
.btn:active{transform:scale(.96)}
/* شرائح الإجراءات السريعة: متجر / أسعار / دعم — أفكار price-button
   و sell-point-button و loan-button من الصفحة المميزة */
.chips{display:flex;gap:8px;margin-top:20px;position:relative;z-index:2}
.chip{flex:1;border:1px solid #e2e8f0;background:#fff;border-radius:14px;padding:10px 6px;text-align:center;font-size:11px;font-weight:700;color:#334155;cursor:pointer;text-decoration:none;transition:.2s}
.chip:active{transform:scale(.96)}
.chip .ch-ico{display:block;font-size:18px;margin-bottom:4px}
.chip-store{display:none;border-color:{{ACCENT_COLOR}};color:{{ACCENT_COLOR}}}
body.hr-store-on .chip-store{display:block}
/* قائمة الأسعار المنبثقة البسيطة */
.prices{display:none;margin-top:14px;background:#fff;border:1px solid #e2e8f0;border-radius:16px;overflow:hidden;position:relative;z-index:2}
.prices.open{display:block;animation:hrFade .3s}
@keyframes hrFade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.pr-row{display:flex;justify-content:space-between;padding:10px 14px;font-size:12px;color:#334155;border-bottom:1px dashed #e2e8f0}
.pr-row:last-child{border-bottom:0}
.pr-row b{color:{{ACCENT_COLOR}}}
.foot{text-align:center;font-size:10.5px;color:#94a3b8;margin-top:18px;position:relative;z-index:2}
</style>
</head>
<body>

$(if chap-id)
<form name="sendin" action="$(link-login-only)" method="post" style="display:none">
<input type="hidden" name="username"><input type="hidden" name="password">
<input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true">
</form>
<script>
%%MD5%%
window.hrChap=function(p){return hexMD5('$(chap-id)'+p+'$(chap-challenge)')};
</script>
$(endif)

<div class="card">
  <div class="deco d1"></div><div class="deco d2"></div>
  <div class="logo-wrap"><img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}" onerror="this.style.display='none'"></div>
  <h1>{{TENANT_NAME}}</h1>
  <p class="sub">{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post" onsubmit="return hrSubmit()">
    <input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true">
    <div class="qf"><input type="text" name="username" placeholder="اسم المستخدم أو رقم البطاقة" value="$(username)" required></div>
    <div class="qf"><input type="password" name="password" placeholder="كلمة المرور"></div>
    <button type="submit" class="btn">دخول سريع ⚡</button>
  </form>
  <div class="chips">
    <a class="chip chip-store" href="{{STORE_URL}}"><span class="ch-ico">🛒</span>المتجر</a>
    <span class="chip" onclick="document.getElementById('hrPrices').classList.toggle('open')"><span class="ch-ico">💳</span>الأسعار</span>
    <a class="chip" href="tel:{{SUPPORT_PHONE}}"><span class="ch-ico">☎️</span>الدعم</a>
  </div>
  <div class="prices" id="hrPrices">
    <!-- قائمة الأسعار القابلة للتحرير — من OFFERS_JSON عبر render() -->
    {{OFFERS_PRICES_HTML}}
  </div>
  <p class="foot">© {{TENANT_NAME}} — للدعم: {{SUPPORT_PHONE}}</p>
</div>

<script>
/* تفعيل شريحة المتجر حسب STORE_ENABLED */
if('{{STORE_ENABLED}}'==='yes'){document.body.classList.add('hr-store-on');}
function hrSubmit(){
  if(typeof window.hrChap==='function'&&document.sendin){
    document.sendin.username.value=document.login.username.value;
    document.sendin.password.value=window.hrChap(document.login.password.value);
    document.sendin.submit();return false;
  }
  return true;
}
</script>
</body>
</html>"""


# ─── «توهّج الألياف» — مستوحى من حزمة فايبر نت (هوية نظيفة) ──────
#
# إلهام لا نسخ: هيكل «قشرة تطبيق جوال» بعرض ثابت، هيدر داكن منحنٍ
# بخلفية جسيمات حيّة (canvas خفيف) وهالات ضوئية وشريط أخبار متحرك،
# تعلوه بطاقة بيضاء طافية بزوايا علوية مدوّرة. كله مقاد بمتغيّرات
# قوالبنا ({{ACCENT_COLOR}}/{{BG_COLOR}}…) وبخط المراعي، وبسقالة
# CHAP نفسها (sendin + hrChap + hrSubmit) المعتمدة في العائلة.
#
# تحسينات مقصودة على فكرة فايبر نت الأصلية:
#   - «آخر البطاقات» تُخزَّن باسم المستخدم فقط في localStorage —
#     لا تُحفظ كلمة المرور إطلاقًا (الأصل كان يخزّنها بنص صريح).
#   - زر المتجر/التجربة عبر مفاتيح التفعيل القياسية (لا أزرار صلبة).
#   - بلا أي غطاء «جاري التحميل» — الصفحة ونموذجها يظهران فورًا،
#     فتمرّ عبر strip_splash() بلا أي أثر يُحذف.
_FIBER_GLOW_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Almarai','Tajawal','Segoe UI',Tahoma,sans-serif;-webkit-tap-highlight-color:transparent}
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Regular.woff2') format('woff2');font-weight:400;font-display:swap}
@font-face{font-family:'Almarai';src:url('fonts/Almarai-Bold.woff2') format('woff2');font-weight:700;font-display:swap}
:root{--accent:{{ACCENT_COLOR}};--bg:{{BG_COLOR}};--ink:#0f172a;--muted:#64748b;--line:#e8eef5;--head1:#0f172a;--head2:#102a36}
body{min-height:100vh;background:var(--bg);display:flex;justify-content:center;color:var(--ink)}
.app{width:100%;max-width:430px;min-height:100vh;background:#fff;display:flex;flex-direction:column;box-shadow:0 25px 50px -12px rgba(0,0,0,.25);position:relative;overflow:hidden}
/* الهيدر الداكن المنحني + خلفية الجسيمات والهالات */
.head{position:relative;background:linear-gradient(135deg,var(--head1) 0%,var(--head2) 55%,var(--accent) 165%);color:#fff;padding:14px 20px 46px;overflow:hidden}
.head canvas{position:absolute;inset:0;width:100%;height:100%;opacity:.45;pointer-events:none}
.glow{position:absolute;border-radius:50%;filter:blur(60px);pointer-events:none;opacity:.5}
.glow.a{width:160px;height:160px;background:var(--accent);top:-60px;left:-40px}
.glow.b{width:130px;height:130px;background:#3b82f6;bottom:-50px;right:-30px;opacity:.35}
/* شريط الأخبار الزجاجي المتحرك */
.ticker{position:relative;z-index:2;background:rgba(255,255,255,.08);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.12);border-radius:999px;padding:6px 12px;overflow:hidden;white-space:nowrap;margin-bottom:14px}
.ticker span{display:inline-block;font-size:11.5px;animation:fgMarquee 20s linear infinite}
@keyframes fgMarquee{from{transform:translateX(100%)}to{transform:translateX(-100%)}}
.topbar{position:relative;z-index:2;display:flex;justify-content:space-between;align-items:center;font-size:11px;margin-bottom:16px}
.oschip{display:flex;align-items:center;gap:6px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.14);padding:4px 10px;border-radius:999px;font-weight:600}
.clock{font-family:monospace;letter-spacing:.5px;direction:ltr;opacity:.92}
.brand{position:relative;z-index:2;display:flex;align-items:center;gap:12px}
.brand img{max-height:48px;max-width:120px;object-fit:contain;background:rgba(255,255,255,.12);border-radius:12px;padding:5px}
.brand h1{font-size:20px;font-weight:800}
.secure{display:flex;align-items:center;gap:6px;font-size:11px;opacity:.9;margin-top:6px}
.dot{width:7px;height:7px;border-radius:50%;background:#22d3ee;box-shadow:0 0 0 0 rgba(34,211,238,.6);animation:fgPulse 1.8s infinite}
@keyframes fgPulse{0%{box-shadow:0 0 0 0 rgba(34,211,238,.6)}70%{box-shadow:0 0 0 8px rgba(34,211,238,0)}100%{box-shadow:0 0 0 0 rgba(34,211,238,0)}}
/* البطاقة البيضاء الطافية فوق الهيدر */
.body{flex:1;background:#fff;margin-top:-26px;border-top-left-radius:26px;border-top-right-radius:26px;position:relative;z-index:3;padding:24px 20px 30px}
.title{font-size:16px;font-weight:800;margin-bottom:4px}
.welcome{font-size:12.5px;color:var(--muted);line-height:1.7;margin-bottom:16px}
.err{background:rgba(239,68,68,.1);color:#b91c1c;border:1px solid rgba(239,68,68,.25);border-radius:12px;padding:11px 13px;font-size:12.5px;margin-bottom:14px;display:flex;align-items:center;gap:8px;animation:fgDown .35s}
@keyframes fgDown{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
.ig{position:relative;margin-bottom:12px}
.ig input{width:100%;background:#f8fafc;border:1.5px solid var(--line);border-radius:14px;padding:13px 44px 13px 14px;font-size:16px;font-weight:600;color:var(--ink);outline:0;transition:.2s}
.ig input::placeholder{color:#94a3b8;font-weight:400}
.ig input:focus{border-color:var(--accent);background:#fff;box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.ig .ico{position:absolute;top:50%;right:14px;transform:translateY(-50%);font-size:15px;opacity:.5;transition:.2s}
.ig:focus-within .ico{opacity:1}
.btn-login{width:100%;background:linear-gradient(to left,var(--accent),#3b82f6);color:#fff;border:0;border-radius:14px;font-size:15px;font-weight:800;padding:14px;cursor:pointer;transition:transform .15s}
.btn-login:active{transform:scale(.98)}
/* آخر البطاقات (اسم المستخدم فقط — بلا كلمة مرور) */
.saved{margin-top:16px}
.saved h3{font-size:12px;font-weight:700;color:var(--muted);display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.saved h3 a{font-size:11px;color:#ef4444;text-decoration:none;cursor:pointer}
.scard{display:flex;align-items:center;gap:10px;background:#f8fafc;border:1px solid var(--line);border-radius:14px;padding:9px 12px;margin-bottom:8px;cursor:pointer;transition:.2s}
.scard:active{transform:scale(.99)}
.savatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,var(--accent),#3b82f6);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;flex-shrink:0}
.sinfo{flex:1;min-width:0}.sinfo b{display:block;font-size:13px;font-weight:700;direction:ltr;text-align:right;font-family:'Courier New',monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sinfo span{font-size:10.5px;color:var(--muted)}
.sdel{border:0;background:#eef2f7;color:#64748b;width:26px;height:26px;border-radius:50%;cursor:pointer;font-size:15px;font-weight:800;line-height:1;flex-shrink:0}
.sdel:hover{background:#fee2e2;color:#ef4444}
.suse{color:var(--accent);font-weight:800;font-size:18px}
/* زر المتجر — يظهر فقط عند STORE_ENABLED=yes */
.store-cta{display:none;align-items:center;gap:12px;background:#fff;border:1.5px solid var(--accent);border-radius:16px;padding:13px 14px;margin-top:16px;text-decoration:none;box-shadow:0 10px 24px rgba(15,23,42,.06)}
body.hr-store-on .store-cta{display:flex}
.store-ico{width:42px;height:42px;border-radius:13px;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-size:19px;flex-shrink:0}
.store-cta b{display:block;font-size:13px;color:var(--ink);font-weight:800}
.store-cta small{font-size:10.5px;color:var(--muted)}
.store-cta .arr{margin-inline-start:auto;color:var(--accent);font-weight:800}
/* زر التجربة المجانية المحقون آليًا من render() */
.hr-addon-trial{display:block;text-align:center;margin-top:12px;background:#f1f5f9;color:var(--accent);border:1.5px dashed var(--accent);border-radius:14px;padding:12px;font-size:13px;font-weight:700;text-decoration:none}
/* عرض الباقات الأفقي */
.sec{margin-top:20px}
.sec-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-size:14px;font-weight:800}
.sec-h small{font-size:11px;color:var(--accent);font-weight:700}
.pkgs{display:flex;gap:10px;overflow-x:auto;padding:2px 0 8px;scroll-snap-type:x mandatory}
.pkg{scroll-snap-align:start;flex:0 0 128px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:13px 11px;text-align:center;box-shadow:0 8px 18px rgba(15,23,42,.05)}
.pkg .p-amt{font-size:19px;font-weight:800;color:var(--accent)}
.pkg .p-unit{font-size:10px;color:#94a3b8}
.pkg .p-name{font-size:12px;font-weight:700;margin:6px 0 2px}
.pkg .p-meta{font-size:10px;color:var(--muted);line-height:1.6}
/* بطاقة الدعم: نسخ الرقم + اتصال مباشر */
.support{display:flex;align-items:center;gap:12px;background:#f8fafc;border:1px solid var(--line);border-radius:16px;padding:12px 14px;margin-top:16px}
.support .sp-ico{width:40px;height:40px;border-radius:50%;background:rgba(37,99,235,.1);color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:18px}
.support b{font-size:12.5px;display:block}.support small{font-size:11px;color:var(--muted)}
.sp-acts{margin-inline-start:auto;display:flex;gap:6px}
.sp-acts button,.sp-acts a{border:0;cursor:pointer;font-size:12px;font-weight:700;border-radius:999px;padding:8px 13px;text-decoration:none;direction:ltr}
.sp-copy{background:#f1f5f9;color:var(--ink)}
.sp-call{background:var(--accent);color:#fff}
.foot{text-align:center;font-size:10.5px;color:#94a3b8;margin-top:20px}
/* توست الإشعارات العائم */
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--ink);color:#fff;font-size:12.5px;font-weight:700;padding:11px 18px;border-radius:999px;box-shadow:0 12px 30px rgba(0,0,0,.25);opacity:0;pointer-events:none;transition:.35s cubic-bezier(.34,1.56,.64,1);z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>

$(if chap-id)
<form name="sendin" action="$(link-login-only)" method="post" style="display:none">
<input type="hidden" name="username"><input type="hidden" name="password">
<input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true">
</form>
<script>
%%MD5%%
window.hrChap=function(p){return hexMD5('$(chap-id)'+p+'$(chap-challenge)')};
</script>
$(endif)

<div class="app">
  <div class="head">
    <canvas id="fgParticles"></canvas>
    <div class="glow a"></div><div class="glow b"></div>
    <div class="ticker"><span>‏{{WELCOME_TEXT}} — أهلًا بكم في {{TENANT_NAME}} — للدعم الفني: {{SUPPORT_PHONE}}‏</span></div>
    <div class="topbar">
      <div class="oschip"><span id="fgOsIco">💻</span><span id="fgOs">جهازك</span></div>
      <div class="clock" id="fgClock">--:--</div>
    </div>
    <div class="brand">
      <img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}" onerror="this.style.display='none'">
      <div><h1>{{TENANT_NAME}}</h1>
        <div class="secure"><span class="dot"></span> اتصال آمن ومشفّر</div>
      </div>
    </div>
  </div>

  <div class="body">
    <div class="title">تسجيل الدخول للشبكة</div>
    <div class="welcome">{{WELCOME_TEXT}}</div>
    $(if error)<div class="err"><span>⚠️</span><span>$(error)</span></div>$(endif)

    <form name="login" action="$(link-login-only)" method="post" onsubmit="return hrSubmit()">
      <input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true">
      <div class="ig"><span class="ico">🪪</span>
        <input type="text" name="username" placeholder="رقم البطاقة / اسم المستخدم" value="$(username)" autocomplete="off" required></div>
      <div class="ig"><span class="ico">🔑</span>
        <input type="password" name="password" placeholder="كلمة المرور"></div>
      <button type="submit" class="btn-login">الاتصال بالشبكة ←</button>
    </form>

    <div class="saved hr-saved-on" id="fgSaved" style="display:none">
      <h3>الجلسات الأخيرة <a onclick="fgClearSaved()">مسح الكل</a></h3>
      <div id="fgSavedList"></div>
    </div>

    <a class="store-cta" href="{{STORE_URL}}">
      <span class="store-ico">🛒</span>
      <span><b>متجر البطاقات الإلكتروني</b><small>اشحن رصيدك واشترِ بطاقتك مباشرة</small></span>
      <span class="arr">‹</span>
    </a>

    <div class="sec">
      <div class="sec-h">باقاتنا المتوفرة <small>أسعار مميزة</small></div>
      <div class="pkgs">{{OFFERS_ROW_HTML}}</div>
    </div>

    <div class="support">
      <span class="sp-ico">☎</span>
      <span><b>الدعم الفني</b><small>فريقنا جاهز لمساعدتك</small></span>
      <span class="sp-acts">
        <button type="button" class="sp-copy" onclick="fgCopyPhone()">نسخ</button>
        <a class="sp-call" href="tel:{{SUPPORT_PHONE}}">اتصال</a>
      </span>
    </div>

    <p class="foot">© {{TENANT_NAME}} — جميع الحقوق محفوظة</p>
  </div>
</div>

<div class="toast" id="fgToast"></div>

<script>
/* تفعيل زر المتجر حسب STORE_ENABLED المستبدلة من المصمّم */
if('{{STORE_ENABLED}}'==='yes'){document.body.classList.add('hr-store-on');}

/* إرسال الدخول: CHAP إن توفّر، وإلا إرسال مباشر — نحفظ اسم المستخدم
   فقط (دون كلمة المرور) إن كان «الحفظ» مفعّلًا. */
function hrSubmit(){
  /* حفظ الجلسة (اسم المستخدم + كلمة المرور) إن كانت الخدمة مفعّلة */
  try{ if('{{SAVED_SESSIONS_ENABLED}}'==='yes') fgRemember(document.login.username.value, document.login.password.value); }catch(e){}
  if(typeof window.hrChap==='function'&&document.sendin){
    document.sendin.username.value=document.login.username.value;
    document.sendin.password.value=window.hrChap(document.login.password.value);
    document.sendin.submit();return false;
  }
  return true;
}

/* توست عائم موحّد */
var fgT;
function fgToast(m){var t=document.getElementById('fgToast');t.textContent=m;t.classList.add('show');clearTimeout(fgT);fgT=setTimeout(function(){t.classList.remove('show')},2200);}

/* الساعة الحيّة بصيغة عربية */
function fgClock(){var d=new Date(),h=d.getHours(),m=d.getMinutes(),ap=h<12?'ص':'م';h=h%12||12;document.getElementById('fgClock').textContent=h+':'+(m<10?'0'+m:m)+' '+ap;}
setInterval(fgClock,1000);fgClock();

/* كشف نظام التشغيل (لمسة شخصية في الهيدر) */
(function(){var u=navigator.userAgent,n='جهازك',i='💻';
  if(/Android/i.test(u)){n='Android';i='🤖';}
  else if(/iPhone|iPad|iPod/i.test(u)){n='iPhone';i='🍎';}
  else if(/Windows/i.test(u)){n='Windows';i='🪟';}
  else if(/Mac/i.test(u)){n='Mac';i='🍏';}
  document.getElementById('fgOs').textContent=n;document.getElementById('fgOsIco').textContent=i;})();

/* خلفية جسيمات خفيفة في الهيدر (لا صورة ثابتة) */
(function(){var c=document.getElementById('fgParticles');if(!c)return;var x=c.getContext('2d'),P=[],N=34,w,h;
  function size(){w=c.width=c.offsetWidth;h=c.height=c.offsetHeight;}
  function mk(){return{x:Math.random()*w,y:Math.random()*h,r:1+Math.random()*1.8,vx:(Math.random()-.5)*.4,vy:(Math.random()-.5)*.4};}
  function init(){size();P=[];for(var i=0;i<N;i++)P.push(mk());}
  function loop(){x.clearRect(0,0,w,h);x.fillStyle='rgba(34,211,238,.55)';for(var i=0;i<P.length;i++){var p=P[i];p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>w)p.vx*=-1;if(p.y<0||p.y>h)p.vy*=-1;x.beginPath();x.arc(p.x,p.y,p.r,0,6.283);x.fill();}requestAnimationFrame(loop);}
  init();loop();window.addEventListener('resize',init);})();

/* «الجلسات الأخيرة»: آخر 5 بطاقات (اسم المستخدم + كلمة المرور —
   بقرار صريح من المالك، البطاقات منخفضة القيمة). البناء عبر
   textContent فلا حقن HTML. نقرة البطاقة تعبّئ الحقلين وتُرسل
   النموذج فورًا (يشغّل hrSubmit فيعمل CHAP). */
function fgGet(){try{return JSON.parse(localStorage.getItem('fg_cards'))||[];}catch(e){return[];}}
function fgRemember(u,p){u=(u||'').trim();if(!u)return;var a=fgGet().filter(function(x){return x.u!==u;});a.unshift({u:u,p:p||'',t:Date.now()});if(a.length>5)a=a.slice(0,5);try{localStorage.setItem('fg_cards',JSON.stringify(a));}catch(e){}fgRender();}
function fgRel(t){var s=Math.floor((Date.now()-t)/1000);if(s<60)return 'الآن';var m=Math.floor(s/60);if(m<60)return 'قبل '+m+' دقيقة';var h=Math.floor(m/60);if(h<24)return 'قبل '+h+' ساعة';var d=Math.floor(h/24);return d===1?'أمس':'قبل '+d+' يوم';}
function fgDel(u){try{localStorage.setItem('fg_cards',JSON.stringify(fgGet().filter(function(x){return x.u!==u;})));}catch(e){}fgRender();}
function fgRender(){var box=document.getElementById('fgSaved'),list=document.getElementById('fgSavedList');if('{{SAVED_SESSIONS_ENABLED}}'!=='yes'){box.style.display='none';return;}var a=fgGet();if(!a.length){box.style.display='none';return;}box.style.display='block';list.innerHTML='';a.forEach(function(it){var el=document.createElement('div');el.className='scard';var av=document.createElement('div');av.className='savatar';av.textContent=(it.u[0]||'?').toUpperCase();var info=document.createElement('div');info.className='sinfo';var b=document.createElement('b');b.textContent=it.u;var sp=document.createElement('span');sp.textContent='آخر استخدام '+fgRel(it.t);info.appendChild(b);info.appendChild(sp);var del=document.createElement('button');del.type='button';del.className='sdel';del.textContent='×';del.title='حذف';del.onclick=function(ev){ev.stopPropagation();fgDel(it.u);};var go=document.createElement('span');go.className='suse';go.textContent='‹';el.appendChild(av);el.appendChild(info);el.appendChild(del);el.appendChild(go);el.onclick=function(){fgUse(it);};list.appendChild(el);});}
function fgUse(it){var u=document.querySelector('input[name=username]'),p=document.querySelector('input[name=password]');if(u)u.value=it.u;if(p)p.value=it.p||'';fgToast('جارٍ الدخول بالبطاقة المحفوظة...');var b=document.querySelector('form[name=login] .btn-login');if(b){b.click();}else if(document.login.requestSubmit){document.login.requestSubmit();}else{document.login.submit();}}
function fgClearSaved(){try{localStorage.removeItem('fg_cards');}catch(e){}fgRender();fgToast('تم مسح السجل');}
fgRender();

/* نسخ رقم الدعم مع بديل execCommand للسياق غير الآمن (هوت سبوت http) */
function fgCopyPhone(){var n='{{SUPPORT_PHONE}}';if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(n).then(function(){fgToast('تم نسخ رقم الدعم');},fgCopyFallback.bind(null,n));}else{fgCopyFallback(n);}}
function fgCopyFallback(n){try{var t=document.createElement('textarea');t.value=n;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();document.execCommand('copy');document.body.removeChild(t);fgToast('تم نسخ رقم الدعم');}catch(e){fgToast('الرقم: '+n);}}
</script>
</body>
</html>"""


# حقن كتلة MD5 في القوالب الجديدة (نفس مصدر CHAP في العائلة).
AURORA_STORE_HTML = _AURORA_STORE_HTML.replace("%%MD5%%", _MD5_JS)
SWIFT_LOGIN_HTML = _SWIFT_LOGIN_HTML.replace("%%MD5%%", _MD5_JS)
FIBER_GLOW_HTML = _FIBER_GLOW_HTML.replace("%%MD5%%", _MD5_JS)


__all__ = [
    "GRADIENT_PRO_HTML",
    "ROYAL_NIGHT_HTML",
    "EMERALD_HTML",
    "AURORA_STORE_HTML",
    "SWIFT_LOGIN_HTML",
    "FIBER_GLOW_HTML",
]
# نهاية الملف.
