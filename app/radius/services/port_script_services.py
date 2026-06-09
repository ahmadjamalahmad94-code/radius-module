"""port_script_services — إطار عام لخدمات السكربت المبنيّة على المنافذ.

الفكرة (مطابقة لنمط «برمجة الهوتسبوت/البرودباند» في mt_programming):
  1) نكتشف واجهات الراوتر (المنافذ) عبر RouterOS API.
  2) المشغّل يختار أيّ واجهات/منافذ تنطبق عليها الخدمة.
  3) نُولّد سكربت RouterOS من قالب الخدمة بعد استبدال العناصر النائبة:
       {{PORTS}}  → قائمة المنافذ مفصولة بفواصل (port1,port2,…)
       {{IFACES}} → أسطر RouterOS لكل واجهة (واحدة في كل سطر) — للقوالب
                    التي تكرّر أمرًا لكل منفذ.
  4) (Q2) يُطبَّق السكربت على الراوتر أمرًا أمرًا (apply اختياري).

التعميم: بدل كتابة صفحة برمجة منفصلة لكل خدمة، نُسجّل كل خدمة بـ slug
في REGISTRY أدناه. إضافة خدمة جديدة = إدخال PortScriptService واحد.
استبدال السكربت الحقيقي لاحقًا = تعديل في مكان واحد فقط (حقل
`script_template` للخدمة) — لا تغييرات في المسارات أو القالب.

خدمتان مُسجَّلتان ومُفعَّلتان الآن بسكربتات حقيقية (is_placeholder=False):
  • bt_wifi_block — منع المشاركة بتثبيت TTL=1 (قاعدة mangle لكل منفذ،
    موسومة HR-AntiShare).
  • loop_detect   — كشف اللوب بإضافة عميل DHCP لكل منفذ (موسوم
    HR-LoopDetect)؛ الحالة تُقرأ حيًّا عبر read_loop_status (bound=لوب).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


# العلامة التي يحملها كل أمر نُصدِره — تُمكّن أي «تراجع» مستقبلي من
# إيجاد كائنات هذه الخدمة وإزالتها (نفس عقد hoberadius:<kind> في
# mt_programming). الـ slug يُلحَق لكل خدمة: hoberadius:pss:<slug>.
PSS_COMMENT_PREFIX = "hoberadius:pss:"

# نائب اسم الواجهة الواحدة داخل سطر {{IFACES}} المتكرّر.
IFACE_PLACEHOLDER = "{{IFACE}}"

# وسوم ثابتة تحملها الكائنات التي يُنشئها كل سكربت على الراوتر — تُسهّل
# الكشف والإزالة لاحقًا (بحث RouterOS بـ comment~"<TAG>"). الوسم يحمل
# اسم الواجهة بعده ليصبح فريدًا لكل منفذ (إزالة دقيقة بـ comment="<TAG> ifc").
BT_WIFI_TAG = "HR-AntiShare"      # قاعدة mangle لتثبيت TTL=1 (منع المشاركة)
LOOP_DETECT_TAG = "HR-LoopDetect"  # عميل DHCP على المنفذ لكشف اللوب

_INTERFACE_NAME_RE = re.compile(r"^[A-Za-z0-9\-_\.]{1,32}$")


# ─── مرشّح «LAN-only» المُشترَك بين خدمتَي loop_detect وbt_wifi_block ──
#
# الخدمتان تنطبقان فقط على منافذ الوصول الداخلي (LAN). تركيب dhcp-client
# على واجهة WAN يكسر التوجيه، وتثبيت TTL=1 على نفق VPN يقطع الاتصال
# المركزي. لذلك نستبعد عبر مرشّح موحّد:
#   (أ) واجهة WAN: إن مُرِّر اسمها صراحةً (محفوظ في wizard_runs.
#       selected_wan_interface) نستبعدها؛ وإلا نستبعد default_wan="ether1"
#       كاحتراز افتراضي فقط (لا تثبيت صلب — يمكن للمناداة تجاوزه).
#   (ب) واجهات الأنفاق/الـVPN عبر type — هذا هو الفحص البنيوي القاسي:
#       pppoe/pptp/l2tp/sstp/ovpn/openvpn/ipsec/wireguard/gre/ipip/eoip/
#       vrrp/loopback. مفيد في حال تغيّر اسم النفق.
#   (ج) واجهات الأنفاق المعروفة في حُزمة Hoberadius عبر الاسم (احتراز
#       ثانٍ في حال أعاد الراوتر إعلان type غير دقيق): hr-wg، hobe-vpn،
#       lo، وكل ما يبدأ بـhr-pppoe-/pppoe-/pptp-/l2tp-/sstp-/ovpn-/
#       ipsec-/wg-/wireguard-.

_LAN_FILTER_EXCLUDE_TYPES: frozenset[str] = frozenset({
    "pppoe-in", "pppoe-out", "pppoe", "ppp-client",
    "pptp-in", "pptp-out", "pptp",
    "l2tp-in", "l2tp-out", "l2tp",
    "sstp-in", "sstp-out", "sstp",
    "ovpn-in", "ovpn-out", "ovpn",
    "openvpn-in", "openvpn-out", "openvpn",
    "ipsec",
    "wireguard", "wg",
    "gre", "gre-tunnel", "gre6", "gre6-tunnel",
    "ipip", "ipip-tunnel", "ipip6", "ipip6-tunnel",
    "eoip", "eoip-tunnel",
    "vrrp",
    "loopback", "lo",
})

_LAN_FILTER_EXCLUDE_NAMES: frozenset[str] = frozenset({
    "lo", "loopback",
    "hr-wg",       # نفق WireGuard إلى الـ VPS المركزي (طبقة إدارة)
    "hobe-vpn",    # اسم بديل للنفق المركزي
})

_LAN_FILTER_EXCLUDE_NAME_PREFIXES: tuple[str, ...] = (
    "hr-pppoe-",   # عملاء PPPoE التي يبنيها معالج الإعداد على المنفذ
    "pppoe-",
    "pptp-",
    "l2tp-",
    "sstp-",
    "ovpn-",
    "openvpn-",
    "ipsec-",
    "wg-",
    "wireguard-",
)

_DEFAULT_WAN_HINT = "ether1"


def is_lan_port(row: Mapping[str, Any], *,
                wan_iface: str = "",
                default_wan: str = _DEFAULT_WAN_HINT) -> bool:
    """يحدّد ما إذا كانت الواجهة منفذًا LAN صالحًا لخدمات port-based.

    True فقط للمنافذ المسموحة (ether/bridge/wlan/vlan ميدانية، ليست WAN
    ولا نفقًا). الفحص دفاعي: يستبعد عبر type أولًا (الأقوى) ثم بالأسماء
    (احتراز)، وأخيرًا يستبعد اسم الـWAN المُمرَّر (أو الافتراضي ether1).

    إن مُرِّر wan_iface صراحةً يُحجَب فقط هو (default_wan لا يُطبَّق) — هذا
    يسمح للمعرفة المُسجّلة في wizard_runs بتجاوز الاحتراز الافتراضي.
    """
    name = str(row.get("name") or "").strip()
    if not name:
        return False
    name_lc = name.lower()
    # (أ) WAN
    wan_name = (wan_iface or "").strip()
    if wan_name:
        if name == wan_name or name_lc == wan_name.lower():
            return False
    else:
        # لا WAN معروف ⇒ نستخدم احتراز افتراضي قابل للتجاوز.
        dflt = (default_wan or "").strip()
        if dflt and (name == dflt or name_lc == dflt.lower()):
            return False
    # (ب) type
    type_str = str(row.get("type") or "").strip().lower()
    if type_str and type_str in _LAN_FILTER_EXCLUDE_TYPES:
        return False
    # (ج) name + prefixes
    if name_lc in _LAN_FILTER_EXCLUDE_NAMES:
        return False
    for prefix in _LAN_FILTER_EXCLUDE_NAME_PREFIXES:
        if name_lc.startswith(prefix):
            return False
    return True


def filter_lan_ports(rows: Sequence[Mapping[str, Any]], *,
                     wan_iface: str = "",
                     default_wan: str = _DEFAULT_WAN_HINT
                     ) -> list[dict]:
    """يُرجع نسخة من rows مع استبعاد كل ما لا يجتاز is_lan_port.
    يحافظ على الترتيب الأصلي وعلى كل حقول الصفوف."""
    return [dict(r) for r in (rows or [])
            if is_lan_port(r, wan_iface=wan_iface, default_wan=default_wan)]


# ─── العنصر الأساسي: تعريف خدمة سكربت مبنيّة على المنافذ ──────────


@dataclass(frozen=True)
class PortScriptService:
    """تعريف خدمة واحدة.

    الحقول:
      slug          معرّف ثابت يُستخدم في الـ URL والعلامة — لا يُعاد
                    تسميته (يكسر الروابط/التراجع).
      title         عنوان عربي يظهر في الواجهة.
      description    وصف عربي قصير لما تفعله الخدمة.
      icon          أيقونة FontAwesome (بدون البادئة fa-).
      script_template
                    قالب RouterOS النصّي. يدعم العناصر النائبة:
                      {{PORTS}}, {{IFACES}}.
                    *هذا هو المكان الوحيد* الذي يُلصق فيه السكربت
                    الحقيقي لاحقًا.
      iface_line_template
                    (اختياري) قالب سطر واحد يُكرَّر لكل منفذ مُختار عند
                    استبدال {{IFACES}}. استخدم {{IFACE}} لاسم الواجهة.
                    إن تُرِك فارغًا، يصبح {{IFACES}} مجرد قائمة الأسماء
                    سطرًا لكل اسم.
      remove_template
                    قالب سكربت **الإزالة/التعطيل** — نفس صيغة
                    script_template ({{PORTS}}, {{IFACES}}). هذا هو
                    المكان الوحيد الذي يُلصق فيه سكربت الإزالة لاحقًا.
      remove_iface_line_template
                    نظير iface_line_template لكن لسكربت الإزالة.
      is_placeholder
                    True طالما القالبان مبدئيان (بانتظار سكربت المستخدم)
                    — تعرضه الواجهة كتنبيه واضح ويمنع زرّي التطبيق
                    والإزالة (لا يوجد ما يُدفَع بعد).
    """
    slug: str
    title: str
    description: str
    icon: str
    script_template: str
    iface_line_template: str = ""
    remove_template: str = ""
    remove_iface_line_template: str = ""
    is_placeholder: bool = True

    @property
    def comment(self) -> str:
        return PSS_COMMENT_PREFIX + self.slug


@dataclass
class PortScriptPlan:
    """ما يُسلَّم للقالب بعد توليد السكربت لخدمة + منافذ مختارة."""
    slug: str
    title: str
    selected_ports: list[str]
    script: str
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_placeholder: bool = True


# ─── سجلّ الخدمات (REGISTRY) ─────────────────────────────────────
#
# لإضافة خدمة جديدة: أضِف PortScriptService هنا.
# لإلصاق السكربت الحقيقي لخدمة قائمة: استبدل نص `script_template`
# (وعدّل is_placeholder=False) — لا شيء آخر يتغيّر.

# ─── الخدمة 1: منع بث البلوتوث/الواي فاي — منع المشاركة بـTTL=1 ───
#
# الفكرة (سكربت المستخدم المرجعي): قاعدة mangle في سلسلة postrouting
# تثبّت TTL=1 على الترافيك الخارج من المنفذ، فيموت بعد قفزة واحدة ولا
# يستطيع أي جهاز خلف هوتسبوت/راوتر زبون مشاركته. المرجع كان out-interface=
# bridge ثابتًا؛ هنا نعمّمه ليُطبَّق سطرًا **لكل منفذ مختار** (out-interface=
# <iface>) ويحمل وسمًا ثابتًا HR-AntiShare <iface> لتسهيل الحذف/الكشف.

_BT_WIFI_BLOCK = PortScriptService(
    slug="bt_wifi_block",
    title="منع بث البلوتوث والواي فاي",
    description=(
        "يثبّت TTL=1 على الترافيك الخارج من المنافذ المختارة (قاعدة mangle "
        "في postrouting)، فيمنع مشاركة الإنترنت عبر البلوتوث/الواي فاي خلف "
        "جهاز الزبون. كل قاعدة تحمل وسم HR-AntiShare لتسهيل الإزالة."
    ),
    icon="tower-broadcast",
    # سطر التفعيل لكل منفذ: قاعدة mangle تثبّت TTL=1 على الخارج منه.
    iface_line_template=(
        "/ip firewall mangle add chain=postrouting "
        "out-interface=" + IFACE_PLACEHOLDER + " "
        "action=change-ttl new-ttl=set:1 passthrough=yes "
        'comment="' + BT_WIFI_TAG + " " + IFACE_PLACEHOLDER + '"'
    ),
    script_template="\n".join([
        "# === Hoberadius — منع بث البلوتوث والواي فاي (تفعيل) ===",
        "# الخدمة: bt_wifi_block — تثبيت TTL=1 لمنع المشاركة.",
        "# المنافذ المختارة: {{PORTS}}",
        "# كل قاعدة mangle تحمل comment=\"" + BT_WIFI_TAG + " <iface>\".",
        "{{IFACES}}",
        "",
    ]),
    # الإزالة لكل منفذ: نحذف القاعدة الموسومة لذلك المنفذ تحديدًا.
    remove_iface_line_template=(
        "/ip firewall mangle remove "
        '[find comment="' + BT_WIFI_TAG + " " + IFACE_PLACEHOLDER + '"]'
    ),
    remove_template="\n".join([
        "# === Hoberadius — منع بث البلوتوث والواي فاي (إزالة) ===",
        "# الخدمة: bt_wifi_block — إزالة قواعد TTL=1 الموسومة "
        + BT_WIFI_TAG + ".",
        "# المنافذ المختارة: {{PORTS}}",
        "{{IFACES}}",
        "",
    ]),
    is_placeholder=False,
)


# ─── الخدمة 2: كشف اللوب — عميل DHCP على المنفذ + تتبّع الحالة ────
#
# الفكرة (شرح المستخدم): نضيف dhcp-client على المنفذ بـadd-default-route=
# no (حتى لا يعبث بجدول التوجيه). إن وُجد لوب فالطلب يدور ويعود فيستلم
# العميل عنوانًا (status=bound مع address/gateway/dhcp-server) = لوب. إن
# لم يوجد لوب يبقى searching. الحالة تُقرأ حيًّا عبر read_loop_status أدناه.

_LOOP_DETECT = PortScriptService(
    slug="loop_detect",
    title="تتبّع اللوب",
    description=(
        "يضيف عميل DHCP على المنافذ المختارة لكشف اللوب: إن استلم المنفذ "
        "عنوانًا (bound) فهناك لوب، وإن بقي searching فلا لوب. زر «فحص "
        "اللوب» يقرأ الحالة الحيّة من الراوتر ويعرضها لكل منفذ."
    ),
    icon="arrows-spin",
    # سطر التفعيل لكل منفذ: عميل DHCP بلا مسار افتراضي ولا DNS/NTP،
    # موسوم HR-LoopDetect <iface> لقراءة حالته لاحقًا.
    iface_line_template=(
        "/ip dhcp-client add interface=" + IFACE_PLACEHOLDER + " "
        "add-default-route=no use-peer-dns=no use-peer-ntp=no disabled=no "
        'comment="' + LOOP_DETECT_TAG + " " + IFACE_PLACEHOLDER + '"'
    ),
    script_template="\n".join([
        "# === Hoberadius — تتبّع اللوب (Loop Detection) (تفعيل) ===",
        "# الخدمة: loop_detect — عميل DHCP لكل منفذ لكشف اللوب.",
        "# المنافذ المختارة: {{PORTS}}",
        "# كل عميل يحمل comment=\"" + LOOP_DETECT_TAG + " <iface>\".",
        "# بعد التفعيل استخدم «فحص اللوب» لقراءة الحالة (bound=لوب).",
        "{{IFACES}}",
        "",
    ]),
    # الإزالة لكل منفذ: نحذف عميل DHCP الموسوم لذلك المنفذ تحديدًا.
    remove_iface_line_template=(
        "/ip dhcp-client remove "
        '[find comment="' + LOOP_DETECT_TAG + " " + IFACE_PLACEHOLDER + '"]'
    ),
    remove_template="\n".join([
        "# === Hoberadius — تتبّع اللوب (إزالة) ===",
        "# الخدمة: loop_detect — إزالة عملاء DHCP الموسومين "
        + LOOP_DETECT_TAG + ".",
        "# المنافذ المختارة: {{PORTS}}",
        "{{IFACES}}",
        "",
    ]),
    is_placeholder=False,
)


REGISTRY: dict[str, PortScriptService] = {
    _BT_WIFI_BLOCK.slug: _BT_WIFI_BLOCK,
    _LOOP_DETECT.slug: _LOOP_DETECT,
}


def list_services() -> list[PortScriptService]:
    """كل الخدمات المُسجَّلة بالترتيب الثابت."""
    return list(REGISTRY.values())


def get_service(slug: str) -> PortScriptService | None:
    return REGISTRY.get((slug or "").strip())


# ─── توليد السكربت من قالب الخدمة + المنافذ المختارة ─────────────


def _validate_ports(ports: Sequence[str]) -> list[str]:
    """ينظّف ويُتحقّق من أسماء الواجهات المختارة. يرفع ValueError عند
    اسم غير صالح. يُزيل التكرار مع الحفاظ على الترتيب."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in ports:
        name = (raw or "").strip()
        if not name:
            continue
        if not _INTERFACE_NAME_RE.match(name):
            raise ValueError(f"اسم الواجهة غير صالح: {name}")
        if name not in seen:
            seen.add(name)
            out.append(name)
    if not out:
        raise ValueError("اختر منفذًا واحدًا على الأقل.")
    return out


