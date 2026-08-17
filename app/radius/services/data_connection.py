"""«اتصال بيانات» — مولّد سكربت اتصال العميل (subscriber DATA connection).

feat/data-connection-oneclick. المشترك في RADIUS يريد ربط مايكروتيك جديد:
يختار إصدار المايكروتيك ويضغط زرًّا واحدًا، فيحصل على سكربت `.rsc` جاهز للّصق.

كل شيء يحدث على RADIUS VPS الخاص بالعميل — **لا بروكسي، لا CHR، ولا أي
نداء للوحة التراخيص في مسار الاتصال**. الهدف (الإصدارات):

  * **v6** → حساب accel-ppp عبر SSTP أو PPTP (transport=vps_accel، شيفت
    Filter-Id بسرعة 5 ميجابت فقط — راجع ``accel_attributes``). الهدف =
    النطاق الفرعي للعميل ``clientN.hoberadius.com`` مع شهادة Let's Encrypt
    حقيقية (لذا verify-server-certificate=yes).
  * **v7** → قرين WireGuard (راجع ``data_connection_wg``). نقطة النهاية =
    نفس النطاق الفرعي + منفذ WG على الـVPS.

هذا الملف **خالص**: يبني نصوص السكربت ويحرس ضد تسرّب أي عنوان داخلي
(CHR/بروكسي/شبكات الإدارة). لا I/O، لا DB. التهيئة تُقرأ من
``env_settings`` كي يضبطها المالك من الواجهة.

LAB-PENDING (مُجمَّعة هنا كمصدر وحيد):
  * ``HOBERADIUS_CLIENT_SUBDOMAIN`` — النطاق الفرعي الفعلي للعميل
    (``clientN.hoberadius.com``). تُنشئه لوحة التراخيص لاحقًا عبر Cloudflare؛
    حتى ذلك الحين يضبطه المالك يدويًا في إعدادات النظام.
  * ``HOBERADIUS_DATA_WG_PORT`` — منفذ استماع WireGuard لاتصال البيانات على
    الـVPS (مستقلّ عن نفق الإدارة 51820). راجع ``data_connection_wg``.
  * شكل ``Filter-Id`` الدقيق — يعيش في ``accel_attributes.ACCEL_FILTER_ID_FORM``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..core import env_settings
from . import accel_attributes

# ════════════════════════════════════════════════════════════════════════
# التهيئة — تُقرأ من env_settings (DB → env → default)
# ════════════════════════════════════════════════════════════════════════

#: مفتاح إعداد النطاق الفرعي للعميل (الهدف الوحيد لكل السكربتات).
CLIENT_SUBDOMAIN_ENV = "HOBERADIUS_CLIENT_SUBDOMAIN"

#: منفذ SSTP الافتراضيّ (TCP/443 خلف شهادة Let's Encrypt حقيقية).
SSTP_PORT = 443

#: مفتاح إعداد منفذ SSTP — **نفس** مفتاح نفق الإدارة
#: (``router_mgmt_tunnel.ACCEL_SSTP_PORT_ENV``) لأنّ المستمع واحد: accel-ppp.
#: حين يُزاح accel عن :443 (مثلًا لتُخدَم اللوحة على :443 بلا منفذ في الرابط)
#: يجب أن تتبعه سكربتات اتصال البيانات، وإلّا ولّدنا عميل SSTP على منفذٍ
#: لا مستمع عليه.
SSTP_PORT_ENV = "HOBERADIUS_ACCEL_SSTP_PORT"

#: السرعة المعروضة للمستخدم — 5 ميجابت/ث (تطابق سقف accel-ppp).
DATA_SPEED_KBIT = accel_attributes.ACCEL_DEFAULT_KBIT  # 5120

#: أسماء الواجهات التي يُنشئها السكربت على مايكروتيك العميل (ASCII، ثابتة).
SSTP_IFACE_NAME = "hobe-data-sstp"
PPTP_IFACE_NAME = "hobe-data-pptp"
WG_IFACE_NAME = "hobe-data-wg"


class DataConnectionError(ValueError):
    """مدخلات غير صالحة لبناء اتصال البيانات."""


def client_subdomain() -> str:
    """النطاق الفرعي للعميل (الهدف الوحيد). فارغ = لم يُضبط بعد (LAB-PENDING)."""
    return str(env_settings.env(CLIENT_SUBDOMAIN_ENV, "") or "").strip()


def sstp_port() -> int:
    """منفذ SSTP الفعليّ (DB → env → 443). قيمة غير صالحة ⇒ الافتراضيّ."""
    try:
        port = int(str(env_settings.env(SSTP_PORT_ENV, SSTP_PORT)).strip())
    except (TypeError, ValueError):
        return SSTP_PORT
    return port if 1 <= port <= 65535 else SSTP_PORT


def _require_subdomain() -> str:
    host = client_subdomain()
    if not host:
        raise DataConnectionError(
            "لم يُضبط النطاق الفرعي للعميل بعد. اضبط "
            "HOBERADIUS_CLIENT_SUBDOMAIN في إعدادات النظام أولًا."
        )
    return host


# ════════════════════════════════════════════════════════════════════════
# تنقية المدخلات — السكربت يجب أن يكون ASCII نظيفًا وآمنًا من حقن RouterOS
# ════════════════════════════════════════════════════════════════════════

# RouterOS يقتبس القيم بعلامتي اقتباس مزدوجتين؛ نمنع أي محرف يكسر الاقتباس
# أو يحقن أمرًا (اقتباس/سطر جديد/فاصلة منقوطة/شرطة مائلة عكسية).
_FORBIDDEN_IN_QUOTED = re.compile(r'["\\\r\n;]')

# اسم اتصال/تعليق آمن: حروف لاتينية وأرقام وفراغ و . _ - ( ) فقط.
_ASCII_LABEL_STRIP = re.compile(r"[^A-Za-z0-9 ._()\-]")


def _safe_quoted(value: str, *, field: str) -> str:
    """يتحقّق أن قيمة ستوضع بين اقتباسين لا تحوي محارف خطرة."""
    v = str(value or "")
    if _FORBIDDEN_IN_QUOTED.search(v):
        raise DataConnectionError(f"قيمة غير صالحة للحقل {field!r} (محارف ممنوعة).")
    if not v.isascii():
        raise DataConnectionError(f"قيمة الحقل {field!r} يجب أن تكون ASCII.")
    return v


def ascii_comment(text: str, *, fallback: str = "HobeRadius DATA") -> str:
    """يحوّل اسمًا قد يكون عربيًا إلى تعليق ASCII آمن للسكربت."""
    cleaned = _ASCII_LABEL_STRIP.sub("", str(text or "")).strip()
    return cleaned or fallback


# ════════════════════════════════════════════════════════════════════════
# مولّدات السكربت — تُعيد سطر RouterOS واحدًا لكل بروتوكول
# ════════════════════════════════════════════════════════════════════════
#
# العيّنات المرجعية (المُتحقَّق منها) تُعاد حرفيًّا:
#   SSTP: /interface sstp-client add name="<conn>" connect-to=<host> port=443
#         user="<sub>" password="<pw>" profile=default-encryption
#         verify-server-certificate=yes [tls-version=only-1.2 على v7 فقط]
#         add-default-route=no disabled=no comment="<name>"
#   PPTP: /interface pptp-client add name="<conn>" connect-to=<host>
#         user="<sub>" password="<pw>" profile=default-encryption
#         add-default-route=no disabled=no comment="<name>"


def render_sstp_client(
    *, host: str, username: str, password: str, comment: str,
    version: int, conn_name: str = SSTP_IFACE_NAME,
    idempotent: bool = True,
) -> str:
    """سكربت عميل SSTP (v6 و v7). ``tls-version=only-1.2`` على v7 فقط.

    Idempotent بالافتراض: يُسبَق بـ``remove [find name=<ours>]`` فإعادة اللصق
    تَتقارب إلى عميل واحد نظيف بلا تكرار (مطابقة نمط كتلة WireGuard). يُمسَح
    باسم واجهتنا فقط (``hobe-data-sstp``، مختلف عن نفق الإدارة ``hr-sstp-mgmt``
    فلا يَلمسه). مرّر ``idempotent=False`` للمُستدعي الذي يَملك تنظيفه (ip_change)."""
    host = _safe_quoted(host, field="connect-to")
    name = _safe_quoted(conn_name, field="name")
    user = _safe_quoted(username, field="user")
    pw = _safe_quoted(password, field="password")
    cmt = _safe_quoted(comment, field="comment")
    tls = "tls-version=only-1.2 " if int(version) >= 7 else ""
    add = (
        f'/interface sstp-client add name="{name}" connect-to={host} '
        f'port={sstp_port()} user="{user}" password="{pw}" '
        f"profile=default-encryption verify-server-certificate=yes "
        f"{tls}add-default-route=no disabled=no "
        f'comment="{cmt}"'
    )
    if not idempotent:
        return add
    return f'/interface sstp-client remove [find name="{name}"]\n' + add


def render_pptp_client(
    *, host: str, username: str, password: str, comment: str,
    version: int = 6, conn_name: str = PPTP_IFACE_NAME,
    idempotent: bool = True,
) -> str:
    """سكربت عميل PPTP (v6 و v7 — لا اختلاف بالسطر).

    Idempotent بالافتراض: يُسبَق بـ``remove [find name=<ours>]`` (واجهتنا
    ``hobe-data-pptp`` فقط). مرّر ``idempotent=False`` لمُستدعٍ يَملك تنظيفه."""
    host = _safe_quoted(host, field="connect-to")
    name = _safe_quoted(conn_name, field="name")
    user = _safe_quoted(username, field="user")
    pw = _safe_quoted(password, field="password")
    cmt = _safe_quoted(comment, field="comment")
    add = (
        f'/interface pptp-client add name="{name}" connect-to={host} '
        f'user="{user}" password="{pw}" profile=default-encryption '
        f'add-default-route=no disabled=no comment="{cmt}"'
    )
    if not idempotent:
        return add
    return f'/interface pptp-client remove [find name="{name}"]\n' + add


def render_wireguard_client(
    *, host: str, wg_port: int, client_private_key: str, server_public_key: str,
    assigned_ip: str, comment: str, allowed_address: str = "0.0.0.0/0",
    keepalive_sec: int = 25, conn_name: str = WG_IFACE_NAME,
    idempotent: bool = True,
) -> str:
    """سكربت عميل WireGuard لمايكروتيك v7.

    ثلاثة أوامر: إنشاء واجهة wireguard، إضافة قرين يشير لنقطة WG على الـVPS،
    وإسناد عنوان النفق (من مجمّع WG لكل VPS) للواجهة. لا يضيف مسارًا
    افتراضيًا تلقائيًا — موازاةً لـ``add-default-route=no`` في SSTP/PPTP؛
    التوجيه/NAT يضبطه المشغّل يدويًا.

    Idempotent بالافتراض: يَمسح (peers + address + interface على واجهتنا
    ``hobe-data-wg``) قبل الإضافة فإعادة اللصق = حالة نظيفة واحدة بلا قرناء
    مكرّرين (نمط كتلة إدارة WireGuard ``hr-wg``، باسم مختلف فلا تعارض)."""
    host = _safe_quoted(host, field="endpoint-address")
    name = _safe_quoted(conn_name, field="name")
    priv = _safe_quoted(client_private_key, field="private-key")
    spub = _safe_quoted(server_public_key, field="server public-key")
    addr = _safe_quoted(assigned_ip, field="assigned_ip")
    allowed = _safe_quoted(allowed_address, field="allowed-address")
    cmt = _safe_quoted(comment, field="comment")
    port = int(wg_port)
    keepalive = int(keepalive_sec)
    cleanup = [
        # wipe our prior peers/address/interface (by interface/name) before
        # re-adding — duplicate WG peers with the same key break crypto-routing.
        f'/interface wireguard peers remove [find interface="{name}"]',
        f'/ip address remove [find interface="{name}"]',
        f'/interface wireguard remove [find name="{name}"]',
    ]
    add = [
        f'/interface wireguard add name="{name}" '
        f'private-key="{priv}" comment="{cmt}"',
        f'/interface wireguard peers add interface="{name}" '
        f'public-key="{spub}" endpoint-address={host} endpoint-port={port} '
        f'allowed-address={allowed} persistent-keepalive={keepalive}s '
        f'comment="{cmt}"',
        f'/ip address add address={addr}/32 interface="{name}"',
    ]
    return "\n".join((cleanup + add) if idempotent else add)


# ════════════════════════════════════════════════════════════════════════
# حارس التسرّب — لا CHR / بروكسي / لوحة / شبكات داخلية في السكربت النهائي
# ════════════════════════════════════════════════════════════════════════

#: بادئات/كلمات ممنوعة منعًا باتًّا في أي سكربت مُسلَّم للعميل. الهدف الوحيد
#: المسموح هو نطاق العميل الفرعي؛ أي ذكر لشبكات الإدارة/CHR/البروكسي تسرّب.
LEAKAGE_TOKENS = (
    "10.99.",   # شبكة بروكسي/أسطول قديمة
    "10.98.",
    "10.51.",
    "10.10.",   # نفق إدارة WireGuard المركزي
    "chr",      # أي ذكر لـ CHR
    "proxy",
    "vps_port_proxy",
)


def assert_no_leakage(script: str) -> None:
    """يرفع ``DataConnectionError`` إذا تسرّب أي عنوان/مفهوم داخلي.

    يُستدعى دائمًا قبل تسليم السكربت — وتُثبّته الاختبارات أيضًا. المقارنة
    غير حسّاسة لحالة الأحرف."""
    low = str(script or "").lower()
    for token in LEAKAGE_TOKENS:
        if token in low:
            raise DataConnectionError(
                f"تسرّب عنوان/مفهوم داخلي في السكربت: {token!r}"
            )


@dataclass(frozen=True)
class RenderedScript:
    """ناتج التوليد: نص السكربت + اسم الملف + بيانات وصفية للعرض."""
    version: int
    protocol: str          # sstp | pptp | wireguard
    filename: str
    script: str
    target_host: str
    speed_kbit: int


__all__ = [
    "DataConnectionError",
    "RenderedScript",
    "CLIENT_SUBDOMAIN_ENV",
    "DATA_SPEED_KBIT",
    "client_subdomain",
    "ascii_comment",
    "render_sstp_client",
    "render_pptp_client",
    "render_wireguard_client",
    "assert_no_leakage",
    "LEAKAGE_TOKENS",
]
