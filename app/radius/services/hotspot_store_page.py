"""hotspot_store_page — متجر المايكروتيك (store.html) المرفوع إلى الراوتر.

الفكرة: المايكروتيك لا يستضيف صفحات ديناميكية — فنرفع متجرًا كاملًا
في ملف HTML واحد إلى ملفات الهوت سبوت (بجانب login.html)، يعمل من
الراوتر نفسه ويتخاطب مع سيرفر الراديوس عبر fetch فقط على نقاط
/api/v1/store/* (انظر app/api/v1/store.py):

  - تسجيل دخول خاص (جوال + كلمة مرور بوابة البطاقات) → توكن موقّع
    يُحفظ في sessionStorage.
  - أربعة تبويبات بشريط سفلي (نفس ما تعرضه بوابة الزبون الويب
    portal_card.html لكن مبسّطًا للراوتر):
      رصيدي   — المحفظة + شحن بطاقة (كود + رقم سري).
      المعرض  — باقات السوق مع شراء فوري.
      بطاقاتي — بطاقاته المشتراة بحالتها من الراديوس (فعالة الآن /
                غير مستخدمة بعد / مستهلكة / منتهية / ملغاة) مع
                بيانات الدخول ونسخها وزر «دخول بهذه البطاقة».
      السجل   — سجل عمليات الشراء مصفّحًا.

  زر «دخول بهذه البطاقة»: نموذج مخفي يرسل اليوزر/الباس إلى
  $(link-login-only) مع dst=$(link-orig) — هذه الـ placeholders
  يملؤها الراوتر لأن store.html يُقدَّم من خادم الهوت سبوت نفسه
  (نفس آلية زر التجربة في hotspot_templates). إن لم تُستبدل (الصفحة
  فُتحت خارج خادم الهوت سبوت) نسقط إلى login.html?u=..&p=.. حيث
  يلتقطها سكربت الدخول التلقائي R4 المحقون في كل صفحات الدخول
  المنشورة (وهو الآمن مع CHAP لأنه يمر عبر doLogin).

الصفحة مكتفية ذاتيًا بالكامل (لا JS خارجي؛ خط المراعي اختياري من
مسار نسبي fonts/Almarai-*.woff2 بجانبها على الراوتر مع سقوط آمن
لخطوط النظام) — فقط عنوان سيرفر الراديوس يُحقن مكان {{API_BASE}}
عند النشر، ويجب أن يكون
العنوان ضمن walled-garden الهوت سبوت حتى تصل إليه الأجهزة قبل
تسجيل دخول الإنترنت.

النشر يعيد استخدام نفس آلية deploy_login (/file/print ثم
/file/set أو /file/add) — انظر deploy_store أدناه.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# اسم الملف على الراوتر — بجانب login.html في مجلد الهوت سبوت،
# فيكفي رابط نسبي "store.html" من صفحة الدخول للوصول إليه.
STORE_FILE_NAME = "store.html"
DEFAULT_STORE_PATH = "hotspot/" + STORE_FILE_NAME

# المتغيّرات التي تُستبدل في الصفحة عند النشر. التحقق هنا مستقل
# عن hotspot_templates.validate_vars لأن المتجر له متغيّر إضافي
# (API_BASE) لا معنى له في صفحات الدخول.
_API_BASE_RE = re.compile(r"^https?://[A-Za-z0-9\.\-_:]+$")
_NAME_RE = re.compile(r"^[\w\s\-\.؀-ۿ]{1,40}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOGO_RE = re.compile(
    r"^(https?://[A-Za-z0-9\.\-_/:%?=&]+"
    r"|/[A-Za-z0-9\.\-_/]*"
    r"|data:image/(png|jpe?g|gif|webp|svg\+xml);base64,"
    r"[A-Za-z0-9+/=]+)$")


class StorePageError(ValueError):
    """خطأ تحقق آمن في متغيّرات صفحة المتجر."""


# ─── حارس «عنوان محلي» — عنوان لا تصل إليه أجهزة الزبائن ─────────
# 127.x.x.x / localhost / ::1 / 0.0.0.0 أو عنوان فارغ: حقنه في
# store.html يعني أن fetch من هاتف الزبون سيضرب الهاتف نفسه —
# الصفحة المنشورة عديمة الفائدة. يُفحص في النشر وحزمة ZIP قبل
# البناء وتُعرض رسالة عربية واضحة بدل صفحة لا تعمل بصمت.
_LOOPBACK_RE = re.compile(
    r"^(https?://)?(127(\.\d{1,3}){3}|localhost|0\.0\.0\.0|\[?::1\]?)"
    r"(:\d+)?/?$", re.IGNORECASE)

API_BASE_LOOPBACK_MSG = (
    "ضبط عنوان الراديوس بالإعدادات أولًا — عنوان محلي لن يعمل من "
    "أجهزة الزبائن (network.radius_server_ip في الإعدادات).")


def api_base_unusable(api_base: str) -> bool:
    """هل عنوان الـ API فارغ أو loopback (لن يعمل من أجهزة الزبائن)؟"""
    base = str(api_base or "").strip()
    if not base:
        return True
    return bool(_LOOPBACK_RE.match(base))


# ─── walled-garden — السماح للأجهزة غير المسجلة بالوصول للراديوس ──
# قبل تسجيل دخول الهوت سبوت يحجب الراوتر كل شيء؛ بدون قاعدة
# walled-garden ip باتجاه عنوان الراديوس يفشل fetch المتجر دائمًا
# («تعذر الوصول لسيرفر المتجر»). نضيف القواعد تلقائيًا عند النشر
# (idempotent — فحص قبل الإضافة بالتعليق المميز) ونولّد نفس الأوامر
# كنص جاهز للنسخ لمن يرفع الحزمة يدويًا (ZIP).

WALLED_GARDEN_COMMENT = "HobeRadius-Store"


def _api_base_host_port(api_base: str) -> tuple[str, str]:
    """يفكك http://host[:port] إلى (host, port) — port فارغ يعني 80."""
    base = str(api_base or "").strip().rstrip("/")
    base = re.sub(r"^https?://", "", base)
    if ":" in base:
        host, _, port = base.rpartition(":")
        if port.isdigit():
            return host, port
    return base, ""


def walled_garden_ports(api_base: str) -> list[str]:
    """المنافذ المطلوب فتحها: منفذ الـ API + 80/443 (خطوط/إحالات)."""
    _, port = _api_base_host_port(api_base)
    ports = []
    if port and port not in ("80", "443"):
        ports.append(port)
    # 80 و443 دائمًا — صفحات المتجر/الدخول قد تجلب موارد من السيرفر
    # (شعار برابط مطلق مثلًا) وإحالة /portal/card تمر عبرهما.
    ports.extend(["80", "443"])
    return ports


def walled_garden_command(api_base: str) -> str:
    """أمر RouterOS جاهز للنسخ — نفس الصيغة في v6 و v7.

    يُعرض في واجهة المصمّم ويُضمَّن في README الحزمة عندما لا
    تُضاف القواعد آليًا (رفع يدوي أو فشل API)."""
    host, _ = _api_base_host_port(api_base)
    if not host:
        return ""
    ports = ",".join(walled_garden_ports(api_base))
    return ("/ip hotspot walled-garden ip add action=accept "
            f"dst-address={host} dst-port={ports} protocol=tcp "
            f'comment="{WALLED_GARDEN_COMMENT}"')


@dataclass
class WalledGardenResult:
    ok: bool
    added: int = 0        # عدد القواعد المضافة فعلًا
    existing: int = 0     # قواعد موجودة مسبقًا (لم تُمس)
    error: str = ""
    command: str = ""     # أمر النسخ اليدوي (يُعرض عند الفشل)


def ensure_walled_garden(client: object, *, api_base: str) -> WalledGardenResult:
    """يضيف قواعد walled-garden ip للسماح بالوصول لسيرفر الراديوس
    قبل تسجيل الدخول — idempotent: يفحص القواعد الموجودة (بالتعليق
    المميز أو نفس العنوان/المنفذ) قبل الإضافة.

    الصيغة واحدة في RouterOS v6 و v7:
      /ip/hotspot/walled-garden/ip/add dst-address=.. dst-port=..
    عند أي فشل (صلاحيات API ناقصة مثلًا) يعيد ok=False مع أمر
    النسخ اليدوي ليعرضه المصمّم بدل إفشال النشر كله."""
    host, _ = _api_base_host_port(api_base)
    cmd = walled_garden_command(api_base)
    if not host:
        return WalledGardenResult(ok=False, error="عنوان API فارغ.",
                                  command=cmd)
    try:
        rows = client.run("/ip/hotspot/walled-garden/ip/print") or []
    except Exception as e:  # noqa: BLE001
        return WalledGardenResult(
            ok=False, command=cmd,
            error="تعذر قراءة قواعد walled-garden: " + str(e))
    existing_ports: set[str] = set()
    for row in rows:
        dst = str(row.get("dst-address") or "").split("/", 1)[0]
        comment = str(row.get("comment") or "")
        if dst == host or comment == WALLED_GARDEN_COMMENT:
            for p in str(row.get("dst-port") or "").split(","):
                p = p.strip()
                if p:
                    existing_ports.add(p)
            if not str(row.get("dst-port") or "").strip() and dst == host:
                # قاعدة بلا منفذ = كل المنافذ مفتوحة لهذا العنوان.
                return WalledGardenResult(ok=True, existing=len(rows),
                                          command=cmd)
    added = 0
    existing = 0
    for port in walled_garden_ports(api_base):
        if port in existing_ports:
            existing += 1
            continue
        try:
            client.run("/ip/hotspot/walled-garden/ip/add", attrs={
                "action": "accept",
                "dst-address": host,
                "dst-port": port,
                "protocol": "tcp",
                "comment": WALLED_GARDEN_COMMENT,
            })
            added += 1
        except Exception as e:  # noqa: BLE001
            return WalledGardenResult(
                ok=False, added=added, existing=existing, command=cmd,
                error="إضافة قاعدة walled-garden فشلت: " + str(e))
    return WalledGardenResult(ok=True, added=added, existing=existing,
                              command=cmd)


def normalize_api_base(api_base: str) -> str:
    """يطبّع عنوان الـ API لما يُحقن في store.html.

    • IP/مضيف مجرد بلا مخطط → http://IP. لا نضيف منفذًا: في الإنتاج
      يُقدَّم سيرفر الراديوس خلف nginx على المنفذ 80 (انظر
      deploy/nginx.conf؛ gunicorn نفسه على 127.0.0.1:8000 محليًا
      فقط، و5050 منفذ التطوير على loopback) — فالعنوان العام من
      أجهزة الزبائن هو المنفذ 80 الافتراضي. من أراد منفذًا صريحًا
      يكتبه في network.radius_server_ip (http://IP:منفذ).
    • شرطة نهائية تُزال (تُلصق به /api/v1/store/* مباشرة).
    يعيد "" للعنوان الفارغ.
    """
    base = str(api_base or "").strip().rstrip("/")
    if base and not re.match(r"^https?://", base):
        base = "http://" + base
    return base


def render_store_page(
    *,
    api_base: str,
    tenant_name: str = "Hoberadius WiFi",
    accent_color: str = "#4F46E5",
    logo_url: str = "",
    store_key: str = "",
    support_whatsapp: str = "",
    strict: bool = True,
) -> str:
    """يبني store.html النهائي بحقن المتغيّرات المفحوصة.

    api_base: عنوان سيرفر الراديوس كاملًا (http://10.0.0.5 أو
    http://10.0.0.5:5000) بلا شرطة نهائية — يُلصق قبل /api/v1/store/*.

    store_key: مفتاح تطبيق المتجر (services/store_key) — يُحقن مكان
    {{STORE_KEY}} وترسله الصفحة في ترويسة X-Store-Key مع كل نداء،
    فيرفض الـ API أي طلب لا يحمله. فارغ = صفحة بلا مفتاح (تثبيت قديم
    قبل توليد مفتاح؛ الـ API لا يفرض حينها — انظر store_key.verify).

    strict=True (افتراضي، مسار النشر/الحزمة): عنوان فارغ/غير صالح
    يرفع StorePageError فيرفض النشر صفحةً لا تعمل.
    strict=False: نبني الصفحة على أي حال بعنوان فارغ — حارس JS
    داخل الصفحة (apiUnusable/guardApi) يُظهر شريطًا عربيًا واضحًا
    «اضبط network.radius_server_ip» بدل محاولة fetch تفشل بصمت.
    """
    base = normalize_api_base(api_base)
    if not base or not _API_BASE_RE.match(base):
        if strict:
            raise StorePageError(
                "عنوان سيرفر الراديوس غير صالح — اضبط network.radius_server_ip في الإعدادات.")
        # غير صارم: نحقن عنوانًا فارغًا فيلتقطه حارس JS ويعرض التحذير.
        base = ""
    name = str(tenant_name or "").strip() or "Hoberadius WiFi"
    if not _NAME_RE.match(name):
        raise StorePageError("اسم المزوّد غير صالح لصفحة المتجر.")
    color = str(accent_color or "").strip() or "#4F46E5"
    if not _COLOR_RE.match(color):
        color = "#4F46E5"
    logo = str(logo_url or "").strip()
    if logo and not _LOGO_RE.match(logo):
        logo = ""
    # المفتاح يُعقَّم إلى [A-Za-z0-9_-] فقط — آمن للحقن في سلسلة JS
    # بلا أي هروب (token_urlsafe أصلًا ضمن هذا النطاق).
    key = re.sub(r"[^A-Za-z0-9_\-]", "", str(store_key or ""))
    # رقم واتساب الدعم: أرقام فقط (wa.me لا يقبل + أو فراغات) — آمن
    # للحقن في سلسلة JS. فارغ = يخفي زر التحويل للواتساب.
    whatsapp = re.sub(r"\D", "", str(support_whatsapp or ""))
    out = STORE_PAGE_HTML
    out = out.replace("{{API_BASE}}", base)
    out = out.replace("{{TENANT_NAME}}", name)
    out = out.replace("{{ACCENT_COLOR}}", color)
    out = out.replace("{{TENANT_LOGO_URL}}", logo)
    out = out.replace("{{STORE_KEY}}", key)
    out = out.replace("{{SUPPORT_WHATSAPP}}", whatsapp)
    # حذف غطاء «جاري التحميل» — نفس strip_splash لصفحات الدخول.
    # المتجر لا يعرض غطاء تحميل كامل الشاشة حاليًا، لكن نمرّره
    # اتساقًا ووقايةً لأي تصميم متجر مستقبلي يضيف غطاءً (الدالة لا
    # تفعل شيئًا إن لم تجد غطاءً). استيراد متأخر يتفادى دورة الاستيراد.
    try:
        from .hotspot_templates import strip_splash
        out = strip_splash(out)
    except Exception:  # noqa: BLE001 — خارج سياق الحزمة (اختبارات وحدة)
        pass
    return out