def render_iface_block(service: PortScriptService, ports: Sequence[str],
                       *, remove: bool = False) -> str:
    """يبني نص {{IFACES}}: سطر لكل منفذ. إن عرّفت الخدمة قالب سطر
    (iface_line_template للتفعيل / remove_iface_line_template للإزالة)
    نستبدل {{IFACE}} فيه؛ وإلا نضع اسم المنفذ خامًا."""
    if remove:
        tpl = service.remove_iface_line_template or IFACE_PLACEHOLDER
    else:
        tpl = service.iface_line_template or IFACE_PLACEHOLDER
    return "\n".join(tpl.replace(IFACE_PLACEHOLDER, port) for port in ports)


def render_script(service: PortScriptService, ports: Sequence[str],
                  *, remove: bool = False) -> str:
    """يستبدل العناصر النائبة في قالب الخدمة (التفعيل أو الإزالة):
      {{PORTS}}  → 'port1,port2,…'
      {{IFACES}} → كتلة الأسطر المتكرّرة (render_iface_block).
    """
    ports_csv = ",".join(ports)
    iface_block = render_iface_block(service, ports, remove=remove)
    script = service.remove_template if remove else service.script_template
    script = script.replace("{{PORTS}}", ports_csv)
    script = script.replace("{{IFACES}}", iface_block)
    return script


