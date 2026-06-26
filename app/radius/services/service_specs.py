"""service_specs — مخطّط مواصفات الخدمات (نظام موحّد).

التصميم
========
عبر الـRadius module تتنوّع نقاط «تفعيل/ترقية/طلب الخدمة» (راجع
الجرد في feat/radius-service-spec-modal): خدمات سكربت المنافذ
(bt_wifi_block, loop_detect)، خدمات الباندويذث (hotspot, broadband)،
خدمات السياسة (block-sites, open-sites)، خدمات النفق (public-ip,
remote-access)، خدمات الكوتا المدفوعة، خطط المشتركين، …

كل خدمة كانت تَلتقط حقولها بشكل مخصّص: زرّ، نموذج، حقل نصّي،
checkbox، JS-only. النتيجة: لا تجربة موحّدة، ولا «طلب التفعيل»
يحمل المواصفات الفعليّة المطلوبة، ولا يمكن استخراج تقرير «من
طلب ماذا».

هذه الوحدة تُرسي:
  1) سجلّ مركزيّ لـ«نوع المواصفات» (SpecKind) — مجموعة حقول
     مشتركة بين خدمات متشابهة.
  2) خريطة (service_type → SpecKind) — كل خدمة معروفة تنتمي
     لنوع مواصفات واحد.
  3) دالّة validate_spec() تتحقّق من نموذج مرسَل من نافذة الحوار
     مقابل المخطّط، فتُعيد المواصفات النظيفة + قائمة أخطاء عربيّة
     (للعرض في النافذة بنفس النصّ).

تستخدمها:
  • نقطة النهاية الموحّدة /service-requests (تُحفَظ المواصفات في
    tenant_settings + audit).
  • Jinja macro service_spec_modal (يبني الحقول من المخطّط).
  • نقطة نهاية المخطّط /service-requests/schema/<service_type>
    تُعيد المخطّط كـJSON للـJS فيرسم النموذج ديناميكيًّا.

الإضافة بسيطة (لا تُغيّر شيئًا آخر):
  • أضِف SpecKind جديد لو احتجت حقولاً جديدة (مثلاً «نظام نسخ
    احتياطي مجدول» = حقول schedule_cron + retention).
  • أضِف إدخالًا في SERVICE_TYPE_MAP لربط خدمة بنوع موجود.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ─── أنواع الحقول المدعومة في نافذة المواصفات ────────────────────
#
# يجب أن تبقى محدودة ومحدّدة: النافذة العامّة ترسمها بنفس الـCSS،
# والمنطق الخادمي يُجبر التحقّق منها. أيّ نوع غير معروف يُرفَض من
# validate_spec بصمت (تُعرض رسالة «نوع حقل غير معروف»).
ALLOWED_FIELD_TYPES = frozenset({
    "number", "text", "textarea", "select", "checkbox",
})


@dataclass(frozen=True)
class SpecField:
    """تعريف حقل واحد في نموذج مواصفات الخدمة.

    key          مفتاح JSON المُرسَل في الـpayload (snake_case ASCII).
    label        تسمية الحقل بالعربيّة في النافذة.
    type         نوع الحقل (راجع ALLOWED_FIELD_TYPES).
    required     هل الحقل مطلوب؟ إن نعم، يَفشل التحقّق عند فراغه.
    placeholder  نصّ توجيهي اختياري داخل الحقل.
    help_text    سطر شرح صغير أسفل الحقل.
    min, max     حدّان رقميّان (للحقل number فقط).
    max_length   حدّ طول النصّ (text/textarea فقط).
    options      قائمة الخيارات لـselect: [{value, label}, …].
    default      قيمة افتراضيّة (تظهر في «التفعيل» الأوّل).
    """
    key: str
    label: str
    type: str = "text"
    required: bool = False
    placeholder: str = ""
    help_text: str = ""
    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    options: tuple[dict, ...] = field(default_factory=tuple)
    default: Any = None

    def to_dict(self) -> dict[str, Any]:
        """يُحوّل الحقل إلى JSON قابل للإرسال للواجهة (الـJS يرسم منه
        النموذج). نُسقط القيم None ليبقى الـpayload نظيفًا."""
        d: dict[str, Any] = {"key": self.key, "label": self.label,
                              "type": self.type, "required": self.required}
        if self.placeholder:  d["placeholder"]  = self.placeholder
        if self.help_text:    d["help_text"]    = self.help_text
        if self.min is not None:        d["min"]        = self.min
        if self.max is not None:        d["max"]        = self.max
        if self.max_length is not None: d["max_length"] = self.max_length
        if self.options:               d["options"]   = list(self.options)
        if self.default is not None:   d["default"]   = self.default
        return d


@dataclass(frozen=True)
class SpecKind:
    """نوع مواصفات — مجموعة حقول مشتركة بين خدمات متشابهة.

    key      معرّف ASCII (snake_case) — هوية النوع.
    title    عنوان عربي يظهر في رأس النافذة.
    summary  جملة شرح موجزة تحت العنوان (ما الذي تُفعّله/تُرقّيه؟).
    fields   ترتيب الحقول كما تُعرض في النافذة.
    """
    key: str
    title: str
    summary: str
    fields: tuple[SpecField, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "summary": self.summary,
            "fields": [f.to_dict() for f in self.fields],
        }


# ─── الأنواع المُسجَّلة ──────────────────────────────────────────
#
# كل نوع يُغطّي عائلة خدمات. أضِف نوعًا جديدًا فقط لو احتجت حقولاً
# مختلفة جوهريًّا — حاول إعادة استخدام الموجود.


_KIND_BANDWIDTH = SpecKind(
    key="bandwidth_plan",
    title="مواصفات خدمة باندويذث",
    summary=(
        "حدّد السرعتَين (تنزيل/رفع)، الكوتا الشهريّة إن وُجدت، "
        "ومدّة الصلاحية. تُستخدم لخطط الهوت سبوت والبرودباند."
    ),
    fields=(
        SpecField(key="download_mbps", label="سرعة التنزيل (Mbps)",
                  type="number", required=True, min=1, max=10000,
                  placeholder="مثل: 25"),
        SpecField(key="upload_mbps", label="سرعة الرفع (Mbps)",
                  type="number", required=True, min=1, max=10000,
                  placeholder="مثل: 5"),
        SpecField(key="quota_gb", label="الكوتا الشهريّة (GB)",
                  type="number", required=False, min=0, max=1_048_576,
                  placeholder="اتركها فارغة لخطّة لامحدودة",
                  help_text="0 أو فارغ = بلا كوتا"),
        SpecField(key="validity_days", label="مدّة الصلاحية (أيام)",
                  type="number", required=True, min=1, max=3650,
                  default=30),
        SpecField(key="notes", label="ملاحظات", type="textarea",
                  required=False, max_length=1000,
                  placeholder="سبب الطلب أو متطلّبات إضافيّة…"),
    ),
)


_KIND_TUNNEL = SpecKind(
    key="tunnel",
    title="مواصفات خدمة نفق / IP عمومي",
    summary=(
        "حدّد المنافذ المطلوبة، الحدّ الأقصى للجلسات المتزامنة، "
        "وهل يحتاج IP ثابتًا. تُستخدم لتغيير IP الخروج والوصول البعيد."
    ),
    fields=(
        SpecField(key="ports", label="المنافذ المطلوبة (مفصولة بفواصل)",
                  type="text", required=True, max_length=200,
                  placeholder="مثل: 80,443,1194",
                  help_text="أرقام المنافذ TCP/UDP فقط (1-65535)."),
        SpecField(key="protocol", label="البروتوكول",
                  type="select", required=True,
                  options=(
                      {"value": "tcp",    "label": "TCP"},
                      {"value": "udp",    "label": "UDP"},
                      {"value": "both",   "label": "TCP + UDP"},
                  ),
                  default="tcp"),
        SpecField(key="concurrent_sessions", label="حدّ الجلسات المتزامنة",
                  type="number", required=False, min=1, max=10000,
                  placeholder="اتركها فارغة لاستخدام الافتراضي"),
        SpecField(key="static_ip", label="يحتاج IP ثابتًا",
                  type="checkbox", required=False, default=False),
        SpecField(key="notes", label="ملاحظات", type="textarea",
                  required=False, max_length=1000),
    ),
)


_KIND_PORT_SCRIPT = SpecKind(
    key="port_script",
    title="مواصفات خدمة مبنيّة على المنافذ",
    summary=(
        "اختر منافذ الراوتر التي تنطبق عليها الخدمة (LAN فقط)؛ "
        "تُستخدم لمنع البث وتتبّع اللوب."
    ),
    fields=(
        SpecField(key="ports", label="منافذ الراوتر (مفصولة بفواصل)",
                  type="text", required=True, max_length=400,
                  placeholder="مثل: ether2,ether3,ether4",
                  help_text="LAN فقط — WAN والأنفاق مرفوضة."),
        SpecField(key="apply_immediately", label="تطبيق فوريّ بعد الموافقة",
                  type="checkbox", required=False, default=True),
        SpecField(key="notes", label="ملاحظات", type="textarea",
                  required=False, max_length=1000),
    ),
)


_KIND_QUOTA = SpecKind(
    key="quota",
    title="مواصفات خدمة بكوتا",
    summary=(
        "حدّد الكمّيّة المطلوبة (ميغابايت) ومدّة الصلاحية ورسالة "
        "موجزة. تُستخدم للخدمات المدفوعة المحدودة بحجم."
    ),
    fields=(
        SpecField(key="quota_mb", label="الكمّيّة المطلوبة (ميغابايت)",
                  type="number", required=True, min=1, max=1_048_576,
                  placeholder="مثل: 2048"),
        SpecField(key="validity_days", label="مدّة الصلاحية (أيام)",
                  type="number", required=False, min=1, max=365,
                  default=30,
                  help_text="افتراضيًّا 30 يومًا."),
        SpecField(key="notes", label="ملاحظات (سبب الطلب)",
                  type="textarea", required=False, max_length=1000),
    ),
)


_KIND_SITE_POLICY = SpecKind(
    key="site_policy",
    title="مواصفات سياسة مواقع",
    summary=(
        "اكتب المواقع (سطر لكل واحد)، وحدّد نطاق التطبيق "
        "(جميع المستخدمين / الهوت سبوت / البرودباند)."
    ),
    fields=(
        SpecField(key="sites", label="المواقع (سطر لكل موقع)",
                  type="textarea", required=True, max_length=8000,
                  placeholder="example.com\nyoutube.com\n*.facebook.com"),
        SpecField(key="scope", label="نطاق التطبيق",
                  type="select", required=True,
                  options=(
                      {"value": "all",       "label": "جميع المستخدمين"},
                      {"value": "hotspot",   "label": "الهوت سبوت فقط"},
                      {"value": "broadband", "label": "البرودباند فقط"},
                  ),
                  default="all"),
        SpecField(key="match_subdomains",
                  label="مطابقة النطاقات الفرعيّة تلقائيًّا",
                  type="checkbox", required=False, default=True),
        SpecField(key="notes", label="ملاحظات", type="textarea",
                  required=False, max_length=1000),
    ),
)


_KIND_IP_CHANGE = SpecKind(
    key="ip_change",
    title="مواصفات خدمة تغيير الـIP",
    summary=(
        "حدّد السرعة المطلوبة بالميغابِت/الثانية (Mbps). الاشتراك شهريّ "
        "متجدّد والبيانات غير محدودة — الشراء للسرعة (rate-limit) لا للكمّيّة، "
        "والسعر لكلّ ميغا من السرعة."
    ),
    fields=(
        SpecField(key="requested_speed_mbps", label="السرعة المطلوبة (Mbps)",
                  type="number", required=True, min=1, max=10000,
                  placeholder="مثل: 100",
                  help_text="عدد الميغابِت في الثانية — السعر يُحسب لكلّ ميغا."),
        # دورة الفوترة وحدّ البيانات ثابتتان لهذه الخدمة (شهريّ/غير محدودة)؛
        # نُمرّرهما كحقلين بخيار وحيد كي يَحملهما الطلب عبر validate_spec
        # (العقد يُلزم أن يَحمل الطلب billing=monthly و data=unlimited).
        SpecField(key="billing_cycle", label="دورة الفوترة",
                  type="select", required=True,
                  options=({"value": "monthly", "label": "شهريّ متجدّد"},),
                  default="monthly",
                  help_text="اشتراك شهريّ متجدّد."),
        SpecField(key="data_limit", label="حدّ البيانات",
                  type="select", required=True,
                  options=({"value": "unlimited", "label": "غير محدودة"},),
                  default="unlimited",
                  help_text="الكمّيّة مفتوحة — الشراء للسرعة لا للكمّيّة."),
        SpecField(key="notes", label="ملاحظات", type="textarea",
                  required=False, max_length=1000,
                  placeholder="أيّ تفاصيل إضافيّة عن الطلب…"),
    ),
)


_REGISTRY: dict[str, SpecKind] = {
    k.key: k for k in (
        _KIND_BANDWIDTH, _KIND_TUNNEL, _KIND_PORT_SCRIPT,
        _KIND_QUOTA, _KIND_SITE_POLICY, _KIND_IP_CHANGE,
    )
}


# ─── خريطة (service_type → SpecKind) ────────────────────────────
#
# الـservice_type هو ما تُرسله الواجهة (مثل data-svc-type="public-ip").
# هذه الخريطة هي «العقد» الوحيد بين الواجهة والخادم — أيّ خدمة
# جديدة لها صفّ هنا.

SERVICE_TYPE_MAP: dict[str, str] = {
    # خدمات سكربت المنافذ (port-script).
    "bt_wifi_block": "port_script",
    "loop_detect":   "port_script",
    "port_script":   "port_script",
    # خدمات الباندويذث.
    "hotspot":       "bandwidth_plan",
    "broadband":     "bandwidth_plan",
    "bandwidth_plan": "bandwidth_plan",
    "subscriber_plan_upgrade": "bandwidth_plan",
    # خدمات النفق/الـIP.
    "public-ip":     "quota",   # مدفوعة بكوتا — نموذج المالك
    "public_ip":     "quota",
    "quota":         "quota",
    "remote-access": "tunnel",
    "remote_access": "tunnel",
    "vpn_tunnel":    "tunnel",
    "tunnel":        "tunnel",
    # خدمة «تغيير الـIP» المدفوعة (سرعة بالـMbps، شهريّ، بيانات غير محدودة).
    "ip_change":     "ip_change",
    "ipchange":      "ip_change",
    "ip-change":     "ip_change",
    # سياسات المواقع.
    "block-sites":   "site_policy",
    "block_sites":   "site_policy",
    "open-sites":    "site_policy",
    "open_sites":    "site_policy",
    "site_policy":   "site_policy",
}


# ─── العناوين الواجهيّة للخدمة ──────────────────────────────────
#
# تظهر في رأس النافذة + رسالة الـtoast بعد الإرسال. لا تتأثّر بنوع
# المواصفات؛ فقط نصّ مقروء للمشغّل.

SERVICE_LABELS: dict[str, str] = {
    "bt_wifi_block":  "منع بث البلوتوث والواي فاي",
    "loop_detect":    "تتبّع اللوب",
    "hotspot":        "خطّة هوت سبوت",
    "broadband":      "خطّة برودباند",
    "subscriber_plan_upgrade": "ترقية خطّة المشترك",
    "public-ip":      "تغيير عنوان التصفح العام (Public)",
    "public_ip":      "تغيير عنوان التصفح العام (Public)",
    "remote-access":  "الوصول البعيد",
    "remote_access":  "الوصول البعيد",
    "vpn_tunnel":     "نفق VPN",
    "ip_change":      "تغيير عنوان التصفح العام (Public)",
    "ipchange":       "تغيير عنوان التصفح العام (Public)",
    "ip-change":      "تغيير عنوان التصفح العام (Public)",
    "block-sites":    "حجب مواقع",
    "block_sites":    "حجب مواقع",
    "open-sites":     "فتح مواقع (Walled Garden)",
    "open_sites":     "فتح مواقع (Walled Garden)",
}


# ─── كتالوج الخدمات القابلة للتفعيل/الترقية ─────────────────────
#
# مصدر واحد لـ«صفحة الخدمات»: كل خدمة يستطيع المستخدم طلب
# تفعيلها/ترقيتها بمواصفاتها عبر النافذة الموحّدة. كل صفّ يَربط
# service_type (نفس ما تُرسله الواجهة) بأيقونة عرض وعلَم «مدفوعة».
# الترتيب هو ترتيب العرض. التسمية ونوع المواصفات (الحقول) يُشتقّان
# من SERVICE_LABELS + SERVICE_TYPE_MAP، فلا تكرار: إضافة خدمة
# جديدة = صفّ هنا + إدخالها في الخريطتَين أعلاه.


@dataclass(frozen=True)
class CatalogEntry:
    """خدمة واحدة في كتالوج «كل الخدمات».

    service_type  مفتاح الخدمة كما تستخدمه الواجهة (data-svc-type).
    icon          اسم أيقونة Font Awesome (بلا البادئة fa-).
    paid          خدمة مدفوعة (طلب يُدفَع للمزوّد) — تعرض شارة «مدفوعة».
    """
    service_type: str
    icon: str = "circle-nodes"
    paid: bool = False


#: ترتيب العرض في صفحة الكتالوج. كل خدمة لها نوع مواصفات في
#: SERVICE_TYPE_MAP — أيّ صفّ بلا نوع مُسجَّل يُسقَط بصمت من catalog().
_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry("ip_change",      "right-left",       paid=True),
    CatalogEntry("public-ip",      "globe",            paid=True),
    CatalogEntry("remote-access",  "key",              paid=True),
    CatalogEntry("vpn_tunnel",     "shield-halved",    paid=True),
    CatalogEntry("hotspot",        "wifi"),
    CatalogEntry("broadband",      "ethernet"),
    CatalogEntry("block-sites",    "ban"),
    CatalogEntry("open-sites",     "door-open"),
    CatalogEntry("bt_wifi_block",  "tower-broadcast"),
    CatalogEntry("loop_detect",    "arrows-spin"),
)


def catalog() -> list[dict[str, Any]]:
    """كل الخدمات القابلة للتفعيل/الترقية، جاهزة للعرض.

    تُعيد قائمة dict مرتّبة — كل عنصر يحوي service_type + التسمية
    العربيّة + نوع المواصفات (المفتاح/العنوان/الملخّص/عدد الحقول)
    + الأيقونة + علَم المدفوعة. تُسقَط أيّ خدمة بلا SpecKind مُسجَّل
    (دفاعيّ — لا يُفترض حدوثه ما دام الصفّ في الخريطة).
    """
    out: list[dict[str, Any]] = []
    for entry in _CATALOG:
        kind = kind_for_service(entry.service_type)
        if kind is None:
            continue
        out.append({
            "service_type": entry.service_type,
            "label": service_label(entry.service_type),
            "icon": entry.icon,
            "paid": entry.paid,
            "kind_key": kind.key,
            "kind_title": kind.title,
            "summary": kind.summary,
            "field_count": len(kind.fields),
        })
    return out


# ─── دوال الاستعلام ─────────────────────────────────────────────


def list_kinds() -> Iterable[SpecKind]:
    """كل أنواع المواصفات المُسجَّلة بترتيب التسجيل."""
    return tuple(_REGISTRY.values())


def get_kind(kind_key: str) -> SpecKind | None:
    """يُعيد SpecKind بمفتاحه، أو None لو غير معروف."""
    return _REGISTRY.get((kind_key or "").strip())


def kind_for_service(service_type: str) -> SpecKind | None:
    """يُعيد SpecKind المرتبط بـservice_type (نوع الخدمة كما تستخدمه
    الواجهة). يُعيد None لو الخدمة غير مُسجَّلة."""
    st = (service_type or "").strip()
    if not st:
        return None
    kind_key = SERVICE_TYPE_MAP.get(st)
    if not kind_key:
        return None
    return _REGISTRY.get(kind_key)


def service_label(service_type: str) -> str:
    """تسمية عربيّة قصيرة للخدمة (مفيدة في رأس النافذة والـtoast)."""
    return SERVICE_LABELS.get((service_type or "").strip(),
                              service_type or "خدمة")


# ─── التحقّق من المواصفات المُرسَلة من الواجهة ──────────────────


def _coerce_number(raw: Any) -> float | None:
    """يحاول تحويل القيمة إلى رقم. يُعيد None لو فشل/فارغ."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _coerce_bool(raw: Any) -> bool:
    """JS قد يُرسل true / "true" / "on" / "1" / 1 / "yes"."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw or "").strip().lower() in ("1", "true", "on", "yes")


def validate_spec(service_type: str, payload: dict[str, Any]
                  ) -> tuple[dict[str, Any], list[str]]:
    """يتحقّق من payload مقابل مخطّط الخدمة، يُعيد (clean, errors).

    سلوك:
      • الخدمة غير معروفة ⇒ errors = ["نوع الخدمة غير معروف"].
      • كل حقل required فارغ ⇒ "الحقل «<label>» مطلوب".
      • number خارج النطاق ⇒ "<label> خارج النطاق المسموح".
      • text/textarea أطول من max_length ⇒ "تجاوز الحدّ المسموح".
      • select قيمة غير ضمن options ⇒ "خيار غير صالح للحقل <label>".
      • أيّ مفتاح إضافي خارج المخطّط يُتجاهَل (لا يُنسَخ لـclean).
    clean يحوي فقط الحقول المعرّفة في المخطّط، مُحوَّلة لأنواعها
    (number→float، checkbox→bool، باقي النصوص مقصوصة إلى max_length).
    """
    kind = kind_for_service(service_type)
    if kind is None:
        return {}, ["نوع الخدمة غير معروف"]

    clean: dict[str, Any] = {}
    errors: list[str] = []

    for f in kind.fields:
        raw = payload.get(f.key)

        if f.type == "number":
            v = _coerce_number(raw)
            if v is None:
                if f.required:
                    errors.append(f"الحقل «{f.label}» مطلوب.")
                continue
            if f.min is not None and v < f.min:
                errors.append(f"«{f.label}» يجب ألا يقلّ عن {int(f.min)}.")
                continue
            if f.max is not None and v > f.max:
                errors.append(f"«{f.label}» يجب ألا يزيد عن {int(f.max)}.")
                continue
            clean[f.key] = int(v) if v.is_integer() else v

        elif f.type == "checkbox":
            clean[f.key] = _coerce_bool(raw)

        elif f.type == "select":
            v = str(raw or "").strip()
            if not v:
                if f.required:
                    errors.append(f"الحقل «{f.label}» مطلوب.")
                continue
            allowed = {str(o.get("value")) for o in f.options}
            if v not in allowed:
                errors.append(f"قيمة غير صالحة للحقل «{f.label}».")
                continue
            clean[f.key] = v

        elif f.type in ("text", "textarea"):
            v = str(raw or "").strip()
            if not v:
                if f.required:
                    errors.append(f"الحقل «{f.label}» مطلوب.")
                continue
            if f.max_length and len(v) > f.max_length:
                errors.append(
                    f"«{f.label}» يتجاوز الحدّ المسموح "
                    f"({f.max_length} حرفًا).")
                continue
            clean[f.key] = v

        else:
            errors.append(f"نوع حقل غير معروف: {f.type}")

    return clean, errors


__all__ = [
    "SpecField",
    "SpecKind",
    "CatalogEntry",
    "SERVICE_TYPE_MAP",
    "SERVICE_LABELS",
    "catalog",
    "list_kinds",
    "get_kind",
    "kind_for_service",
    "service_label",
    "validate_spec",
]
