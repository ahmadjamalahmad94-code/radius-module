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

خدمتان مُسجَّلتان الآن بقوالب **مبدئية (PLACEHOLDER)** — بانتظار سكربت
المستخدم:
  • bt_wifi_block — منع بث البلوتوث والواي فاي.
  • loop_detect   — تتبّع اللوب (Loop Detection).

ملاحظة مهمة: القوالب الحالية تحوي تعليقًا عربيًا «‹ضع السكربت هنا —
بانتظار سكربت المستخدم›» في موضع الأوامر الفعلية. لا نخترع أوامر
MikroTik لمنع البث أو كشف اللوب — تُضاف يدويًا عند توفّرها.
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

_INTERFACE_NAME_RE = re.compile(r"^[A-Za-z0-9\-_\.]{1,32}$")


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

_PLACEHOLDER_MARKER = "‹ضع سكربت المستخدم هنا — بانتظار سكربت التفعيل›"
_REMOVE_MARKER = "‹ضع سكربت الإزالة هنا — بانتظار سكربت المستخدم›"


_BT_WIFI_BLOCK = PortScriptService(
    slug="bt_wifi_block",
    title="منع بث البلوتوث والواي فاي",
    description=(
        "يطبّق سكربتًا يمنع بث البلوتوث والواي فاي على المنافذ المختارة. "
        "السكربت المبدئي بانتظار سكربت المستخدم النهائي."
    ),
    icon="tower-broadcast",
    iface_line_template=(
        "# " + _PLACEHOLDER_MARKER + " — على الواجهة " + IFACE_PLACEHOLDER
    ),
    script_template="\n".join([
        "# === Hoberadius — منع بث البلوتوث والواي فاي (تفعيل) ===",
        "# الخدمة: bt_wifi_block",
        "# المنافذ المختارة: {{PORTS}}",
        "# كل أمر يجب أن يحمل comment=" + PSS_COMMENT_PREFIX + "bt_wifi_block",
        "# لتسهيل التراجع لاحقًا.",
        "#",
        "# " + _PLACEHOLDER_MARKER,
        "# (أضِف أوامر RouterOS الفعلية هنا — لكل منفذ سطر إن لزم:)",
        "{{IFACES}}",
        "",
    ]),
    remove_iface_line_template=(
        "# " + _REMOVE_MARKER + " — على الواجهة " + IFACE_PLACEHOLDER
    ),
    remove_template="\n".join([
        "# === Hoberadius — منع بث البلوتوث والواي فاي (إزالة/تعطيل) ===",
        "# الخدمة: bt_wifi_block — إزالة",
        "# المنافذ المختارة: {{PORTS}}",
        "# المفترض أن يُزيل كل كائن يحمل comment="
        + PSS_COMMENT_PREFIX + "bt_wifi_block",
        "#",
        "# " + _REMOVE_MARKER,
        "# (ضع هنا أوامر إزالة/تعطيل RouterOS — لكل منفذ سطر إن لزم:)",
        "{{IFACES}}",
        "",
    ]),
    is_placeholder=True,
)


_LOOP_DETECT = PortScriptService(
    slug="loop_detect",
    title="تتبّع اللوب",
    description=(
        "يطبّق سكربت كشف اللوب (Loop Detection) على المنافذ المختارة. "
        "السكربت المبدئي بانتظار سكربت المستخدم النهائي."
    ),
    icon="arrows-spin",
    iface_line_template=(
        "# " + _PLACEHOLDER_MARKER + " — على الواجهة " + IFACE_PLACEHOLDER
    ),
    script_template="\n".join([
        "# === Hoberadius — تتبّع اللوب (Loop Detection) (تفعيل) ===",
        "# الخدمة: loop_detect",
        "# المنافذ المختارة: {{PORTS}}",
        "# كل أمر يجب أن يحمل comment=" + PSS_COMMENT_PREFIX + "loop_detect",
        "# لتسهيل التراجع لاحقًا.",
        "#",
        "# " + _PLACEHOLDER_MARKER,
        "# (أضِف أوامر RouterOS الفعلية هنا — لكل منفذ سطر إن لزم:)",
        "{{IFACES}}",
        "",
    ]),
    remove_iface_line_template=(
        "# " + _REMOVE_MARKER + " — على الواجهة " + IFACE_PLACEHOLDER
    ),
    remove_template="\n".join([
        "# === Hoberadius — تتبّع اللوب (Loop Detection) (إزالة/تعطيل) ===",
        "# الخدمة: loop_detect — إزالة",
        "# المنافذ المختارة: {{PORTS}}",
        "# المفترض أن يُزيل كل كائن يحمل comment="
        + PSS_COMMENT_PREFIX + "loop_detect",
        "#",
        "# " + _REMOVE_MARKER,
        "# (ضع هنا أوامر إزالة/تعطيل RouterOS — لكل منفذ سطر إن لزم:)",
        "{{IFACES}}",
        "",
    ]),
    is_placeholder=True,
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


__all__ = [
    "PSS_COMMENT_PREFIX",
    "IFACE_PLACEHOLDER",
    "PortScriptService",
    "PortScriptPlan",
    "REGISTRY",
    "list_services",
    "get_service",
    "render_iface_block",
    "render_script",
    "build_plan",
    "build_push_commands",
    "discover_interfaces",
]