def build_plan(slug: str, ports: Sequence[str],
               *, remove: bool = False) -> PortScriptPlan:
    """يُولّد خطة كاملة (سكربت + ملخّص + تحذيرات) لخدمة + منافذ مختارة.

    remove=True يبني خطة الإزالة/التعطيل (من remove_template) بدل خطة
    التفعيل. يرفع ValueError عند slug غير معروف أو منافذ غير صالحة."""
    service = get_service(slug)
    if service is None:
        raise ValueError("الخدمة غير معروفة.")
    valid_ports = _validate_ports(ports)
    script = render_script(service, valid_ports, remove=remove)
    action = "الإزالة/التعطيل" if remove else "التفعيل"
    summary = [
        f"الخدمة: {service.title} ({action}).",
        f"المنافذ المختارة ({len(valid_ports)}): {', '.join(valid_ports)}.",
        f"كل أمر يحمل comment={service.comment} لتسهيل التراجع.",
    ]
    warnings: list[str] = []
    if service.is_placeholder:
        warnings.append(
            "هذا قالب مبدئي — لم يُضَف السكربت الفعلي بعد. الدفع للراوتر "
            "معطّل حتى يُلصَق سكربت المستخدم في قالب الخدمة."
        )
    return PortScriptPlan(
        slug=service.slug,
        title=service.title,
        selected_ports=valid_ports,
        script=script,
        summary=summary,
        warnings=warnings,
        is_placeholder=service.is_placeholder,
    )


