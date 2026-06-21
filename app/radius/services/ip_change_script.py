"""ip_change_script — مولّد سكربت «تغيير الـIP» الشامل لمايكروتيك (RouterOS).

المرحلة 3: عند تزويد العميل (بيانات SSTP + IP الخادم) يولّد هذا الملف سكربت
`.rsc` كامل بضغطة واحدة:

  • تنظيف   — يزيل أي آثار «تغيير IP» سابقة (idempotent، لا تكرار).
  • SSTP    — ينشئ عميل SSTP إلى الخادم المُزوَّد (يُعاد استخدام مولّد
              data_connection.render_sstp_client الآمن).
  • إعادة التوجيه — يمرّر **كل** حركة الراوتر عبر النفق فيصير الـIP العام =
              IP الخادم (مسار افتراضي عبر النفق + masquerade).
  • الحماية — مسار مضادّ للحلقة لعنوان الخادم عبر بوابة WAN الأصليّة، إبقاء
              جلسات الإدارة/winbox على WAN عبر routing-mark، إبقاء المسار
              الأصليّ failsafe (مسافة أعلى)، ومجدول «رجل ميّت» يُلغي النفق
              تلقائيًا بعد 5 دقائق ما لم يؤكّد المشغّل.
  • التحقّق — يقيس الـIP العام قبل/بعد عبر مُصدِّر IP خارجي ويُبلّغ نجاح/فشل.
  • التراجع — سكربت منفصل بنقرة يزيل كل الآثار ويُعيد المسار الأصليّ.
  • استبعاد الفيديو (اختياري) — يمرّر CDN فيديو (يوتيوب/تيك توك/فيسبوك) خارج
              النفق عبر address-list + mark-routing فيبقى البثّ على الاتصال
              الأصليّ.

نقيّ: يبني نصوصًا فقط (لا I/O، لا DB). يُعاد استخدام تنقية data_connection
وحارس التسرّب. RouterOS صالح (لا توكِن قالب خام).
"""
from __future__ import annotations

import re
from typing import Any

from . import data_connection as dc

# ── ثوابت الأسماء (ASCII ثابتة، مُوسومة كلها بـTAG للتنظيف) ──
IFACE = "hobe-ipchange-sstp"
TAG_BASE = "HOBERADIUS_IP_CHANGE"
BYPASS_MARK = "hobe-ipchange-bypass"        # routing-mark لتجاوز النفق (فيديو)
MGMT_MARK = "hobe-ipchange-mgmt"            # routing-mark لإبقاء الإدارة على WAN
VIDEO_LIST = "hobe-ipchange-video"          # address-list لمضيفي CDN الفيديو
FAILSAFE_NAME = "hobe-ipchange-failsafe"
FAILSAFE_MINUTES = 5
# مُصدِّر IP عام خارجي للتحقّق (بلا أي بنية داخلية — آمن من حارس التسرّب).
IP_ECHO_URL = "https://api.ipify.org/"

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# مضيفو CDN الفيديو (الخدمة فقط لا الموقع كاملًا). RouterOS يحلّ أسماء
# النطاقات في address-list ويتعقّبها ديناميكيًّا. (الحروف البديلة "*."
# غير مدعومة في address-list — نستعمل النطاق التمثيليّ لكل خدمة.)
VIDEO_CDN_HOSTS: tuple[tuple[str, str], ...] = (
    ("googlevideo.com",     "youtube"),     # يوتيوب — CDN الفيديو فقط
    ("tiktokcdn.com",       "tiktok"),
    ("tiktokv.com",         "tiktok"),
    ("tiktokcdn-us.com",    "tiktok"),
    ("byteoversea.com",     "tiktok"),
    ("video.xx.fbcdn.net",  "facebook"),     # فيسبوك — مدى فيديو fbcdn
    ("fbcdn.net",           "facebook"),
)


def _is_ipv4(host: str) -> bool:
    return bool(_IPV4_RE.match(str(host or "").strip()))


def _sstp_line(*, host: str, username: str, password: str, comment: str,
               version: int) -> str:
    """سطر عميل SSTP — يُعاد استخدام مولّد data_connection الآمن. عند الاتصال
    بعنوان IP خام (لا اسم نطاق بشهادة) نُطفئ التحقّق من الشهادة فقط."""
    line = dc.render_sstp_client(
        host=host, username=username, password=password, comment=comment,
        version=int(version), conn_name=IFACE,
    )
    if _is_ipv4(host):
        line = line.replace("verify-server-certificate=yes",
                            "verify-server-certificate=no")
    return line