@dataclass
class StoreDeployResult:
    ok: bool
    path: str
    bytes: int
    error: str = ""


def deploy_store(
    client: object,
    *,
    api_base: str,
    tenant_name: str = "Hoberadius WiFi",
    accent_color: str = "#4F46E5",
    logo_url: str = "",
    store_key: str = "",
    support_whatsapp: str = "",
    target_path: str = DEFAULT_STORE_PATH,
) -> StoreDeployResult:
    """يرفع store.html إلى الراوتر — نفس خطوات deploy_login حرفيًا:

      1. /file/print للتأكد هل الملف موجود.
      2a. موجود → /file/set [.id=X] contents=<html>.
      2b. غير موجود → /file/add name=<path> contents=<html>.

    store_key: مفتاح تطبيق المتجر يُحقن في الصفحة (انظر render_store_page)
    — يمرّره مسار النشر عبر store_key.get_or_create_store_key.
    """
    # حارس عنوان محلي: 127.0.0.1/localhost أو فارغ → الصفحة المنشورة
    # لن تصل أبدًا للـ API من أجهزة الزبائن — نرفض برسالة واضحة.
    if api_base_unusable(api_base):
        return StoreDeployResult(ok=False, path=target_path, bytes=0,
                                 error=API_BASE_LOOPBACK_MSG)
    try:
        html = render_store_page(
            api_base=api_base,
            tenant_name=tenant_name,
            accent_color=accent_color,
            logo_url=logo_url,
            store_key=store_key,
            support_whatsapp=support_whatsapp,
        )
    except StorePageError as e:
        return StoreDeployResult(ok=False, path=target_path, bytes=0,
                                 error=str(e))
    try:
        existing = client.run("/file/print",
                              attrs={"where": "name=" + target_path})
    except Exception as e:  # noqa: BLE001
        return StoreDeployResult(ok=False, path=target_path, bytes=0,
                                 error=f"/file/print فشل: {e}")
    found_id = None
    for row in (existing or []):
        if (row.get("name") or "") == target_path:
            found_id = row.get(".id") or row.get("id")
            break
    try:
        if found_id:
            client.run("/file/set", attrs={".id": found_id,
                                           "contents": html})
        else:
            client.run("/file/add", attrs={"name": target_path,
                                           "contents": html})
    except Exception as e:  # noqa: BLE001
        return StoreDeployResult(ok=False, path=target_path,
                                 bytes=len(html),
                                 error=f"رفع متجر الراوتر فشل: {e}")
    return StoreDeployResult(ok=True, path=target_path, bytes=len(html))


# ═══════════════════════════════════════════════════════════════
# صفحة المتجر — تطبيق صفحة واحدة (شاشة دخول + أربعة تبويبات بشريط
# سفلي) بنفس لغة تصميم عائلة «التدرج الاحترافي»: تدرجات، بطاقات
# بظلال ناعمة، RTL جوال أولًا، وخط المراعي من مسار نسبي fonts/
# مع سقوط آمن لخطوط النظام (لا أي ملف خارجي إجباري — الصفحة قد
# تعمل قبل فتح الإنترنت). كل النصوص عربية
# والأخطاء ودّية. ملاحظات راوتر أو إس:
#   - $(link-login-only) و $(link-orig) في نموذج الدخول المخفي
#     يملؤها خادم الهوت سبوت عند تقديم الصفحة (الملف داخل مجلد
#     hotspot/). أي $(...) أخرى غير معرّفة تُترك كما هي.
#   - JS يتجنّب كتابة '$(' حرفيًا حتى لا يلتقطها محلّل الراوتر.
# ═══════════════════════════════════════════════════════════════

STORE_PAGE_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="pragma" content="no-cache">
<title>متجر البطاقات — {{TENANT_NAME}}</title>
<style>
/* ───── أساس التصميم: نفس لغة قوالب «التدرج الاحترافي» ───── */
/* خط المراعي المعتمد — مسار نسبي fonts/ (نفس مجلد الهوت سبوت
   بجانب store.html). إن غابت الملفات تسقط الصفحة لخطوط النظام. */
@font-face{font-family:'Almarai';
  src:url('fonts/Almarai-Regular.woff2') format('woff2');
  font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:'Almarai';
  src:url('fonts/Almarai-Bold.woff2') format('woff2');
  font-weight:700;font-style:normal;font-display:swap}
*{margin:0;padding:0;box-sizing:border-box;
  font-family:'Almarai','Tajawal','Segoe UI',Tahoma,Arial,sans-serif;
  -webkit-tap-highlight-color:transparent}