# ─── دفع السكربت إلى الراوتر — إعادة استخدام منفّذ الأوامر الموجود ─
#
# لا نخترع مسار دفع جديدًا: نُعيد استخدام mt_programming.Command +
# mt_programming.apply_commands (نفس ما تستخدمه برمجة الهوتسبوت/
# البرودباند). السكربت المُولّد (نصّ console RouterOS) يُدفَع كـ
# «سكربت نظام» مؤقّت ثم يُشغَّل ثم يُحذَف (تنظيف). هكذا يعمل الدفع مع
# أي نصّ يلصقه المستخدم لاحقًا دون أي تغيير في الكود.


def build_push_commands(script: str, *, name: str, comment: str,
                        cleanup: bool = True) -> list:
    """يبني قائمة Command لدفع `script` إلى الراوتر عبر
    mt_programming.apply_commands.

    name     اسم سكربت النظام المؤقّت (يُمرَّره المسار فريدًا لكل عملية
             حتى لا يتعارض مع بقايا محاولة سابقة).
    comment  تعليق hoberadius:pss:<slug> — يحمله سكربت النظام، والكائنات
             التي يُنشئها يُفترض أن تحمله أيضًا (لتسهيل الإزالة).
    cleanup  حذف سكربت النظام المؤقّت بعد تشغيله (الكائنات الناتجة تبقى).

    إرجاع: list[mt_programming.Command]. نستورد Command داخل الدالة حتى
    تبقى هذه الوحدة قابلة للاستيراد/الاختبار بأقل اعتماديات."""
    from .mt_programming import Command

    cmds = [
        Command("/system/script/add", {
            "name": name,
            "source": script,
            "comment": comment,
        }),
        # تشغيل السكربت بالاسم. (إن احتاج بناء RouterOS لديك التشغيل
        # بالـ.id بدل الاسم، فهذا هو السطر الوحيد المطلوب تعديله.)
        Command("/system/script/run", {"number": name}),
    ]
    if cleanup:
        cmds.append(Command("/system/script/remove", {"numbers": name}))
    return cmds