def _cleanup_block(tag: str) -> list[str]:
    """يزيل آثار «تغيير IP» السابقة — يُنفَّذ أوّلًا (idempotent)."""
    return [
        ":log info \"IP-CHANGE: cleanup previous artifacts\"",
        f'/system scheduler remove [find where name="{FAILSAFE_NAME}"]',
        f'/ip firewall mangle remove [find where comment~"{tag}"]',
        f'/ip firewall nat remove [find where comment~"{tag}"]',
        f'/ip route remove [find where comment~"{tag}"]',
        f'/ip firewall address-list remove [find where list="{VIDEO_LIST}"]',
        f'/interface sstp-client remove [find where name="{IFACE}"]',
    ]


def _video_block(tag: str) -> list[str]:
    """استبعاد CDN الفيديو: address-list (يحلّه RouterOS) + mark-routing +
    مسار تجاوز عبر WAN، فيبقى البثّ خارج النفق."""
    out: list[str] = [
        ":log info \"IP-CHANGE: video-CDN exclusion ON\"",
    ]
    for host, svc in VIDEO_CDN_HOSTS:
        out.append(
            f'/ip firewall address-list add list="{VIDEO_LIST}" '
            f'address={host} comment="{tag}:video:{svc}"'
        )
    out += [
        f'/ip firewall mangle add chain=prerouting '
        f'dst-address-list="{VIDEO_LIST}" action=mark-routing '
        f'new-routing-mark="{BYPASS_MARK}" passthrough=no comment="{tag}:video"',
        f'/ip route add dst-address=0.0.0.0/0 gateway=$wanGw '
        f'routing-mark="{BYPASS_MARK}" comment="{tag}:video"',
    ]
    return out