:root{--accent:{{ACCENT_COLOR}};--ink:#0f172a;--mut:#64748b;
  --line:#e2e8f0;--ok:#10b981;--bad:#ef4444;--warn:#f59e0b}
body{min-height:100vh;background:
  linear-gradient(160deg,#eef2ff 0%,#ffffff 45%,#f0f9ff 100%);
  color:var(--ink);display:flex;justify-content:center;overflow-x:hidden}
.phone{width:100%;max-width:430px;padding:0 14px 96px;position:relative}

/* ───── الهيدر العائم ───── */
.top{display:flex;align-items:center;gap:10px;padding:16px 4px 12px}
.top img{max-height:40px;max-width:96px;object-fit:contain;border-radius:10px}
.top .t-name{font-size:16px;font-weight:800}
.top .t-sub{font-size:10.5px;color:var(--mut)}
.top .t-out{margin-inline-start:auto;background:#fff;border:1px solid var(--line);
  color:var(--mut);font-size:11px;font-weight:700;border-radius:999px;
  padding:7px 14px;cursor:pointer;display:none}

/* ───── شاشة الدخول ───── */
.hero{position:relative;border-radius:24px;overflow:hidden;
  background:linear-gradient(135deg,var(--accent),#1e293b);
  box-shadow:0 18px 40px rgba(15,23,42,.25);padding:28px 22px;color:#fff;margin-top:8px}
.shape{position:absolute;border-radius:30px;transform:rotate(-45deg);
  pointer-events:none;opacity:.14;background:#fff}
.s1{width:220px;height:220px;top:-120px;left:-60px}
.s2{width:140px;height:140px;bottom:-70px;right:-40px;opacity:.09}
.hero h1{font-size:20px;font-weight:800;position:relative;z-index:2}
.hero p{font-size:12px;opacity:.9;margin:6px 0 20px;line-height:1.8;position:relative;z-index:2}
.f{position:relative;z-index:2;margin-bottom:12px}
.f label{display:block;font-size:11px;font-weight:700;margin-bottom:6px;color:#e2e8f0}
.f input{width:100%;background:rgba(255,255,255,.12);
  border:1px solid rgba(255,255,255,.35);border-radius:14px;color:#fff;
  font-size:15px;font-weight:600;padding:13px 16px;outline:0;transition:.25s}
.f input::placeholder{color:rgba(255,255,255,.5);font-weight:400}
.f input:focus{border-color:#fff;background:rgba(255,255,255,.18);
  box-shadow:0 0 0 3px rgba(255,255,255,.12)}
.btn-main{position:relative;z-index:2;display:block;width:100%;margin-top:6px;
  background:#fff;color:var(--accent);border:0;border-radius:999px;
  font-size:14.5px;font-weight:800;padding:14px;cursor:pointer;
  box-shadow:0 8px 20px rgba(0,0,0,.18);transition:transform .15s,opacity .2s}
.btn-main:active{transform:scale(.97)}
.btn-main[disabled]{opacity:.6;cursor:wait}
.back-login{display:block;text-align:center;margin-top:14px;position:relative;z-index:2}
.back-login a{color:#e2e8f0;font-size:11.5px;text-decoration:none;opacity:.85}

/* ───── تنبيهات ───── */
.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%) translateY(80px);
  z-index:9000;max-width:90%;background:var(--ink);color:#fff;font-size:12.5px;
  font-weight:700;border-radius:14px;padding:12px 20px;opacity:0;
  transition:.3s;box-shadow:0 14px 30px rgba(15,23,42,.35);text-align:center}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.ok{background:var(--ok)}.toast.bad{background:var(--bad)}
.inline-err{background:rgba(239,68,68,.95);color:#fff;border-radius:12px;
  padding:10px 12px;font-size:12px;margin-bottom:14px;text-align:center;
  position:relative;z-index:2;display:none}

/* ───── بطاقة المحفظة ───── */
.wallet{position:relative;border-radius:22px;overflow:hidden;margin-top:10px;
  background:linear-gradient(135deg,var(--accent),#312e81);
  color:#fff;padding:22px 20px;box-shadow:0 16px 36px rgba(49,46,129,.3)}
.wallet .w-label{font-size:11px;opacity:.85;font-weight:700}
.wallet .w-balance{font-size:34px;font-weight:800;margin:4px 0 2px;direction:ltr;text-align:right}
.wallet .w-balance small{font-size:14px;font-weight:700;opacity:.85;margin-inline-start:6px}
.wallet .w-name{font-size:12px;opacity:.9;margin-top:10px;display:flex;align-items:center;gap:6px}
.wallet .w-refresh{position:absolute;top:16px;left:16px;background:rgba(255,255,255,.16);
  border:0;color:#fff;width:34px;height:34px;border-radius:50%;cursor:pointer;font-size:15px}

/* ───── أقسام الرئيسية ───── */
.sec-title{display:flex;justify-content:space-between;align-items:center;
  margin:22px 4px 10px;font-size:14px;font-weight:800}
.sec-title small{font-size:10.5px;color:var(--accent);font-weight:700}
.card{background:#fff;border:1px solid var(--line);border-radius:18px;
  padding:16px;box-shadow:0 8px 18px rgba(15,23,42,.05)}
.recharge .r-row{display:flex;flex-direction:column;gap:10px}
.recharge input{width:100%;border:1.5px solid var(--line);border-radius:14px;
  font-size:15px;font-weight:600;padding:12px 14px;outline:0;transition:.2s;
  text-align:center;letter-spacing:1px}
.recharge input:focus{border-color:var(--accent);
  box-shadow:0 0 0 3px rgba(79,70,229,.12)}
.btn-acc{background:var(--accent);color:#fff;border:0;border-radius:14px;
  font-size:13.5px;font-weight:800;padding:13px;cursor:pointer;width:100%;
  transition:filter .15s,opacity .2s}
.btn-acc:active{filter:brightness(.9)}
.btn-acc[disabled]{opacity:.6;cursor:wait}

/* ───── معرض الباقات ───── */
.pkgs{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.pkg{background:#fff;border:1px solid var(--line);border-radius:18px;
  padding:14px 12px;text-align:center;box-shadow:0 8px 18px rgba(15,23,42,.05);
  position:relative;overflow:hidden}
.pkg::before{content:'';position:absolute;top:0;right:0;left:0;height:4px;
  background:var(--pkg-color,var(--accent))}
.pkg .p-name{font-size:13px;font-weight:800;margin-bottom:4px}
.pkg .p-meta{font-size:10.5px;color:var(--mut);line-height:1.7;min-height:30px}
.pkg .p-amt{font-size:19px;font-weight:800;color:var(--accent);margin:6px 0}
.pkg .p-amt small{font-size:10px;color:var(--mut);font-weight:700}
.pkg .p-buy{background:var(--accent);color:#fff;border:0;border-radius:999px;
  font-size:11.5px;font-weight:800;padding:8px 18px;cursor:pointer;width:100%}
.pkg .p-buy[disabled]{background:#cbd5e1;cursor:not-allowed}
.pkg .p-out{font-size:10px;color:var(--bad);font-weight:700;margin-top:5px}
.empty{font-size:12px;color:var(--mut);text-align:center;padding:18px 8px}

/* ───── سجل المشتريات ───── */
.hist .h-item{display:flex;align-items:center;gap:12px;padding:12px 4px;
  border-bottom:1px solid #f1f5f9}
.hist .h-item:last-child{border-bottom:0}
.hist .h-ico{width:38px;height:38px;border-radius:12px;flex-shrink:0;
  background:rgba(79,70,229,.1);color:var(--accent);display:flex;
  align-items:center;justify-content:center;font-size:16px}
.hist .h-txt{flex:1;min-width:0}
.hist .h-txt b{display:block;font-size:12.5px}
.hist .h-txt span{font-size:10.5px;color:var(--mut)}
.hist .h-amt{font-size:12.5px;font-weight:800;color:var(--ink);
  direction:ltr;text-align:left}
.hist .h-amt small{display:block;font-size:9.5px;font-weight:700;
  text-align:left}

/* ───── شارات الحالة (chips) ───── */
.chip{display:inline-block;font-size:10px;font-weight:800;
  border-radius:999px;padding:3px 10px;white-space:nowrap}
.chip-active{background:rgba(16,185,129,.14);color:#047857}
.chip-unused{background:#f1f5f9;color:#475569}
.chip-expired{background:rgba(239,68,68,.12);color:#b91c1c}
.chip-consumed{background:rgba(245,158,11,.14);color:#92400e}
.chip-revoked{background:rgba(239,68,68,.12);color:#b91c1c}

/* ───── بطاقاتي ───── */
.mycards{display:flex;flex-direction:column;gap:10px}
.mc{background:#fff;border:1px solid var(--line);border-radius:18px;
  box-shadow:0 8px 18px rgba(15,23,42,.05);overflow:hidden;
  border-inline-start:5px solid var(--mc-color,var(--accent))}
.mc .mc-head{display:flex;align-items:center;gap:10px;padding:13px 14px;
  cursor:pointer}
.mc .mc-name{font-size:13px;font-weight:800;flex:1;min-width:0;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mc .mc-arrow{color:var(--mut);font-size:11px;transition:transform .2s}
.mc.open .mc-arrow{transform:rotate(180deg)}
.mc .mc-sub{display:flex;gap:8px;flex-wrap:wrap;padding:0 14px 12px;
  font-size:10.5px;color:var(--mut)}
.mc .mc-body{display:none;border-top:1px dashed var(--line);
  padding:12px 14px;background:#fbfcfe}
.mc.open .mc-body{display:block}
.mc .mc-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;
  margin-bottom:10px}
.mc .mc-cell{background:#fff;border:1px solid #f1f5f9;border-radius:10px;
  padding:7px 9px}
.mc .mc-cell .k{display:block;font-size:9.5px;color:var(--mut);
  font-weight:700}
.mc .mc-cell .v{display:block;font-size:11.5px;font-weight:800;
  margin-top:2px;word-break:break-all}
.mc .mc-cred{display:flex;gap:8px;margin-bottom:10px}
.mc .mc-cred .cr{flex:1;background:#fff;border:1.5px dashed var(--accent);
  border-radius:12px;padding:7px 9px;min-width:0}
.mc .mc-cred .cr small{display:block;font-size:9.5px;color:var(--mut);
  font-weight:700}
.mc .mc-cred .cr b{display:block;font-size:13px;direction:ltr;
  text-align:left;word-break:break-all}
.mc .mc-cred .cp{background:#fff;border:1.5px solid var(--line);
  border-radius:12px;width:38px;cursor:pointer;font-size:14px;
  color:var(--accent);flex-shrink:0}
.mc .mc-login{display:block;width:100%;background:
  linear-gradient(135deg,var(--ok),#059669);color:#fff;border:0;
  border-radius:999px;font-size:12.5px;font-weight:800;padding:11px;
  cursor:pointer;box-shadow:0 6px 16px rgba(16,185,129,.3)}
.mc .mc-login[disabled]{background:#cbd5e1;box-shadow:none;
  cursor:not-allowed}
.pager{display:flex;justify-content:center;align-items:center;gap:14px;
  margin-top:12px}
.pager button{background:#fff;border:1px solid var(--line);
  border-radius:999px;font-size:11.5px;font-weight:800;padding:8px 18px;
  cursor:pointer;color:var(--ink)}
.pager button[disabled]{opacity:.4;cursor:not-allowed}
.pager span{font-size:11px;color:var(--mut);font-weight:700}

/* ───── شريط التبويبات السفلي ───── */
.tabbar{position:fixed;bottom:0;left:50%;transform:translateX(-50%);
  z-index:8000;width:100%;max-width:430px;display:flex;
  background:#fff;border-top:1px solid var(--line);
  box-shadow:0 -8px 24px rgba(15,23,42,.08);
  padding:6px 6px calc(6px + env(safe-area-inset-bottom,0))}
.tabbar button{flex:1;background:none;border:0;cursor:pointer;
  padding:7px 2px;border-radius:14px;color:var(--mut);
  font-size:10px;font-weight:700;transition:.15s}
.tabbar button .ti{display:block;font-size:18px;line-height:1.25}
.tabbar button.on{color:var(--accent);background:rgba(79,70,229,.08)}

/* ───── نافذة نجاح الشراء (بيانات الكرت) ───── */
.modal{position:fixed;inset:0;z-index:9500;background:rgba(15,23,42,.55);
  display:none;align-items:center;justify-content:center;padding:20px}
.modal.show{display:flex}
.modal .m-card{background:#fff;border-radius:22px;max-width:340px;width:100%;
  padding:26px 22px;text-align:center;box-shadow:0 24px 60px rgba(0,0,0,.3)}
.modal .m-ico{width:58px;height:58px;border-radius:50%;background:rgba(16,185,129,.12);
  color:var(--ok);font-size:26px;display:flex;align-items:center;
  justify-content:center;margin:0 auto 12px}
.modal h3{font-size:16px;font-weight:800;margin-bottom:6px}
.modal p{font-size:11.5px;color:var(--mut);line-height:1.8;margin-bottom:14px}
.modal .m-cred{background:#f8fafc;border:1.5px dashed var(--accent);
  border-radius:14px;padding:12px;margin-bottom:16px;direction:ltr}
.modal .m-cred div{font-size:13px;font-weight:800;color:var(--ink);
  word-break:break-all;padding:2px 0}
.modal .m-cred small{font-size:10px;color:var(--mut);font-weight:700;display:block}
.modal .m-close{background:var(--accent);color:#fff;border:0;border-radius:999px;
  font-size:13px;font-weight:800;padding:12px 36px;cursor:pointer;width:100%}

/* ───── المتجر المتقدّم: أزرار إجراءات المحفظة ───── */
.wact{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.wact button{display:flex;flex-direction:column;align-items:center;gap:4px;
  background:#fff;border:1px solid var(--line);border-radius:16px;
  padding:14px 8px;cursor:pointer;box-shadow:0 8px 18px rgba(15,23,42,.05);
  font-size:12px;font-weight:800;color:var(--ink);transition:transform .15s}
.wact button:active{transform:scale(.97)}
.wact button .wi{font-size:22px}
.wact button.dep .wi{color:var(--ok)}
.wact button.wd .wi{color:var(--accent)}

/* ───── قائمة طلباتي (إيداع/سحب) ───── */
.reqs .rq{display:flex;align-items:center;gap:10px;padding:11px 4px;
  border-bottom:1px solid #f1f5f9}
.reqs .rq:last-child{border-bottom:0}
.reqs .rq-ico{width:34px;height:34px;border-radius:10px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:15px;
  background:rgba(79,70,229,.1);color:var(--accent)}
.reqs .rq-ico.dep{background:rgba(16,185,129,.12);color:#047857}
.reqs .rq-txt{flex:1;min-width:0}
.reqs .rq-txt b{display:block;font-size:12px}
.reqs .rq-txt span{font-size:10px;color:var(--mut)}
.reqs .rq-amt{font-size:12.5px;font-weight:800;direction:ltr;text-align:left}

/* ───── ورقة منزلقة (modal طويلة قابلة للتمرير) ───── */
.sheet{position:fixed;inset:0;z-index:9500;background:rgba(15,23,42,.55);
  display:none;align-items:flex-end;justify-content:center}
.sheet.show{display:flex}
.sheet .sh-card{background:#fff;border-radius:22px 22px 0 0;width:100%;
  max-width:430px;max-height:92vh;overflow-y:auto;padding:18px 16px
  calc(18px + env(safe-area-inset-bottom,0));box-shadow:0 -16px 40px rgba(0,0,0,.3)}
.sheet .sh-head{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.sheet .sh-head h3{font-size:15px;font-weight:800;flex:1}
.sheet .sh-x{background:#f1f5f9;border:0;width:32px;height:32px;border-radius:50%;
  font-size:15px;cursor:pointer;color:var(--mut)}
.sheet .f label{color:var(--ink)}
.sheet .f input,.sheet .f select,.sheet .f textarea{width:100%;
  border:1.5px solid var(--line);border-radius:12px;font-size:14px;
  font-weight:600;padding:11px 13px;outline:0;background:#fff;color:var(--ink)}
.sheet .f input:focus,.sheet .f select:focus{border-color:var(--accent)}
.sheet .f textarea{min-height:64px;resize:vertical;font-weight:500}
.file-row{display:flex;align-items:center;gap:8px;border:1.5px dashed var(--line);
  border-radius:12px;padding:10px 12px;font-size:12px;color:var(--mut);cursor:pointer}
.file-row.has{border-color:var(--ok);color:#047857;font-weight:700}

/* ───── محافظ الاستلام (للنسخ + QR) ───── */
.paym{border:1px solid var(--line);border-radius:14px;padding:12px;margin-bottom:10px}
.paym .pm-top{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.paym .pm-top b{font-size:13px;font-weight:800;flex:1}
.paym .pm-num{display:flex;align-items:center;gap:8px;background:#f8fafc;
  border:1.5px dashed var(--accent);border-radius:10px;padding:8px 10px}
.paym .pm-num b{flex:1;font-size:13.5px;direction:ltr;text-align:left;word-break:break-all}
.paym .pm-num .cp{background:#fff;border:1.5px solid var(--line);border-radius:10px;
  width:34px;height:34px;cursor:pointer;font-size:13px;color:var(--accent);flex-shrink:0}
.paym .pm-name{font-size:10.5px;color:var(--mut);margin-top:5px}
.paym .pm-qr{display:block;max-width:120px;max-height:120px;margin:8px auto 0;
  border-radius:10px;border:1px solid var(--line)}
.paym .pm-hint{font-size:10.5px;color:var(--mut);margin-top:6px;line-height:1.7}

/* ───── قنوات الدفع كبطاقات قابلة للاختيار ───── */
.pmgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px}
.pmcard{display:flex;flex-direction:column;align-items:center;gap:5px;
  background:#fff;border:1.5px solid var(--line);border-radius:14px;
  padding:12px 6px;cursor:pointer;position:relative;text-align:center;
  font-family:inherit;transition:border-color .15s,box-shadow .15s,transform .1s}
.pmcard:active{transform:scale(.97)}
.pmcard.sel{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.12)}
.pmcard .pmc-logo{width:34px;height:34px;border-radius:9px;object-fit:cover;
  border:1px solid var(--line)}
.pmcard .pmc-ico{font-size:25px;line-height:1.1}
.pmcard .pmc-name{font-size:11px;font-weight:800;color:var(--ink);line-height:1.3;
  word-break:break-word}
.pmcard .pmc-tick{position:absolute;top:5px;inset-inline-start:5px;width:18px;
  height:18px;border-radius:50%;background:var(--accent);color:#fff;font-size:11px;
  display:none;align-items:center;justify-content:center}
.pmcard.sel .pmc-tick{display:flex}
.pay-selected{background:#f8fafc;border:1px solid var(--line);border-radius:14px;
  padding:12px;margin-bottom:12px}
.pay-selected .ps-label{font-size:11px;font-weight:800;color:var(--mut);margin-bottom:6px}
.pay-selected .pm-num{display:flex;align-items:center;gap:8px;background:#fff;
  border:1.5px dashed var(--accent);border-radius:10px;padding:9px 11px}
.pay-selected .pm-num b{flex:1;font-size:15px;font-weight:800;direction:ltr;
  text-align:left;word-break:break-all;color:var(--ink)}
.pay-selected .pm-num .cp{background:var(--accent);color:#fff;border:0;
  border-radius:10px;padding:8px 14px;font-size:11.5px;font-weight:800;
  cursor:pointer;white-space:nowrap;flex-shrink:0}
.pay-selected .pm-name{font-size:11px;color:var(--mut);margin-top:7px}
.pay-selected .pm-qr{display:block;max-width:130px;max-height:130px;
  margin:9px auto 0;border-radius:10px;border:1px solid var(--line)}
.pay-selected .pm-hint{font-size:11px;color:var(--mut);margin-top:7px;line-height:1.7}

/* ───── شات الدعم ───── */
.fab-chat{position:fixed;bottom:78px;left:14px;z-index:7000;width:54px;height:54px;
  border-radius:50%;background:linear-gradient(135deg,var(--accent),#312e81);
  color:#fff;border:0;font-size:23px;cursor:pointer;
  box-shadow:0 10px 26px rgba(49,46,129,.4);display:none}
.fab-chat .badge{position:absolute;top:-3px;right:-3px;background:var(--bad);
  color:#fff;font-size:10px;font-weight:800;min-width:18px;height:18px;
  border-radius:9px;padding:0 4px;display:none;align-items:center;justify-content:center}
.fab-chat .badge.show{display:flex}
.chat-body{max-height:54vh;overflow-y:auto;padding:6px 2px;display:flex;
  flex-direction:column;gap:8px}
.msg{max-width:80%;padding:9px 12px;border-radius:14px;font-size:12.5px;
  line-height:1.7;word-break:break-word}
.msg small{display:block;font-size:9px;opacity:.6;margin-top:3px}
.msg.me{align-self:flex-start;background:var(--accent);color:#fff;border-bottom-right-radius:4px}
.msg.them{align-self:flex-end;background:#f1f5f9;color:var(--ink);border-bottom-left-radius:4px}
.msg img{display:block;max-width:100%;border-radius:10px;margin-top:6px}
.chat-bar{display:flex;align-items:center;gap:6px;margin-top:10px;
  border-top:1px solid var(--line);padding-top:10px}
.chat-bar input[type=text]{flex:1;border:1.5px solid var(--line);border-radius:999px;
  padding:10px 14px;font-size:13px;outline:0}
.chat-bar input[type=text]:focus{border-color:var(--accent)}
.chat-bar .cb{background:#f1f5f9;border:0;width:40px;height:40px;border-radius:50%;
  font-size:16px;cursor:pointer;color:var(--accent);flex-shrink:0}
.chat-bar .cb.send{background:var(--accent);color:#fff}
.wa-btn{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;
  background:#25d366;color:#fff;border:0;border-radius:12px;font-size:12.5px;
  font-weight:800;padding:11px;cursor:pointer;margin-bottom:10px}

/* ───── سبينر ───── */
.spin{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.4);
  border-top-color:#fff;border-radius:50%;animation:hrSpin .7s linear infinite;
  vertical-align:-2px;margin-inline-end:6px}
@keyframes hrSpin{to{transform:rotate(360deg)}}
.skel{background:linear-gradient(90deg,#f1f5f9 25%,#e2e8f0 50%,#f1f5f9 75%);
  background-size:200% 100%;animation:hrSkel 1.2s infinite;border-radius:12px}
@keyframes hrSkel{from{background-position:200% 0}to{background-position:-200% 0}}
.hide{display:none!important}
.foot{text-align:center;font-size:10px;color:#94a3b8;margin-top:26px}
</style>
</head>
<body>
<div class="phone">

  <!-- ═══ شريط حالة الاتصال بالخادم (فحص ذاتي) ═══
       عند تحميل الصفحة — قبل أي محاولة دخول — ننادي
       /api/v1/store/ping ونعرض الحالة الدقيقة هنا بدل رسالة «تأكد
       من الشبكة» العامة المضلّلة. يملؤه JS (setConn) بنصّ ولون حسب
       الحالة: يفحص / ✓ متصل / غير مضبوط / عنوان محلي / تعذّر الوصول
       (منفذ خاطئ أو خارج walled-garden) / خطأ HTTP. -->
  <div id="connStatus" role="status" style="display:none;color:#fff;
       border-radius:14px;padding:12px 15px;margin:14px 0 2px;font-size:12px;
       font-weight:700;line-height:1.8;text-align:center;
       box-shadow:0 10px 24px rgba(15,23,42,.18)"></div>

  <!-- الهيدر -->
  <div class="top">
    <img id="hLogo" src="{{TENANT_LOGO_URL}}" alt=""
         onerror="this.style.display='none'">
    <div>
      <div class="t-name">{{TENANT_NAME}}</div>
      <div class="t-sub">متجر البطاقات الإلكتروني</div>
    </div>
    <button class="t-out" id="btnLogout" type="button">خروج</button>
  </div>

  <!-- ═══ شاشة الدخول ═══ -->
  <section id="scrLogin">
    <div class="hero">
      <div class="shape s1"></div><div class="shape s2"></div>
      <h1>أهلًا بك 👋</h1>
      <p>سجّل دخولك برقم جوالك وكلمة مرور حسابك لشحن رصيدك وشراء بطاقات الإنترنت مباشرة.</p>
      <div class="inline-err" id="loginErr"></div>
      <div class="f"><label>رقم الجوال</label>
        <input id="inMobile" type="tel" inputmode="tel" autocomplete="tel"
               placeholder="05xxxxxxxx"></div>
      <div class="f"><label>كلمة المرور</label>
        <input id="inPass" type="password" autocomplete="current-password"
               placeholder="••••••••"></div>
      <button class="btn-main" id="btnLogin" type="button">تسجيل الدخول</button>
      <span class="back-login">
        <a href="#" id="goRegister">✨ ليس لديك حساب؟ أنشئ حسابًا جديدًا</a>
      </span>
      <span class="back-login"><a href="login.html">↪ العودة لصفحة الدخول للشبكة</a></span>
    </div>
  </section>

  <!-- ═══ شاشة التسجيل الذاتي ═══
       تنشئ حسابًا فعّالًا فورًا عبر /api/v1/store/register ثم تدخل
       تلقائيًا (نفس توكن الدخول). الاسم الثلاثي + الجوال + كلمة المرور. -->
  <section id="scrRegister" class="hide">
    <div class="hero">
      <div class="shape s1"></div><div class="shape s2"></div>
      <h1>إنشاء حساب 🎉</h1>
      <p>أنشئ حسابك الآن لتشحن رصيدك وتشتري بطاقات الإنترنت مباشرة — التفعيل فوري بلا انتظار.</p>
      <div class="inline-err" id="regErr"></div>
      <div class="f"><label>الاسم الثلاثي</label>
        <input id="rgName" type="text" autocomplete="name"
               placeholder="مثال: محمد أحمد علي"></div>
      <div class="f"><label>رقم الجوال</label>
        <input id="rgMobile" type="tel" inputmode="tel" autocomplete="tel"
               placeholder="05xxxxxxxx"></div>
      <div class="f"><label>كلمة المرور</label>
        <input id="rgPass" type="password" autocomplete="new-password"
               placeholder="٤ أحرف على الأقل"></div>
      <button class="btn-main" id="btnRegister" type="button">إنشاء الحساب</button>
      <span class="back-login"><a href="#" id="goLogin">↪ لديك حساب؟ سجّل الدخول</a></span>
    </div>
  </section>

  <!-- ═══ الشاشة الرئيسية (٤ تبويبات) ═══ -->
  <section id="scrHome" class="hide">

    <!-- ── تبويب ١: رصيدي ── -->
    <div id="tabWallet">
      <div class="wallet">
        <button class="w-refresh" id="btnRefresh" type="button" title="تحديث">⟳</button>
        <div class="w-label">رصيد المحفظة</div>
        <div class="w-balance"><span id="wBalance">…</span><small id="wCurrency"></small></div>
        <div class="w-name">👤 <span id="wName">…</span></div>
      </div>
      <div class="sec-title">شحن المحفظة <small>أدخل بيانات بطاقة الشحن</small></div>
      <div class="card recharge">
        <div class="r-row">
          <input id="inCardNo" type="text" inputmode="numeric"
                 autocomplete="off" placeholder="رقم / كود البطاقة">
          <input id="inCardPin" type="text" inputmode="numeric"
                 autocomplete="off" placeholder="الرقم السري (إن وُجد)">
          <button class="btn-acc" id="btnRedeem" type="button">⚡ شحن الرصيد</button>
        </div>
      </div>

      <!-- إجراءات: شحن بتحويل (إيداع) + سحب رصيد -->
      <div class="wact">
        <button type="button" class="dep" id="btnOpenDeposit">
          <span class="wi">💰</span>شحن بتحويل</button>
        <button type="button" class="wd" id="btnOpenWithdraw">
          <span class="wi">💸</span>سحب رصيد</button>
      </div>

      <!-- طلباتي (إيداع/سحب) بحالتها -->
      <div class="sec-title">طلباتي <small id="reqCount"></small></div>
      <div class="card reqs" id="reqList">
        <div class="empty">لا توجد طلبات بعد.</div>
      </div>
    </div>

    <!-- ── تبويب ٢: المعرض ── -->
    <div id="tabMarket" class="hide">
      <div class="sec-title">باقات الإنترنت <small>شراء فوري من رصيدك</small></div>
      <div class="pkgs" id="pkgList">
        <div class="skel" style="height:140px"></div>
        <div class="skel" style="height:140px"></div>
      </div>
    </div>

    <!-- ── تبويب ٣: بطاقاتي ── -->
    <div id="tabCards" class="hide">
      <div class="sec-title">بطاقاتي <small id="mcCount"></small></div>
      <div class="mycards" id="mcList">
        <div class="skel" style="height:64px"></div>
        <div class="skel" style="height:64px"></div>
      </div>
      <div class="pager hide" id="mcPager">
        <button type="button" id="mcPrev">السابق</button>
        <span id="mcPage"></span>
        <button type="button" id="mcNext">التالي</button>
      </div>
    </div>

    <!-- ── تبويب ٤: السجل ── -->
    <div id="tabHistory" class="hide">
      <div class="sec-title">سجل المشتريات <small id="hsCount"></small></div>
      <div class="card hist" id="histList">
        <div class="empty">لا توجد مشتريات بعد.</div>
      </div>
      <div class="pager hide" id="hsPager">
        <button type="button" id="hsPrev">السابق</button>
        <span id="hsPage"></span>
        <button type="button" id="hsNext">التالي</button>
      </div>
    </div>

    <p class="foot">© {{TENANT_NAME}} — متجر مدعوم من HobeRadius</p>
  </section>
</div>

<!-- ═══ شريط التبويبات السفلي ═══ -->
<nav class="tabbar hide" id="tabBar">
  <button type="button" data-tab="tabWallet" class="on">
    <span class="ti">💳</span>رصيدي</button>
  <button type="button" data-tab="tabMarket">
    <span class="ti">🛒</span>المعرض</button>
  <button type="button" data-tab="tabCards">
    <span class="ti">🎫</span>بطاقاتي</button>
  <button type="button" data-tab="tabHistory">
    <span class="ti">🧾</span>السجل</button>
</nav>

<!-- نموذج الدخول التلقائي بالبطاقة — يرسل بيانات الكرت إلى مدخل
     الهوت سبوت. $(link-login-only) و $(link-orig) يملؤهما الراوتر
     عند تقديم الصفحة (نفس قيم نموذج sendin في قوالب الدخول؛
     dst في قيمة نموذج = $(link-orig) بلا esc). إن لم تُستبدل
     (الصفحة فُتحت خارج خادم الهوت سبوت) يسقط JS إلى
     login.html?u=..&p=.. حيث يلتقطها سكربت الدخول التلقائي R4. -->
<form id="hsLogin" name="hslogin" method="post"
      action="$(link-login-only)" style="display:none">
  <input type="hidden" name="username" value="">
  <input type="hidden" name="password" value="">
  <input type="hidden" name="dst" value="$(link-orig)">
  <input type="hidden" name="popup" value="false">
</form>

<!-- نافذة نجاح الشراء -->
<div class="modal" id="buyModal">
  <div class="m-card">
    <div class="m-ico">✓</div>
    <h3>تم الشراء بنجاح!</h3>
    <p>هذه بيانات بطاقتك — استخدمها في صفحة دخول الشبكة. ستجدها دائمًا في «مشترياتي».</p>
    <div class="m-cred">
      <small>اسم المستخدم</small><div id="mUser">—</div>
      <small style="margin-top:8px">كلمة المرور</small><div id="mPass">—</div>
    </div>
    <button class="m-close" id="btnModalLogin" type="button"
            style="margin-bottom:8px;background:var(--ok)">🔓 دخول بهذه البطاقة الآن</button>
    <button class="m-close" id="btnModalClose" type="button">تم — فهمت</button>
  </div>
</div>

<!-- ═══ ورقة شحن المحفظة بتحويل (طلب إيداع) ═══ -->
<div class="sheet" id="depositSheet">
  <div class="sh-card">
    <div class="sh-head">
      <h3>💰 شحن المحفظة بتحويل</h3>
      <button class="sh-x" type="button" data-close-sheet="depositSheet">✕</button>
    </div>
    <p style="font-size:11.5px;color:#64748b;line-height:1.8;margin-bottom:12px">
      حوّل المبلغ إلى إحدى المحافظ التالية، ثم عبّئ بيانات التحويل وارفع
      صورة الوصل. يُضاف الرصيد بعد تأكيد المزوّد.</p>
    <div class="f"><label>اختر قناة الدفع التي حوّلت إليها</label></div>
    <div id="payMethods" class="pmgrid">
      <div class="skel" style="height:66px"></div>
      <div class="skel" style="height:66px"></div>
    </div>
    <!-- تفاصيل القناة المختارة: الرقم بارزًا + نسخ + QR + تعليمات -->
    <div id="paySelected" class="pay-selected hide"></div>
    <div class="inline-err" id="depErr" style="position:static;margin:6px 0"></div>
    <div class="f"><label>المبلغ المحوَّل</label>
      <input id="depAmount" type="number" inputmode="decimal" min="0"
             step="0.01" placeholder="0.00"></div>
    <div class="f"><label>رقم الجوال الذي حوّلت منه</label>
      <input id="depPhone" type="tel" inputmode="tel" placeholder="05xxxxxxxx"></div>
    <div class="f"><label>الرقم المرجعي للدفعة</label>
      <input id="depRef" type="text" placeholder="رقم العملية / المرجع"></div>
    <div class="f"><label>اسم صاحب الحساب المحوِّل</label>
      <input id="depPayer" type="text" placeholder="الاسم كما في حسابك"></div>
    <div class="f"><label>صورة الوصل</label>
      <label class="file-row" id="depFileRow" for="depReceipt">
        <span>📎</span><span id="depFileName">اضغط لإرفاق صورة الوصل</span></label>
      <input id="depReceipt" type="file" accept="image/*" style="display:none"></div>
    <button class="btn-acc" id="btnDepositSubmit" type="button"
            style="margin-top:6px">إرسال طلب الشحن</button>
  </div>
</div>

<!-- ═══ ورقة سحب الرصيد ═══ -->
<div class="sheet" id="withdrawSheet">
  <div class="sh-card">
    <div class="sh-head">
      <h3>💸 سحب رصيد</h3>
      <button class="sh-x" type="button" data-close-sheet="withdrawSheet">✕</button>
    </div>
    <p style="font-size:11.5px;color:#64748b;line-height:1.8;margin-bottom:12px">
      اطلب تحويل رصيدك (كله أو جزء) إلى حسابك. يُنفّذ التحويل يدويًا
      ويُخصم الرصيد بعد تأكيد المزوّد.</p>
    <div class="inline-err" id="wdErr" style="position:static;margin:6px 0"></div>
    <div class="f"><label>المبلغ المطلوب سحبه</label>
      <input id="wdAmount" type="number" inputmode="decimal" min="0"
             step="0.01" placeholder="0.00">
      <div style="font-size:10.5px;color:#64748b;margin-top:5px">
        رصيدك الحالي: <b id="wdBalance" style="direction:ltr">—</b></div></div>
    <div class="f"><label>اسم صاحب الحساب</label>
      <input id="wdName" type="text" placeholder="الاسم المستلِم للتحويل"></div>
    <div class="f"><label>رقم الحساب الذي نحوّل إليه</label>
      <input id="wdAccount" type="text" placeholder="رقم الحساب / الجوال"></div>
    <button class="btn-acc" id="btnWithdrawSubmit" type="button"
            style="margin-top:6px">إرسال طلب السحب</button>
  </div>
</div>

<!-- ═══ ورقة شات الدعم ═══ -->
<div class="sheet" id="chatSheet">
  <div class="sh-card">
    <div class="sh-head">
      <h3>💬 الدعم</h3>
      <button class="sh-x" type="button" data-close-sheet="chatSheet">✕</button>
    </div>
    <button class="wa-btn" id="btnWhatsapp" type="button" style="display:none">
      <span>📱</span> تحويل المحادثة إلى واتساب</button>
    <div class="chat-body" id="chatBody">
      <div class="empty">ابدأ المحادثة — نحن هنا للمساعدة.</div>
    </div>
    <div class="chat-bar">
      <button class="cb" id="btnChatAttach" type="button" title="إرفاق صورة">📎</button>
      <input id="chatImage" type="file" accept="image/*" style="display:none">
      <input id="chatText" type="text" placeholder="اكتب رسالتك…">
      <button class="cb send" id="btnChatSend" type="button" title="إرسال">➤</button>
    </div>
  </div>
</div>

<!-- زر الشات العائم -->
<button class="fab-chat" id="fabChat" type="button" title="الدعم">💬
  <span class="badge" id="chatBadge">0</span></button>

<div class="toast" id="toast"></div>

<script>
/* ═════════ متجر المايكروتيك — منطق الصفحة ═════════
   الصفحة تعمل من الراوتر (origin = IP الراوتر) وتتخاطب مع سيرفر
   الراديوس فقط عبر نقاط /api/v1/store/*. التوكن الموقّع يُحفظ في
   sessionStorage — يموت بإغلاق المتصفح وتفحص صلاحيته في السيرفر. */
(function () {
  'use strict';

  /* عنوان سيرفر الراديوس — يُحقن عند النشر من إعداد
     network.radius_server_ip. يجب أن يكون ضمن walled-garden. */
  var API = '{{API_BASE}}';
  var TKEY = 'hr_store_token';
  /* مفتاح تطبيق المتجر — يُحقن عند النشر (services/store_key) ويُرسل
     في ترويسة X-Store-Key مع كل نداء. يرفض الـ API أي طلب لا يحمل
     المفتاح الصحيح، فلا يستدعي النقاطَ إلا هذا المتجر المنشور. */
  var SKEY = '{{STORE_KEY}}';
  /* رقم واتساب الدعم — يُحقن عند النشر (أرقام فقط، صيغة دولية). فارغ
     يخفي زر «تحويل للواتساب». */
  var WA = '{{SUPPORT_WHATSAPP}}';

  var $ = function (id) { return document.getElementById(id); };

  /* ───── حارس عنوان الـ API + الفحص الذاتي للاتصال ─────
     السبب الجذري لرسالة «تأكد من الشبكة» المضلّلة: العنوان قد يكون
     مضبوطًا لكنه غير قابل للوصول فعليًا — منفذ خاطئ (مثلاً السيرفر
     على 5050 والعنوان بلا منفذ فيُضرب 80)، أو خارج walled-garden،
     أو النسخة المنشورة على الراوتر قديمة. فبدل ترك الزبون يخمّن
     نكشف الحالة الدقيقة بفحص ذاتي (ping) عند الإقلاع ونعرضها في
     شريط الحالة أعلى الصفحة.
     apiReason: '' = العنوان صالح شكليًا | empty/placeholder/loopback
     = لن يصل أبدًا من أجهزة الزبائن. '$'+'{' مفصولة حتى لا يلتقطها
     أي محلّل قوالب. */
  function apiReason() {
    var a = String(API || '').trim();
    if (!a) return 'empty';
    if (a.indexOf('{' + '{') !== -1) return 'placeholder';
    if (/^(https?:\/\/)?(127(\.\d{1,3}){3}|localhost|0\.0\.0\.0|\[?::1\]?)(:\d+)?\/?$/i
        .test(a)) return 'loopback';
    return '';
  }
  function apiUnusable() { return apiReason() !== ''; }

  /* شريط الحالة: نص ولون حسب الحالة. يبقى ظاهرًا في كل الحالات عدا
     «متصل» (يتلاشى بعد ثوانٍ) فلا يزعج الزبون السليم. */
  var connHideTimer = null;
  function setConn(kind, info) {
    var el = $('connStatus');
    if (!el) return;
    var addr = esc(String(API || '').trim() || '—');
    var bg = '#b91c1c', txt = '';
    if (kind === 'checking') {
      bg = '#475569';
      txt = '<span class="spin"></span> جارٍ التحقق من الاتصال بالخادم…';
    } else if (kind === 'ok') {
      bg = '#047857'; txt = '✓ متصل بالخادم';
    } else if (kind === 'empty') {
      txt = 'لم يُضبط عنوان سيرفر الراديوس — لن يعمل المتجر من أجهزة ' +
            'الزبائن. اضبط network.radius_server_ip في الإعدادات ثم ' +
            'أعِد نشر store.html.';
    } else if (kind === 'placeholder') {
      txt = 'لم تُستبدل قيمة العنوان عند النشر — أعِد نشر store.html ' +
            'من مصمّم صفحة الدخول (النسخة الحالية قديمة).';
    } else if (kind === 'loopback') {
      txt = 'العنوان المضبوط محلي (' + addr + ') ولا يعمل من أجهزة ' +
            'الزبائن — اضبط IP السيرفر على الشبكة المحلية (مثل ' +
            '192.168.x.x مع المنفذ إن لزم) لا 127.0.0.1، ثم أعِد النشر.';
    } else if (kind === 'http') {
      bg = '#b45309';
      txt = 'وصل الطلب للخادم لكنه ردّ بخطأ HTTP ' +
            (info && info.status ? info.status : '') +
            ' — راجع مزوّد الخدمة.';
    } else { /* network */
      txt = 'تعذّر الوصول إلى الخادم على ' + addr + ' — تأكد أن ' +
            'العنوان والمنفذ صحيحان وأن الخادم مسموح به في ' +
            'walled-garden لدى مزوّد الخدمة.';
    }
    el.innerHTML = txt;
    el.style.background = bg;
    el.style.display = 'block';
    clearTimeout(connHideTimer);
    if (kind === 'ok') {
      connHideTimer = setTimeout(function () { el.style.display = 'none'; },
                                 2600);
    }
  }

  /* نداء ping خفيف (بلا توكن) يحمل مفتاح المتجر X-Store-Key مع مهلة
     قصوى عبر AbortController حتى لا تتعلّق الصفحة على عنوان لا يستجيب.
     يميّز فشل الشبكة (لم نصل) عن خطأ HTTP (وصلنا وردّ بخطأ — بما فيه
     403 لمفتاح خاطئ، فيظهر في شريط الحالة كخطأ HTTP واضح). إرسال
     المفتاح يجعله طلبًا غير بسيط (preflight) — وهو مدعوم (يرد 204). */
  function pingApi() {
    var ctrl = ('AbortController' in window) ? new AbortController() : null;
    var timer = ctrl
      ? setTimeout(function () { ctrl.abort(); }, 6000) : null;
    var opts = ctrl ? { signal: ctrl.signal } : {};
    if (SKEY) opts.headers = { 'X-Store-Key': SKEY };
    return fetch(API + '/api/v1/store/ping', opts)
      .then(function (res) {
        if (timer) clearTimeout(timer);
        if (!res.ok) {
          var e = new Error('http'); e.kind = 'http';
          e.status = res.status; throw e;
        }
        return true;
      }, function () {
        if (timer) clearTimeout(timer);
        var e = new Error('network'); e.kind = 'network'; throw e;
      });
  }

  /* الفحص الذاتي عند الإقلاع: إن كان العنوان غير صالح شكليًا نعرض
     السبب فورًا، وإلا ننفّذ ping فعليًا. يعيد Promise<boolean>:
     true = الخادم قابل للوصول. */
  function selfCheck() {
    var reason = apiReason();
    if (reason) { setConn(reason); return Promise.resolve(false); }
    setConn('checking');
    return pingApi().then(
      function () { setConn('ok'); return true; },
      function (e) { setConn(e.kind || 'network', e); return false; });
  }

  /* حارس قبل أي نداء API: عنوان غير صالح شكليًا → لا fetch (يرمي
     خطأ شبكة مضلّلًا) بل إظهار السبب الحقيقي في شريط الحالة. */
  function guardApi() {
    var reason = apiReason();
    if (!reason) return false;
    setConn(reason);
    show('login');
    return true;
  }

  /* ───── تنبيه عائم (توست) بالعربية ───── */
  var toastTimer = null;
  function toast(msg, kind) {
    var t = $('toast');
    t.textContent = msg;
    t.className = 'toast show' + (kind ? ' ' + kind : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.className = 'toast'; }, 3500);
  }

  /* ───── نداء API موحّد: توكن + أخطاء عربية ودّية ───── */
  function api(path, opts) {
    opts = opts || {};
    /* حارس العنوان: عنوان غير صالح → لا fetch (يرمي خطأ شبكة
       مضلّلًا) بل خطأ واضح بالسبب الحقيقي + إظهار شريط التحذير. */
    if (apiUnusable()) {
      guardApi();
      var ce = new Error('لم يُضبط عنوان سيرفر الراديوس في الإعدادات ' +
        '— لن يعمل المتجر من أجهزة الزبائن.');
      ce.code = 'api_unconfigured';
      return Promise.reject(ce);
    }
    var headers = { 'Content-Type': 'application/json' };
    if (SKEY) headers['X-Store-Key'] = SKEY;
    var token = sessionStorage.getItem(TKEY);
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return fetch(API + '/api/v1/store' + path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (j) {
        if (res.ok && j && j.ok) return j.data || {};
        var err = (j && j.error) || {};
        /* جلسة منتهية → عودة لشاشة الدخول بهدوء */
        if (res.status === 401 && token) { doLogout(true); }
        var e = new Error(err.message ||
          'تعذر الاتصال بالخادم — تأكد من الشبكة وحاول مجددًا.');
        e.code = err.code || 'request_failed';
        throw e;
      });
    }, function () {
      /* فشل الشبكة نفسها — العنوان غير قابل للوصول (منفذ خاطئ، أو
         الراديوس خارج walled-garden، أو نسخة store.html قديمة).
         نُحدّث شريط الحالة بالسبب الدقيق ونرمي رسالة تحيل إليه بدل
         «تأكد من الشبكة» العامة المضلّلة. */
      setConn('network');
      var e = new Error('تعذّر الوصول إلى الخادم — راجع شريط حالة ' +
        'الاتصال أعلى الصفحة (العنوان/المنفذ أو walled-garden، أو ' +
        'تواصل مع مزوّد الخدمة).');
      e.code = 'network_error';
      throw e;
    });
  }

  /* نداء multipart (FormData) للرفع (وصل/صورة شات) — لا نضبط
     Content-Type يدويًا فيضيف المتصفح boundary. نفس التوكن والمفتاح
     ومعالجة الأخطاء العربية في api(). */
  function apiForm(path, formData) {
    if (apiUnusable()) {
      guardApi();
      var ce = new Error('لم يُضبط عنوان الخادم.');
      ce.code = 'api_unconfigured';
      return Promise.reject(ce);
    }
    var headers = {};
    if (SKEY) headers['X-Store-Key'] = SKEY;
    var token = sessionStorage.getItem(TKEY);
    if (token) headers['Authorization'] = 'Bearer ' + token;
    return fetch(API + '/api/v1/store' + path, {
      method: 'POST', headers: headers, body: formData
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (j) {
        if (res.ok && j && j.ok) return j.data || {};
        var err = (j && j.error) || {};
        if (res.status === 401 && token) { doLogout(true); }
        var e = new Error(err.message || 'تعذر الاتصال بالخادم.');
        e.code = err.code || 'request_failed';
        throw e;
      });
    }, function () {
      setConn('network');
      var e = new Error('تعذّر الوصول إلى الخادم — راجع شريط الحالة أعلى الصفحة.');
      e.code = 'network_error';
      throw e;
    });
  }

  /* ───── تبديل الشاشات ───── */
  function show(screen) {
    $('scrLogin').classList.toggle('hide', screen !== 'login');
    $('scrRegister').classList.toggle('hide', screen !== 'register');
    $('scrHome').classList.toggle('hide', screen !== 'home');
    $('tabBar').classList.toggle('hide', screen !== 'home');
    $('btnLogout').style.display = screen === 'home' ? 'inline-block' : 'none';
    // زر الشات العائم يظهر داخل الرئيسية فقط؛ مغادرتها توقف الاستطلاع.
    $('fabChat').style.display = screen === 'home' ? 'block' : 'none';
    if (screen !== 'home' && typeof closeChat === 'function') closeChat();
  }

  /* ───── شريط التبويبات السفلي ─────
     بطاقاتي والسجل يُحمَّلان كسولًا عند أول فتح للتبويب —
     تخفيفًا على سيرفر الراديوس من شبكة الهوت سبوت. */
  var TABS = ['tabWallet', 'tabMarket', 'tabCards', 'tabHistory'];
  var loadedTabs = {};
  function openTab(id) {
    TABS.forEach(function (t) { $(t).classList.toggle('hide', t !== id); });
    Array.prototype.forEach.call(
      $('tabBar').querySelectorAll('button'),
      function (b) {
        b.classList.toggle('on', b.getAttribute('data-tab') === id);
      });
    if (id === 'tabCards' && !loadedTabs.cards) loadMyCards(1);
    if (id === 'tabHistory' && !loadedTabs.hist) loadHistory(1);
    window.scrollTo(0, 0);
  }

  /* ───── عرض الأرقام: المبالغ تأتي نصًا جاهزًا من السيرفر ───── */
  function fmtSpeed(kbps) {
    if (!kbps) return '';
    return kbps >= 1024 ? (Math.round(kbps / 102.4) / 10) + ' ميجا'
                        : kbps + ' كيلو';
  }
  function fmtDuration(mins) {
    if (!mins) return '';
    if (mins % 1440 === 0) return (mins / 1440) + ' يوم';
    if (mins % 60 === 0) return (mins / 60) + ' ساعة';
    return mins + ' دقيقة';
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fmtWhen(iso) {
    return String(iso || '').replace('T', ' ').slice(0, 16) || '—';
  }
  function fmtMB(mb) {
    if (!mb) return '';
    return mb >= 1024 ? (Math.round(mb / 102.4) / 10) + ' جيجا'
                      : mb + ' ميجا';
  }
  function fmtBytes(b) {
    if (!b) return '0';
    var mb = b / (1024 * 1024);
    return mb >= 1024 ? (Math.round(mb / 102.4) / 10) + ' جيجا'
                      : Math.max(1, Math.round(mb)) + ' ميجا';
  }

  /* ───── تعبئة الرئيسية من /store/me ───── */
  function renderHome(data) {
    var w = data.wallet || {};
    $('wBalance').textContent = w.balance || '0.00';
    $('wCurrency').textContent = w.currency || '';
    $('wName').textContent = (data.card_user || {}).display_name || '';
    renderPackages(data.packages || []);
  }

  function renderPackages(items) {
    var box = $('pkgList');
    if (!items.length) {
      box.innerHTML = '<div class="empty" style="grid-column:1/-1">' +
        'لا توجد باقات متاحة حاليًا — تواصل مع المزوّد.</div>';
      return;
    }
    box.innerHTML = items.map(function (p) {
      var meta = [];
      var d = fmtDuration(p.duration_minutes); if (d) meta.push('⏱ ' + d);
      var s = fmtSpeed(p.speed_down_kbps); if (s) meta.push('🚀 ' + s);
      if (p.quota_total_mb) {
        meta.push('📶 ' + (p.quota_total_mb >= 1024
          ? (Math.round(p.quota_total_mb / 102.4) / 10) + ' جيجا'
          : p.quota_total_mb + ' ميجا'));
      }
      var out = !p.in_stock;
      /* شارة «كمية محدودة» لعروض المخزون شبه النافد — من availability_ar */
      var limited = (!out && p.availability === 'limited')
        ? '<div class="p-out" style="color:#b45309">' +
          esc(p.availability_ar || 'كمية محدودة') +
          (p.stock_remaining ? ' — متبقّي ' + p.stock_remaining : '') + '</div>'
        : '';
      return '<div class="pkg" style="--pkg-color:' + esc(p.card_color) + '">' +
        '<div class="p-name">' + esc(p.name) + '</div>' +
        '<div class="p-meta">' + (meta.join('<br>') || '&nbsp;') + '</div>' +
        '<div class="p-amt">' + esc(p.price) +
          ' <small>' + esc(p.currency) + '</small></div>' +
        '<button class="p-buy" data-id="' + p.id + '"' +
          (out ? ' disabled' : '') + '>' +
          (out ? 'نفدت الكمية' : 'شراء الآن') + '</button>' +
        (out ? '<div class="p-out">سيتوفر مخزون قريبًا</div>' : limited) +
        '</div>';
    }).join('');
    /* ربط أزرار الشراء */
    Array.prototype.forEach.call(
      box.querySelectorAll('.p-buy:not([disabled])'),
      function (btn) {
        btn.addEventListener('click', function () {
          buyPackage(parseInt(btn.getAttribute('data-id'), 10), btn);
        });
      });
  }

  /* ───── بطاقاتي: /store/my-cards (مصفّح) ─────
     كل بطاقة: شارة حالة من الراديوس (فعالة الآن / غير مستخدمة بعد /
     مستهلكة / منتهية / ملغاة)، تفاصيل قابلة للفرد، نسخ اليوزر
     والباس، وزر «دخول بهذه البطاقة». */
  var CHIP_CLS = { active: 'chip-active', unused: 'chip-unused',
                   expired: 'chip-expired', consumed: 'chip-consumed',
                   revoked: 'chip-revoked' };
  var mcState = { page: 1, pages: 1 };

  function renderMyCards(data) {
    var items = data.items || [];
    mcState.page = data.page || 1;
    mcState.pages = data.pages || 1;
    $('mcCount').textContent = (data.total || 0) + ' بطاقة';
    var box = $('mcList');
    if (!items.length) {
      box.innerHTML = '<div class="card"><div class="empty">' +
        'لا تملك بطاقات بعد — اشترِ أول بطاقة لك من تبويب المعرض.' +
        '</div></div>';
      $('mcPager').classList.add('hide');
      return;
    }
    box.innerHTML = items.map(function (c, i) {
      var meta = [];
      var d = fmtDuration(c.duration_minutes); if (d) meta.push('⏱ ' + d);
      var s = fmtSpeed(c.speed_down_kbps); if (s) meta.push('🚀 ' + s);
      var q = fmtMB(c.quota_total_mb); if (q) meta.push('📶 ' + q);
      var cells =
        cell('تاريخ الشراء', fmtWhen(c.purchased_at)) +
        cell('أول استخدام', fmtWhen(c.first_used_at)) +
        cell('تاريخ الانتهاء', fmtWhen(c.expire_at)) +
        cell('سعر الشراء', esc(c.price) + ' ' + esc(c.currency || ''));
      if (c.total_minutes) {
        cells += cell('مدة الاستخدام', c.total_minutes + ' دقيقة') +
                 cell('التنزيل', fmtBytes(c.download_bytes));
      }
      return '<div class="mc" style="--mc-color:' +
          esc(c.card_color || '') + '" data-i="' + i + '">' +
        '<div class="mc-head" data-mc-toggle="' + i + '">' +
          '<span class="mc-name">🎫 ' + esc(c.package_name) + '</span>' +
          '<span class="chip ' + (CHIP_CLS[c.state] || 'chip-unused') +
            '">' + esc(c.state_ar) + '</span>' +
          '<span class="mc-arrow">▼</span></div>' +
        '<div class="mc-sub"><span>👤 ' + esc(c.username) + '</span>' +
          '<span>🕓 ' + fmtWhen(c.purchased_at) + '</span></div>' +
        '<div class="mc-body">' +
          '<div class="mc-cred">' +
            '<span class="cr"><small>اسم المستخدم</small><b>' +
              esc(c.username) + '</b></span>' +
            '<button type="button" class="cp" data-copy="' +
              esc(c.username) + '" title="نسخ اليوزر">📋</button></div>' +
          '<div class="mc-cred">' +
            '<span class="cr"><small>كلمة المرور</small><b>' +
              esc(c.password) + '</b></span>' +
            '<button type="button" class="cp" data-copy="' +
              esc(c.password) + '" title="نسخ الباس">📋</button></div>' +
          '<div class="mc-grid">' + cells + '</div>' +
          (meta.length ? '<div class="mc-sub" style="padding:0 0 10px">' +
            meta.map(function (m) { return '<span>' + m + '</span>'; })
                .join('') + '</div>' : '') +
          '<button type="button" class="mc-login" data-login-i="' + i +
            '"' + (c.can_login ? '' : ' disabled') + '>' +
            (c.can_login ? '🔓 دخول بهذه البطاقة'
                         : 'البطاقة غير صالحة للدخول') + '</button>' +
        '</div></div>';
    }).join('');
    function cell(k, v) {
      return '<span class="mc-cell"><span class="k">' + k +
        '</span><span class="v">' + (v || '—') + '</span></span>';
    }
    /* فرد/طي التفاصيل */
    Array.prototype.forEach.call(
      box.querySelectorAll('[data-mc-toggle]'),
      function (h) {
        h.addEventListener('click', function () {
          h.parentNode.classList.toggle('open');
        });
      });
    /* نسخ اليوزر / الباس */
    Array.prototype.forEach.call(
      box.querySelectorAll('[data-copy]'),
      function (b) {
        b.addEventListener('click', function () {
          copyText(b.getAttribute('data-copy') || '');
        });
      });
    /* دخول بهذه البطاقة */
    Array.prototype.forEach.call(
      box.querySelectorAll('[data-login-i]:not([disabled])'),
      function (b) {
        b.addEventListener('click', function () {
          var c = items[parseInt(b.getAttribute('data-login-i'), 10)];
          if (c) hotspotLogin(c.username, c.password);
        });
      });
    /* الترقيم */
    var pgr = $('mcPager');
    pgr.classList.toggle('hide', mcState.pages <= 1);
    $('mcPage').textContent = mcState.page + ' / ' + mcState.pages;
    $('mcPrev').disabled = mcState.page <= 1;
    $('mcNext').disabled = mcState.page >= mcState.pages;
  }

  function loadMyCards(page) {
    loadedTabs.cards = true;
    $('mcList').innerHTML =
      '<div class="skel" style="height:64px"></div>' +
      '<div class="skel" style="height:64px"></div>';
    api('/my-cards?page=' + (page || 1) + '&per_page=10')
      .then(renderMyCards)
      .catch(function (e) {
        loadedTabs.cards = false;
        $('mcList').innerHTML = '<div class="card"><div class="empty">' +
          esc(e.message) + '</div></div>';
      });
  }

  /* ───── نسخ نص للحافظة — مع سقوط لطريقة execCommand لأن
     navigator.clipboard يتطلب سياقًا آمنًا (https) والصفحة على
     http من الراوتر. ───── */
  function copyText(text) {
    function done() { toast('تم النسخ ✓', 'ok'); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {
        legacyCopy(text); done();
      });
      return;
    }
    legacyCopy(text); done();
  }
  function legacyCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
  }

  /* ───── دخول الهوت سبوت ببطاقة ─────
     النموذج المخفي hsLogin وجهته $(link-login-only) — يملؤها
     الراوتر عند تقديم الصفحة من خادم الهوت سبوت فيتم الدخول
     مباشرة (PAP عادي، نفس ما تفعله بوابة الويب portal_card).
     إن بقي الـ placeholder حرفيًا (الصفحة فُتحت من خارج خادم
     الهوت سبوت) نسقط إلى login.html?u=..&p=.. — سكربت الدخول
     التلقائي R4 المحقون في كل صفحات الدخول المنشورة يلتقطها
     ويُرسل النموذج (وهو الآمن مع CHAP لأنه يمر عبر doLogin). */
  function hotspotLogin(username, password) {
    if (!username) return;
    var f = $('hsLogin');
    var action = f.getAttribute('action') || '';
    /* '$' + '(' مفصولة حتى لا يلتقطها محلّل متغيّرات الراوتر */
    if (!action || action.indexOf('$' + '(') !== -1) {
      location.href = 'login.html?u=' + encodeURIComponent(username) +
        '&p=' + encodeURIComponent(password || '');
      return;
    }
    f.elements['username'].value = username;
    f.elements['password'].value = password || '';
    toast('جارٍ تسجيل دخولك للشبكة…', 'ok');
    f.submit();
  }

  /* ───── السجل: /store/purchases (مصفّح) ───── */
  var hsState = { page: 1, pages: 1 };

  function renderHistory(data) {
    var items = data.items || [];
    hsState.page = data.page || 1;
    hsState.pages = data.pages || 1;
    $('hsCount').textContent = (data.total || 0) + ' عملية';
    var box = $('histList');
    if (!items.length) {
      box.innerHTML = '<div class="empty">لا توجد مشتريات بعد — ' +
        'اشترِ أول باقة لك من تبويب المعرض.</div>';
      $('hsPager').classList.add('hide');
      return;
    }
    box.innerHTML = items.map(function (h) {
      var ok = h.status === 'completed';
      var chip = h.card_revoked
        ? '<span class="chip chip-revoked">البطاقة ملغاة</span>'
        : (h.card_used
            ? '<span class="chip chip-consumed">البطاقة مستخدمة</span>'
            : '<span class="chip chip-unused">لم تُستخدم بعد</span>');
      return '<div class="h-item"><span class="h-ico">' +
          (ok ? '🎫' : '⚠️') + '</span>' +
        '<span class="h-txt"><b>' + esc(h.package_name) + '</b>' +
        '<span>' + fmtWhen(h.created_at) +
          (h.card_username ? ' · ' + esc(h.card_username) : '') +
          '</span>' +
        '<span style="margin-top:3px;display:inline-block">' +
          (ok ? chip : '<span class="chip chip-expired">' +
            esc(h.status_ar) + '</span>') + '</span></span>' +
        '<span class="h-amt">' + esc(h.amount) +
          '<small>' + esc(h.currency || '') + '</small></span></div>';
    }).join('');
    var pgr = $('hsPager');
    pgr.classList.toggle('hide', hsState.pages <= 1);
    $('hsPage').textContent = hsState.page + ' / ' + hsState.pages;
    $('hsPrev').disabled = hsState.page <= 1;
    $('hsNext').disabled = hsState.page >= hsState.pages;
  }

  function loadHistory(page) {
    loadedTabs.hist = true;
    $('histList').innerHTML = '<div class="skel" style="height:54px"></div>';
    api('/purchases?page=' + (page || 1) + '&per_page=15')
      .then(renderHistory)
      .catch(function (e) {
        loadedTabs.hist = false;
        $('histList').innerHTML = '<div class="empty">' +
          esc(e.message) + '</div>';
      });
  }

  /* ───── تحميل / تحديث بيانات الرئيسية ─────
     silent=true: لا توست عند الفشل لكن الخطأ يُمرَّر للمستدعي
     (إقلاع الصفحة يحتاجه ليعود لشاشة الدخول). التحديث يُبطل
     ذاكرة تبويبي بطاقاتي والسجل فيُعاد تحميلهما عند فتحهما. */
  function loadHome(silent) {
    return api('/me').then(function (data) {
      renderHome(data);
      loadMyRequests();  // طلبات الإيداع/السحب بحالتها في تبويب رصيدي
      loadedTabs = {};
      if (!$('tabCards').classList.contains('hide')) loadMyCards(1);
      if (!$('tabHistory').classList.contains('hide')) loadHistory(1);
      show('home');
    }).catch(function (e) {
      if (!silent) toast(e.message, 'bad');
      throw e;
    });
  }

  /* ───── تسجيل الدخول ───── */
  function doLogin() {
    var mobile = $('inMobile').value.trim();
    var pass = $('inPass').value;
    var errBox = $('loginErr');
    errBox.style.display = 'none';
    if (!mobile || !pass) {
      errBox.textContent = 'أدخل رقم الجوال وكلمة المرور أولًا.';
      errBox.style.display = 'block';
      return;
    }
    var btn = $('btnLogin');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> جارٍ الدخول…';
    api('/login', { method: 'POST', body: { mobile: mobile, password: pass } })
      .then(function (data) {
        sessionStorage.setItem(TKEY, data.token || '');
        $('inPass').value = '';
        toast('أهلًا ' + ((data.card_user || {}).display_name || '') +
              ' — تم تسجيل الدخول', 'ok');
        return loadHome();
      })
      .catch(function (e) {
        errBox.textContent = e.message;
        errBox.style.display = 'block';
      })
      .then(function () {
        btn.disabled = false;
        btn.textContent = 'تسجيل الدخول';
      });
  }

  /* ───── التسجيل الذاتي ─────
     ينشئ حسابًا فعّالًا فورًا عبر /register ثم يدخل تلقائيًا بالتوكن
     المُعاد (نفس مسار doLogin بعد الحصول على التوكن). */
  function doRegister() {
    var name = $('rgName').value.trim();
    var mobile = $('rgMobile').value.trim();
    var pass = $('rgPass').value;
    var errBox = $('regErr');
    errBox.style.display = 'none';
    if (!name || !mobile || !pass) {
      errBox.textContent = 'أدخل الاسم الثلاثي ورقم الجوال وكلمة المرور.';
      errBox.style.display = 'block';
      return;
    }
    var btn = $('btnRegister');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> جارٍ إنشاء الحساب…';
    api('/register', { method: 'POST',
                       body: { display_name: name, mobile: mobile,
                               password: pass } })
      .then(function (data) {
        sessionStorage.setItem(TKEY, data.token || '');
        $('rgPass').value = '';
        toast('تم إنشاء حسابك بنجاح — أهلًا بك 🎉', 'ok');
        return loadHome();
      })
      .catch(function (e) {
        errBox.textContent = e.message;
        errBox.style.display = 'block';
      })
      .then(function () {
        btn.disabled = false;
        btn.textContent = 'إنشاء الحساب';
      });
  }

  function doLogout(silent) {
    sessionStorage.removeItem(TKEY);
    loadedTabs = {};
    openTab('tabWallet');
    show('login');
    if (!silent) toast('تم تسجيل الخروج.');
    else toast('انتهت الجلسة — سجّل الدخول من جديد.', 'bad');
  }

  /* ───── شحن بطاقة ───── */
  function doRedeem() {
    var no = $('inCardNo').value.trim();
    var pin = $('inCardPin').value.trim();
    if (!no) { toast('أدخل رقم البطاقة أولًا.', 'bad'); return; }
    var btn = $('btnRedeem');
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> جارٍ الشحن…';
    api('/redeem', { method: 'POST',
                     body: { card_number: no, card_password: pin } })
      .then(function (data) {
        $('inCardNo').value = ''; $('inCardPin').value = '';
        var w = data.wallet || {};
        $('wBalance').textContent = w.balance || $('wBalance').textContent;
        toast('تم شحن ' + (data.amount || '') + ' لمحفظتك بنجاح 🎉', 'ok');
        loadHome(true).catch(function () {}); /* تحديث السجل بصمت */
      })
      .catch(function (e) { toast(e.message, 'bad'); })
      .then(function () {
        btn.disabled = false;
        btn.innerHTML = '⚡ شحن الرصيد';
      });
  }

  /* ───── شراء باقة ───── */
  function buyPackage(id, btn) {
    if (!id) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>…';
    api('/purchase', { method: 'POST', body: { package_id: id } })
      .then(function (data) {
        var w = data.wallet || {};
        $('wBalance').textContent = w.balance || $('wBalance').textContent;
        var c = data.card || {};
        $('mUser').textContent = c.username || '—';
        $('mPass').textContent = c.password || '—';
        $('buyModal').classList.add('show');
        loadHome(true).catch(function () {}); /* تحديث الرصيد والمخزون
          بصمت — ويُبطل ذاكرة بطاقاتي والسجل ليظهر الكرت الجديد */
      })
      .catch(function (e) { toast(e.message, 'bad'); })
      .then(function () {
        btn.disabled = false;
        btn.textContent = 'شراء الآن';
      });
  }

  /* ═════════ المتجر المتقدّم: إيداع / سحب / شات ═════════ */

  function openSheet(id) { $(id).classList.add('show'); }
  function closeSheet(id) { $(id).classList.remove('show'); }

  /* ── طلباتي (إيداع + سحب مدموجين زمنيًا) ── */
  function loadMyRequests() {
    Promise.all([
      api('/deposits').catch(function () { return { items: [] }; }),
      api('/withdrawals').catch(function () { return { items: [] }; })
    ]).then(function (res) {
      var deps = (res[0].items || []).map(function (d) { d._k = 'dep'; return d; });
      var wds = (res[1].items || []).map(function (w) { w._k = 'wd'; return w; });
      var all = deps.concat(wds).sort(function (a, b) {
        return String(b.created_at).localeCompare(String(a.created_at));
      });
      renderRequests(all);
    });
  }
  function renderRequests(items) {
    var box = $('reqList');
    $('reqCount').textContent = items.length ? items.length + ' طلب' : '';
    if (!items.length) {
      box.innerHTML = '<div class="empty">لا توجد طلبات بعد.</div>';
      return;
    }
    box.innerHTML = items.map(function (r) {
      var dep = r._k === 'dep';
      var amt = dep ? (r.confirmed_amount || r.amount_claimed) : r.amount;
      return '<div class="rq"><span class="rq-ico ' + (dep ? 'dep' : '') +
        '">' + (dep ? '💰' : '💸') + '</span>' +
        '<span class="rq-txt"><b>' + (dep ? 'شحن بتحويل' : 'سحب رصيد') +
          ' · ' + esc(r.status_ar || '') + '</b>' +
          '<span>' + fmtWhen(r.created_at) + '</span></span>' +
        '<span class="rq-amt">' + esc(amt || '') +
          ' <small>' + esc(r.currency || '') + '</small></span></div>';
    }).join('');
  }

  /* ── الإيداع: محافظ الاستلام + الإرسال ── */
  /* القنوات تُعرض كبطاقات قابلة للاختيار؛ اختيار بطاقة يُبرز رقم
     التحويل + زر نسخ + QR + التعليمات. «قناة أخرى» احتياطية دائمًا. */
  var payMethodsCache = [];
  var depSel = { id: '', method: '' };  // القناة المختارة حاليًا

  function openDeposit() { openSheet('depositSheet'); loadPaymentMethods(); }
  function loadPaymentMethods() {
    var box = $('payMethods');
    clearMethodSelection();
    api('/payment-methods').then(function (data) {
      payMethodsCache = data.items || [];
      box.innerHTML = '';
      payMethodsCache.forEach(function (m) { box.appendChild(methodCard(m)); });
      // بطاقة احتياطية «قناة أخرى» — دائمًا في النهاية.
      box.appendChild(methodCard(null));
    }).catch(function (e) {
      box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
    });
  }
  function methodCard(m) {
    var el = document.createElement('button');
    el.type = 'button';
    el.className = 'pmcard';
    el.setAttribute('data-id', m ? m.id : '');
    var inner;
    if (m) {
      inner = (m.logo_image_url
        ? '<img class="pmc-logo" src="' + esc(API + m.logo_image_url) + '" alt="">'
        : '<span class="pmc-ico">🏦</span>') +
        '<span class="pmc-name">' + esc(m.label || m.method_ar || '') + '</span>';
    } else {
      inner = '<span class="pmc-ico">➕</span>' +
        '<span class="pmc-name">قناة أخرى</span>';
    }
    el.innerHTML = inner + '<span class="pmc-tick">✓</span>';
    el.addEventListener('click', function () {
      selectMethod(m ? String(m.id) : '');
    });
    return el;
  }
  function selectMethod(id) {
    Array.prototype.forEach.call(
      $('payMethods').querySelectorAll('.pmcard'), function (c) {
        c.classList.toggle('sel', c.getAttribute('data-id') === String(id));
      });
    var detail = $('paySelected');
    if (!id) {  // قناة أخرى (احتياطية)
      depSel = { id: '', method: 'other' };
      detail.className = 'pay-selected';
      detail.innerHTML = '<div class="pm-hint">حوّل عبر القناة المتّفق ' +
        'عليها مع المزوّد ثم أكمل البيانات أدناه.</div>';
      return;
    }
    var m = null;
    payMethodsCache.forEach(function (x) {
      if (String(x.id) === String(id)) m = x;
    });
    if (!m) return;
    depSel = { id: m.id, method: m.method || 'other' };
    var qr = m.qr_image_url ? '<img class="pm-qr" src="' +
      esc(API + m.qr_image_url) + '" alt="QR">' : '';
    var hint = m.instructions ? '<div class="pm-hint">' +
      esc(m.instructions) + '</div>' : '';
    var name = m.account_name ? '<div class="pm-name">صاحب الحساب: ' +
      esc(m.account_name) + '</div>' : '';
    detail.className = 'pay-selected';
    detail.innerHTML = '<div class="ps-label">حوّل إلى هذا الرقم:</div>' +
      '<div class="pm-num"><b>' + esc(m.account_number || '—') + '</b>' +
      '<button type="button" class="cp" id="psCopy">📋 نسخ</button></div>' +
      name + qr + hint;
    var cp = detail.querySelector('#psCopy');
    if (cp) cp.addEventListener('click', function () {
      copyText(m.account_number || '');
    });
  }
  function clearMethodSelection() {
    depSel = { id: '', method: '' };
    var detail = $('paySelected');
    detail.className = 'pay-selected hide';
    detail.innerHTML = '';
    Array.prototype.forEach.call(
      $('payMethods').querySelectorAll('.pmcard'),
      function (c) { c.classList.remove('sel'); });
  }
  function resetDeposit() {
    ['depAmount', 'depPhone', 'depRef', 'depPayer'].forEach(
      function (id) { $(id).value = ''; });
    $('depReceipt').value = '';
    $('depFileName').textContent = 'اضغط لإرفاق صورة الوصل';
    $('depFileRow').classList.remove('has');
    clearMethodSelection();
  }
  function submitDeposit() {
    var err = $('depErr'); err.style.display = 'none';
    var amount = $('depAmount').value.trim();
    if (!depSel.method) {
      err.textContent = 'اختر قناة الدفع التي حوّلت إليها أولًا.';
      err.style.display = 'block'; return;
    }
    if (!amount || parseFloat(amount) <= 0) {
      err.textContent = 'أدخل المبلغ المحوَّل.'; err.style.display = 'block';
      return;
    }
    var fd = new FormData();
    fd.append('amount_claimed', amount);
    fd.append('method', depSel.method || 'other');
    if (depSel.id) fd.append('payment_method_id', depSel.id);
    fd.append('payer_phone', $('depPhone').value.trim());
    fd.append('reference', $('depRef').value.trim());
    fd.append('payer_name', $('depPayer').value.trim());
    var file = $('depReceipt').files[0];
    if (file) fd.append('receipt', file);
    var btn = $('btnDepositSubmit'); btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> جارٍ الإرسال…';
    apiForm('/deposits', fd).then(function () {
      closeSheet('depositSheet');
      toast('تم إرسال طلب الشحن — بانتظار تأكيد المزوّد.', 'ok');
      resetDeposit(); loadMyRequests();
    }).catch(function (e) {
      err.textContent = e.message; err.style.display = 'block';
    }).then(function () {
      btn.disabled = false; btn.textContent = 'إرسال طلب الشحن';
    });
  }

  /* ── السحب ── */
  function openWithdraw() {
    $('wdErr').style.display = 'none';
    $('wdBalance').textContent = ($('wBalance').textContent || '—') + ' ' +
      ($('wCurrency').textContent || '');
    openSheet('withdrawSheet');
  }
  function submitWithdrawal() {
    var err = $('wdErr'); err.style.display = 'none';
    var amount = $('wdAmount').value.trim();
    var name = $('wdName').value.trim(), acc = $('wdAccount').value.trim();
    if (!amount || parseFloat(amount) <= 0) {
      err.textContent = 'أدخل المبلغ المطلوب سحبه.'; err.style.display = 'block';
      return;
    }
    if (!name || !acc) {
      err.textContent = 'أدخل اسم صاحب الحساب ورقم الحساب.';
      err.style.display = 'block'; return;
    }
    var btn = $('btnWithdrawSubmit'); btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span> جارٍ الإرسال…';
    api('/withdrawals', { method: 'POST', body: {
      amount: amount, payee_name: name, payee_account: acc } })
      .then(function () {
        closeSheet('withdrawSheet');
        toast('تم إرسال طلب السحب — بانتظار تنفيذ المزوّد.', 'ok');
        $('wdAmount').value = ''; $('wdName').value = ''; $('wdAccount').value = '';
        loadMyRequests();
      }).catch(function (e) {
        err.textContent = e.message; err.style.display = 'block';
      }).then(function () {
        btn.disabled = false; btn.textContent = 'إرسال طلب السحب';
      });
  }

  /* ── شات الدعم (استطلاع خفيف) ── */
  var chatLastId = 0, chatPollTimer = null;
  function openChat() {
    openSheet('chatSheet'); setupWhatsapp();
    chatLastId = 0; $('chatBody').innerHTML = '';
    loadChat(true);
    if (chatPollTimer) clearInterval(chatPollTimer);
    chatPollTimer = setInterval(function () { loadChat(false); }, 5000);
  }
  function closeChat() {
    closeSheet('chatSheet');
    if (chatPollTimer) { clearInterval(chatPollTimer); chatPollTimer = null; }
  }
  function loadChat(first) {
    api('/chat?after_id=' + chatLastId).then(function (data) {
      var items = data.items || [];
      if (items.length) {
        chatLastId = data.last_id || chatLastId;
        appendChat(items, first);
      } else if (first) {
        $('chatBody').innerHTML =
          '<div class="empty">ابدأ المحادثة — نحن هنا للمساعدة.</div>';
      }
    }).catch(function () {});
  }
  function appendChat(items, first) {
    var body = $('chatBody');
    if (first) body.innerHTML = '';
    var em = body.querySelector('.empty'); if (em) em.parentNode.removeChild(em);
    items.forEach(function (m) {
      var me = m.sender === 'customer';
      var div = document.createElement('div');
      div.className = 'msg ' + (me ? 'me' : 'them');
      var html = m.body ? esc(m.body) : '';
      if (m.image_url) html += '<img src="' + esc(API + m.image_url) + '" alt="">';
      html += '<small>' + fmtWhen(m.created_at) + '</small>';
      div.innerHTML = html;
      body.appendChild(div);
    });
    body.scrollTop = body.scrollHeight;
  }
  function sendChat() {
    var txt = $('chatText').value.trim();
    var file = $('chatImage').files[0];
    if (!txt && !file) return;
    var fd = new FormData();
    if (txt) fd.append('body', txt);
    if (file) fd.append('image', file);
    $('chatText').value = ''; $('chatImage').value = '';
    apiForm('/chat', fd).then(function (data) {
      if (data.message) {
        appendChat([data.message], false);
        chatLastId = Math.max(chatLastId, data.message.id || 0);
      }
    }).catch(function (e) { toast(e.message, 'bad'); });
  }
  function setupWhatsapp() {
    var btn = $('btnWhatsapp');
    /* '$'+'{' لا يلتقطها محلّل القوالب — وWA المحقون أرقام فقط. */
    if (!WA || WA.indexOf('{' + '{') !== -1) { btn.style.display = 'none'; return; }
    btn.style.display = 'flex';
    btn.onclick = function () {
      var name = $('wName').textContent || '';
      var msg = encodeURIComponent('مرحبًا، أنا ' + name +
        ' من متجر البطاقات وأحتاج مساعدة.');
      window.open('https://wa.me/' + WA + '?text=' + msg, '_blank');
    };
  }

  /* ───── ربط الأحداث ───── */
  $('btnLogin').addEventListener('click', doLogin);
  $('inPass').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') doLogin();
  });
  /* تبديل شاشتي الدخول/التسجيل */
  $('goRegister').addEventListener('click', function (e) {
    e.preventDefault(); $('regErr').style.display = 'none'; show('register');
  });
  $('goLogin').addEventListener('click', function (e) {
    e.preventDefault(); $('loginErr').style.display = 'none'; show('login');
  });
  $('btnRegister').addEventListener('click', doRegister);
  $('rgPass').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') doRegister();
  });
  $('btnLogout').addEventListener('click', function () { doLogout(false); });
  $('btnRefresh').addEventListener('click', function () {
    toast('جارٍ التحديث…');
    loadHome().catch(function () {}); /* التوست عُرض داخل loadHome */
  });
  $('btnRedeem').addEventListener('click', doRedeem);
  $('btnModalClose').addEventListener('click', function () {
    $('buyModal').classList.remove('show');
  });
  $('btnModalLogin').addEventListener('click', function () {
    var u = $('mUser').textContent, p = $('mPass').textContent;
    if (u && u !== '—') hotspotLogin(u, p === '—' ? '' : p);
  });
  /* المتجر المتقدّم: إيداع / سحب / شات */
  $('btnOpenDeposit').addEventListener('click', openDeposit);
  $('btnOpenWithdraw').addEventListener('click', openWithdraw);
  $('btnDepositSubmit').addEventListener('click', submitDeposit);
  $('btnWithdrawSubmit').addEventListener('click', submitWithdrawal);
  $('depReceipt').addEventListener('change', function () {
    var f = this.files[0];
    $('depFileName').textContent = f ? ('✓ ' + f.name) : 'اضغط لإرفاق صورة الوصل';
    $('depFileRow').classList.toggle('has', !!f);
  });
  Array.prototype.forEach.call(
    document.querySelectorAll('[data-close-sheet]'), function (b) {
      b.addEventListener('click', function () {
        var id = b.getAttribute('data-close-sheet');
        if (id === 'chatSheet') closeChat(); else closeSheet(id);
      });
    });
  $('fabChat').addEventListener('click', openChat);
  $('btnChatSend').addEventListener('click', sendChat);
  $('chatText').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') sendChat();
  });
  $('btnChatAttach').addEventListener('click', function () { $('chatImage').click(); });
  $('chatImage').addEventListener('change', function () {
    if (this.files[0]) sendChat();
  });
  /* شريط التبويبات */
  Array.prototype.forEach.call(
    $('tabBar').querySelectorAll('button'),
    function (b) {
      b.addEventListener('click', function () {
        openTab(b.getAttribute('data-tab'));
      });
    });
  /* ترقيم بطاقاتي والسجل */
  $('mcPrev').addEventListener('click', function () {
    if (mcState.page > 1) loadMyCards(mcState.page - 1);
  });
  $('mcNext').addEventListener('click', function () {
    if (mcState.page < mcState.pages) loadMyCards(mcState.page + 1);
  });
  $('hsPrev').addEventListener('click', function () {
    if (hsState.page > 1) loadHistory(hsState.page - 1);
  });
  $('hsNext').addEventListener('click', function () {
    if (hsState.page < hsState.pages) loadHistory(hsState.page + 1);
  });

  /* ───── الإقلاع: فحص ذاتي للاتصال ثم منطق الجلسة ─────
     selfCheck() يعرض حالة الاتصال فورًا (يفحص/متصل/سبب الفشل) قبل
     أي محاولة دخول؛ يجري بالتوازي ولا يعطّل الواجهة. ثم: عنوان غير
     صالح شكليًا → ابقَ على شاشة الدخول (شريط الحالة يبيّن السبب)؛
     جلسة محفوظة وعنوان صالح → الرئيسية؛ وإلا شاشة الدخول. */
  selfCheck();
  if (apiUnusable()) {
    show('login');
  } else if (sessionStorage.getItem(TKEY)) {
    show('home');
    loadHome(true).catch(function () { show('login'); });
  } else {
    show('login');
  }
})();
</script>
</body>
</html>"""


__all__ = [
    "STORE_FILE_NAME",
    "DEFAULT_STORE_PATH",
    "STORE_PAGE_HTML",
    "StorePageError",
    "StoreDeployResult",
    "render_store_page",
    "normalize_api_base",
    "deploy_store",
    "API_BASE_LOOPBACK_MSG",
    "api_base_unusable",
    "WALLED_GARDEN_COMMENT",
    "WalledGardenResult",
    "walled_garden_ports",
    "walled_garden_command",
    "ensure_walled_garden",
]