# ─── اكتشاف المنافذ/الواجهات عبر RouterOS API ────────────────────


def discover_interfaces(nas_call: Mapping[str, Any],
                        interface_list_fn: Callable[[Mapping[str, Any]], Any]
                        ) -> list[dict]:
    """يكتشف واجهات الراوتر عبر دالة القراءة المُمرَّرة (عادةً
    mikrotik_admin_client.interface_list). نمرّر الدالة بدل استيرادها
    هنا حتى تبقى الوحدة قابلة للاختبار بلا راوتر (نمرّر stub).

    يُرجع قائمة قواميس الواجهات كما يعيدها العميل، أو [] عند الفشل
    (الفشل غير قاتل — الواجهة تعرض رسالة وتسمح بإدخال يدوي)."""
    try:
        res = interface_list_fn(nas_call)
    except Exception:  # noqa: BLE001
        return []
    if not getattr(res, "ok", False):
        return []
    return list(getattr(res, "data", []) or [])


# ─── تتبّع حالة اللوب — قراءة /ip dhcp-client الحيّة ──────────────
#
# خدمة loop_detect تنشئ عميل DHCP موسومًا HR-LoopDetect على كل منفذ.
# هنا نقرأ تلك الإدخالات حيًّا ونحوّلها لحالة مفهومة لكل منفذ:
#   status=bound (أو عاد عنوان غير 0.0.0.0) → لوب مكتشف.
#   status=searching/أي شيء آخر بلا عنوان    → لا لوب.