def _apply_script(*, server_host: str, public_ip: str, username: str,
                  password: str, version: int, reference: str,
                  exclude_video: bool, speed_mbps: int | None) -> str:
    tag = f"{TAG_BASE}:{dc.ascii_comment(reference, fallback='default')}"
    cmt = dc.ascii_comment(f"HobeRadius IP-Change {reference}")
    # تنقية القيم المُقحَمة غير المقتبسة (الـIP/المضيف) ضد الحقن.
    safe_host = dc._safe_quoted(server_host, field="connect-to")
    safe_pub = dc._safe_quoted(public_ip, field="public_ip")
    spd = ("" if not speed_mbps else
           f"  · speed={int(speed_mbps)}Mbps")

    lines: list[str] = []
    lines.append(f"# ═══ HobeRadius — تغيير الـIP (تطبيق) — {reference}{spd} ═══")
    lines.append("# الصق هذا في تيرمنال المايكروتيك (New Terminal).")
    lines.append("")
    # (1) قياس الـIP العام قبل التغيير.
    lines.append(":global hobeIpBefore \"\"")
    lines.append(":do {")
    lines.append(f'  :local r [/tool fetch url="{IP_ECHO_URL}" mode=https '
                 f'check-certificate=no output=user as-value]')
    lines.append('  :set hobeIpBefore ($r->"data")')
    lines.append('} on-error={ :set hobeIpBefore "?" }')
    lines.append(':log info ("IP-CHANGE: before=" . $hobeIpBefore)')
    lines.append("")
    # (2) التقاط بوابة WAN الحاليّة (للمسار المضادّ للحلقة + failsafe).
    lines.append(":global wanGw \"\"")
    lines.append(":do {")
    lines.append('  :set wanGw [/ip route get '
                 '[find where dst-address="0.0.0.0/0" and active=yes] gateway]')
    lines.append('} on-error={ :set wanGw "" }')
    lines.append(":if ([:len $wanGw] = 0) do={")
    lines.append('  :log error "IP-CHANGE: no active default route — abort"')
    lines.append('  :error "no active WAN default route"')
    lines.append("}")
    lines.append("")
    # (3) تنظيف.
    lines += _cleanup_block(tag)
    lines.append("")
    # (4) NTP (شهادة SSTP تتطلّب ساعة صحيحة).
    lines.append("/system ntp client set enabled=yes")
    lines.append("")
    # (5) عميل SSTP إلى الخادم المُزوَّد.
    lines.append(_sstp_line(host=safe_host, username=username,
                            password=password, comment=cmt, version=version))
    lines.append("")
    # (6) حماية: مسار مضادّ للحلقة لعنوان الخادم عبر WAN الأصليّة.
    lines.append(f'/ip route add dst-address={safe_pub}/32 gateway=$wanGw '
                 f'distance=1 comment="{tag}:anti-loop"')
    # رفع مسافة المسار الافتراضيّ الأصليّ ليصبح failsafe (يبقى لكن أقلّ أفضليّة).
    lines.append('/ip route set [find where dst-address="0.0.0.0/0" '
                 'and gateway=$wanGw and !routing-mark] distance=2')
    lines.append("")
    # (7) إعادة التوجيه: المسار الافتراضيّ عبر النفق + masquerade.
    lines.append(f'/ip route add dst-address=0.0.0.0/0 gateway="{IFACE}" '
                 f'distance=1 comment="{tag}:default"')
    lines.append(f'/ip firewall nat add chain=srcnat out-interface="{IFACE}" '
                 f'action=masquerade comment="{tag}:nat"')
    lines.append("")
    # (8) حماية الإدارة: ردّ جلسات WAN الواردة يخرج عبر WAN لا النفق
    #     (يعتمد على قائمة الواجهات الافتراضيّة WAN؛ يُتجاوز بأمان لو غابت).
    lines.append(":do {")
    lines.append('  /ip firewall mangle add chain=input '
                 'connection-state=new in-interface-list=WAN '
                 f'action=mark-connection new-connection-mark="{MGMT_MARK}" '
                 f'passthrough=yes comment="{tag}:mgmt"')
    lines.append('  /ip firewall mangle add chain=output '
                 f'connection-mark="{MGMT_MARK}" action=mark-routing '
                 f'new-routing-mark="{MGMT_MARK}" passthrough=no '
                 f'comment="{tag}:mgmt"')
    lines.append(f'  /ip route add dst-address=0.0.0.0/0 gateway=$wanGw '
                 f'routing-mark="{MGMT_MARK}" comment="{tag}:mgmt"')
    lines.append('} on-error={ :log warning '
                 '"IP-CHANGE: WAN interface-list missing — skipped mgmt guard" }')
    lines.append("")
    # (9) استبعاد فيديو CDN (اختياري).
    if exclude_video:
        lines += _video_block(tag)
        lines.append("")
    # (10) فيلسيف «رجل ميّت»: يُطفئ النفق بعد N دقيقة ما لم تؤكّد بإزالة المجدول.
    #      on-event بلا اقتباسات داخليّة (اسم الواجهة وسيط مباشر للأمر).
    lines.append(f'/system scheduler add name="{FAILSAFE_NAME}" '
                 f'interval={FAILSAFE_MINUTES}m comment="{tag}:failsafe" '
                 f'on-event="/interface sstp-client disable {IFACE}"')
    lines.append(f':put "FAILSAFE: سيُطفأ النفق تلقائيًا بعد {FAILSAFE_MINUTES} '
                 f'دقائق ما لم تؤكّد بقاءه. بعد التأكّد أنّ اتصالك سليم نفّذ:"')
    lines.append(f':put "    /system scheduler remove {FAILSAFE_NAME}"')
    lines.append("")
    # (11) تحقّق: قياس الـIP بعد التغيير ومقارنته.
    lines.append(":delay 8s")
    lines.append(":global hobeIpAfter \"\"")
    lines.append(":do {")
    lines.append(f'  :local r2 [/tool fetch url="{IP_ECHO_URL}" mode=https '
                 f'check-certificate=no output=user as-value]')
    lines.append('  :set hobeIpAfter ($r2->"data")')
    lines.append('} on-error={ :set hobeIpAfter "?" }')
    lines.append(':log info ("IP-CHANGE: after=" . $hobeIpAfter)')
    lines.append(':put ("IP قبل: " . $hobeIpBefore)')
    lines.append(':put ("IP بعد: " . $hobeIpAfter)')
    lines.append(f':if ($hobeIpAfter = "{safe_pub}") do={{')
    lines.append('  :put "النتيجة: نجح ✓ — الـIP العام صار IP الخادم."')
    lines.append("}")
    lines.append(f':if (($hobeIpAfter != "{safe_pub}") and '
                 '($hobeIpAfter != $hobeIpBefore) and '
                 '($hobeIpAfter != "?")) do={')
    lines.append('  :put "النتيجة: تغيّر الـIP لكن ليس IP الخادم المتوقّع — راجع التزويد."')
    lines.append("}")
    lines.append(':if (($hobeIpAfter = $hobeIpBefore) or ($hobeIpAfter = "?")) do={')
    lines.append('  :put "النتيجة: فشل ✗ — لم يتغيّر الـIP. شغّل سكربت التراجع وراجع الفحوص أدناه."')
    lines.append("}")
    lines.append("")
    # (12) فحوص صحّة/استكشاف أخطاء.
    lines.append("# ── فحوص صحّة (استكشاف الأخطاء) ──")
    lines.append(f'/interface sstp-client print detail where name="{IFACE}"')
    lines.append(f'/ip route print detail where comment~"{TAG_BASE}"')
    lines.append('/ping 1.1.1.1 count=4')
    return "\n".join(lines)