@dataclass(frozen=True)
class LoopProbe:
    """نتيجة فحص اللوب لمنفذ واحد (مشتقّة من إدخال dhcp-client موسوم)."""
    iface: str
    status: str
    address: str
    gateway: str
    dhcp_server: str
    is_loop: bool
    message: str


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _probe_from_row(iface: str, row: Mapping[str, Any]) -> "LoopProbe":
    """يصنع LoopProbe من صفّ dhcp-client موجود — bound أو searching."""
    status = _clean(row.get("status")).lower()
    address = _clean(row.get("address"))
    gateway = _clean(row.get("gateway"))
    dhcp_server = _clean(row.get("dhcp-server") or row.get("dhcp_server"))
    has_addr = bool(address) and not address.startswith("0.0.0.0")
    is_loop = status.startswith("bound") or has_addr
    if is_loop:
        msg = f"لوب مكتشف على {iface} — رجع IP {address or '—'}"
        if dhcp_server:
            msg += f" من DHCP server {dhcp_server}"
    else:
        msg = f"لا لوب على {iface} (الحالة: {status or 'searching'})"
    return LoopProbe(
        iface=iface, status=status, address=address, gateway=gateway,
        dhcp_server=dhcp_server, is_loop=is_loop, message=msg,
    )


def _probe_missing(iface: str) -> "LoopProbe":
    """منفذ مُختار لكنه لم يجد له قاعدة HR-LoopDetect مركّبة — نُنبّه
    المشغّل بدل إسقاطه من النتيجة بصمت."""
    return LoopProbe(
        iface=iface, status="no-rule", address="", gateway="",
        dhcp_server="", is_loop=False,
        message=(
            f"لم تُركَّب قاعدة كشف اللوب على {iface} بعد — "
            "اضغط «معاينة سكربت التفعيل» ثم «تطبيق» أولًا."
        ),
    )


def parse_loop_status(rows: Sequence[Mapping[str, Any]], *,
                      only_ports: Sequence[str] | None = None,
                      tag: str = LOOP_DETECT_TAG) -> list[LoopProbe]:
    """يحوّل صفوف /ip dhcp-client إلى نتائج فحص اللوب — *واحد لكل منفذ
    مُختار* (لا تسقط بنود بصمت).

    منطق اللوب لصفّ موسوم HR-LoopDetect:
      status يبدأ بـ"bound"           → لوب (دار الطلب وعاد فاستُلِم عنوان).
      أو عاد address غير 0.0.0.0     → لوب أيضًا (احتراز لاختلاف صيغ ROS).
      غير ذلك (searching/…)           → لا لوب.

    سلوك only_ports — *إصلاح فحص اللوب* (يونيو 2026):
      الإصدار السابق كان يكرّر على صفوف /ip dhcp-client ويُسقِط أيّ منفذ
      مُختار لم يجد له صفًّا (= حذف بصمت). نتيجة: يختار المشغّل 9 منافذ
      فيرى 4 فقط — لأن apply كان شُغِّل بـ4 فقط. الآن: نُفهرس الصفوف
      الموسومة بالاسم ثم نُولّد بطاقة لكلّ منفذ من only_ports بنفس
      ترتيبه: موجود ⇒ probe حقيقي؛ مفقود ⇒ probe «no-rule» يُنبّه.
    إن لم يُمرّر only_ports نُخرج كل الإدخالات الموسومة كما هي (سلوك العرض
    العامّ — صفحة الخدمة بلا اختيار).
    """
    # 1) فهرسة الصفوف الموسومة باسم الواجهة.
    tagged: dict[str, Mapping[str, Any]] = {}
    for row in rows or []:
        comment = _clean(row.get("comment"))
        if tag not in comment:
            continue
        iface = _clean(row.get("interface"))
        if not iface:
            continue
        tagged.setdefault(iface, row)

    # 2) only_ports — بطاقة لكل منفذ مُختار بترتيبه.
    if only_ports is not None:
        out: list[LoopProbe] = []
        seen: set[str] = set()
        for raw in only_ports:
            port = _clean(raw)
            if not port or port in seen:
                continue
            seen.add(port)
            row = tagged.get(port)
            out.append(_probe_from_row(port, row) if row is not None
                       else _probe_missing(port))
        return out

    # 3) العرض العامّ — كل الموسومين كما هم.
    return [_probe_from_row(iface, row) for iface, row in tagged.items()]


def read_loop_status(nas_call: Mapping[str, Any],
                     dhcp_client_fn: Callable[[Mapping[str, Any]], Any],
                     *, only_ports: Sequence[str] | None = None
                     ) -> tuple[list[LoopProbe], str]:
    """يقرأ حالة اللوب الحيّة عبر دالة قراءة /ip dhcp-client المُمرَّرة
    (عادةً mikrotik_admin_client.dhcp_client_list — نمرّرها بدل استيرادها
    حتى تبقى الوحدة قابلة للاختبار بلا راوتر، نفس نمط discover_interfaces).

    يُرجع (probes, error): عند نجاح القراءة error='' وprobes هي حالة كل
    منفذ موسوم؛ عند الفشل probes=[] وerror رسالة عربية."""
    try:
        res = dhcp_client_fn(nas_call)
    except Exception as e:  # noqa: BLE001
        return [], f"تعذّر قراءة حالة اللوب من الراوتر: {e}"
    if not getattr(res, "ok", False):
        return [], (_clean(getattr(res, "error", ""))
                    or "تعذّر الاتصال بالراوتر لقراءة حالة اللوب.")
    probes = parse_loop_status(
        getattr(res, "data", []) or [], only_ports=only_ports)
    return probes, ""


__all__ = [
    "PSS_COMMENT_PREFIX",
    "IFACE_PLACEHOLDER",
    "BT_WIFI_TAG",
    "LOOP_DETECT_TAG",
    "PortScriptService",
    "PortScriptPlan",
    "LoopProbe",
    "REGISTRY",
    "list_services",
    "get_service",
    "render_iface_block",
    "render_script",
    "build_plan",
    "build_push_commands",
    "discover_interfaces",
    "parse_loop_status",
    "read_loop_status",
    "is_lan_port",
    "filter_lan_ports",
]