def _rollback_script(*, public_ip: str, reference: str) -> str:
    tag = f"{TAG_BASE}:{dc.ascii_comment(reference, fallback='default')}"
    safe_pub = dc._safe_quoted(public_ip, field="public_ip")
    lines: list[str] = []
    lines.append(f"# ═══ HobeRadius — تغيير الـIP (تراجع) — {reference} ═══")
    lines.append("# يزيل كل آثار «تغيير IP» ويُعيد المسار الأصليّ.")
    lines.append("")
    lines.append(":global wanGw \"\"")
    lines.append(":do {")
    lines.append('  :set wanGw [/ip route get '
                 '[find where dst-address="0.0.0.0/0" and active=yes '
                 'and !routing-mark] gateway]')
    lines.append('} on-error={ :set wanGw "" }')
    lines.append("")
    lines.append(f'/system scheduler remove [find where name="{FAILSAFE_NAME}"]')
    lines.append(f'/ip firewall mangle remove [find where comment~"{tag}"]')
    lines.append(f'/ip firewall nat remove [find where comment~"{tag}"]')
    lines.append(f'/ip route remove [find where comment~"{tag}"]')
    lines.append(f'/ip firewall address-list remove [find where list="{VIDEO_LIST}"]')
    lines.append(f'/interface sstp-client remove [find where name="{IFACE}"]')
    # إعادة مسافة المسار الأصليّ إلى 1 (كان مرفوعًا إلى 2 كـfailsafe).
    lines.append(':if ([:len $wanGw] > 0) do={')
    lines.append('  /ip route set [find where dst-address="0.0.0.0/0" '
                 'and gateway=$wanGw and !routing-mark] distance=1')
    lines.append("}")
    lines.append(':put "تمّ التراجع — استُعيد المسار الأصليّ."')
    lines.append('/ping 1.1.1.1 count=4')
    return "\n".join(lines)


def generate(*, server_host: str, public_ip: str, username: str,
             password: str, version: int = 7, reference: str = "default",
             exclude_video: bool = False,
             speed_mbps: int | None = None) -> dict[str, Any]:
    """يولّد سكربتَي التطبيق + التراجع ويتحقّق من سلامتهما.

    server_host: عنوان الاتصال بالخادم (اسم نطاق بشهادة، أو IP خام).
    public_ip:   الـIP العام الجديد المتوقَّع (للمقارنة في التحقّق + العرض).
    """
    if not (server_host and username and password and public_ip):
        raise dc.DataConnectionError("بيانات التزويد ناقصة لتوليد السكربت.")
    apply_script = _apply_script(
        server_host=server_host, public_ip=public_ip, username=username,
        password=password, version=int(version), reference=reference,
        exclude_video=bool(exclude_video), speed_mbps=speed_mbps)
    rollback_script = _rollback_script(public_ip=public_ip, reference=reference)
    # حارس التسرّب (لا بنية داخليّة) + حارس عدم تسرّب توكِن قالب خام.
    dc.assert_no_leakage(apply_script)
    dc.assert_no_leakage(rollback_script)
    assert_no_raw_token(apply_script)
    assert_no_raw_token(rollback_script)
    return {
        "apply_script": apply_script,
        "rollback_script": rollback_script,
        "apply_filename": f"ip-change-{dc.ascii_comment(reference, fallback='default')}.rsc",
        "rollback_filename": f"ip-change-rollback-{dc.ascii_comment(reference, fallback='default')}.rsc",
        "iface": IFACE,
        "failsafe_minutes": FAILSAFE_MINUTES,
        "ip_echo_url": IP_ECHO_URL,
        "exclude_video": bool(exclude_video),
        "video_hosts": [h for h, _ in VIDEO_CDN_HOSTS],
    }


# توكِن قالب خام = نمط بايثون غير مُحلّل مثل {server_ip} أو ${x} أو <iface>.
# RouterOS الشرعيّ يستعمل do={ \n ... } و on-error={} و {}، فلا يُطابق هذا.
_RAW_TOKEN_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}|\$\{|<[a-z_]+>")


def assert_no_raw_token(script: str) -> None:
    """يرفع DataConnectionError إذا بقي توكِن قالب خام غير مُحلّل."""
    m = _RAW_TOKEN_RE.search(str(script or ""))
    if m:
        raise dc.DataConnectionError(
            f"توكِن قالب خام غير مُحلّل في السكربت: {m.group(0)!r}")


__all__ = [
    "IFACE", "TAG_BASE", "VIDEO_LIST", "VIDEO_CDN_HOSTS", "IP_ECHO_URL",
    "FAILSAFE_NAME", "generate", "assert_no_raw_token",
]
