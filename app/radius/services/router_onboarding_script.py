"""HobeRadius — one-paste RouterOS onboarding script generator (phase 1).

Produces a COMPLETE RouterOS setup script for a single router/customer from the
router's real data + panel settings. It is HobeRadius-native by construction —
our ``hr-``/``hobe-`` naming, our bilingual (AR/EN) comment voice, our section
order. It is **not** modelled on any third-party script's text/structure; an
external "advanced" script is only a checklist of WHICH capabilities to cover.

Two invariants drive the design:

1. **Firewall ordering is sacred.** RouterOS filter is first-match, top-down.
   The provider management path (the SSTP tunnel interface + the route to our
   RADIUS) can NEVER be locked out by a generated rule. So every ruleset puts,
   in this exact order: established/related → the mgmt SSTP interface → our
   RADIUS server → DNS → the walled-garden allow-list, THEN expiry/limit
   handling (reject the expired pool, leaving the walled garden reachable),
   THEN safe defaults. We rebuild our managed block idempotently and MOVE it to
   the top of each chain so re-pasting never reorders or duplicates.

2. **Idempotent.** Every add is guarded (``find`` existence checks) or the
   managed block is removed-then-rebuilt, so re-pasting the whole script is a
   no-op that converges to the same state.

Secrets are UNIQUE per router (the rtr- tunnel password from radcheck and the
per-NAS RADIUS secret) — never a shared constant.

This module is pure (string in → string out); the route layer gathers the
params from nas_devices + radcheck/radreply + settings.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import List

# Reuse the vetted RouterOS injection-safety helpers (single source).
from .data_connection import _safe_quoted, ascii_comment

#: RouterOS object names we own (stable, ASCII). The SSTP iface matches the
#: name already provisioned by router_mgmt_tunnel (hr-sstp-mgmt).
MGMT_IFACE_DEFAULT = "hr-sstp-mgmt"
PPP_PROFILE = "hr-mgmt-profile"
WALLED_GARDEN_LIST = "hr-walled-garden"
EXPIRED_LIST = "hr-pool-expired"
API_USER_DEFAULT = "hobe-api"
FW_TAG = "hr-fw:"            # every managed filter rule comment starts with this
NAT_TAG = "hr-nat:"


class OnboardingScriptError(ValueError):
    """Bad/unsafe parameters for the onboarding script."""


@dataclass
class OnboardingParams:
    """Everything the script needs — all from the router's real data + panel
    settings, never hardcoded constants."""

    router_name: str
    router_id: int
    accel_host: str                 # public SSTP server address
    sstp_port: int
    tunnel_user: str                # rtr-<slug>
    tunnel_password: str            # UNIQUE per router (radcheck Cleartext)
    tunnel_ip: str                  # router's fixed tunnel IP (radreply Framed-IP)
    radius_ip: str                  # our RADIUS, reachable over the tunnel (gateway)
    radius_secret: str              # UNIQUE per-NAS secret (nas_devices.secret)
    api_user: str
    api_password: str               # UNIQUE per router
    walled_garden: List[str] = field(default_factory=list)  # domains/IPs allow-list
    block_page_url: str = ""        # configurable placeholder (page hosted in phase 2)
    hotspot_pool: str = "10.5.50.0/24"
    pppoe_pool: str = "10.5.60.0/24"
    mgmt_iface: str = MGMT_IFACE_DEFAULT
    coa_port: int = 3799

    def validate(self) -> None:
        # Hard-fail on anything that would inject into a quoted RouterOS value.
        for name in ("accel_host", "tunnel_user", "tunnel_password", "tunnel_ip",
                     "radius_ip", "radius_secret", "api_user", "api_password",
                     "mgmt_iface", "block_page_url"):
            _safe_quoted(getattr(self, name), field=name)
        for entry in self.walled_garden:
            _safe_quoted(entry, field="walled_garden")
        if not self.tunnel_user.startswith("rtr-"):
            raise OnboardingScriptError("tunnel_user يجب أن يبدأ بـrtr-")
        if not self.tunnel_password or len(self.tunnel_password) < 12:
            raise OnboardingScriptError("كلمة مرور النفق ضعيفة/مفقودة")
        if not self.radius_secret or len(self.radius_secret) < 8:
            raise OnboardingScriptError("سرّ RADIUS ضعيف/مفقود")


# ─── small builders ───────────────────────────────────────────────────────

def _hdr(ar: str, en: str) -> str:
    """A bilingual section header in our voice."""
    bar = "# " + "─" * 70
    return f"{bar}\n# {ar}\n# {en}\n{bar}"


def _q(value: str, *, field: str) -> str:
    return _safe_quoted(value, field=field)


def _url_host(url: str) -> str:
    """Bare host (IP or domain) from a URL — scheme/port/path stripped. Used to
    add the block page to the walled garden + as the dst-nat redirect target."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]          # drop path
    raw = raw.split("@", 1)[-1]         # drop userinfo
    raw = raw.split(":", 1)[0]          # drop port
    return raw.strip()


# ─── sections ──────────────────────────────────────────────────────────────

def _section_banner(p: OnboardingParams) -> str:
    name = ascii_comment(p.router_name, fallback=f"router-{p.router_id}")
    return (
        "# ═══════════════════════════════════════════════════════════════════\n"
        "# HobeRadius — سكربت تهيئة الراوتر بضغطة واحدة\n"
        "# HobeRadius — one-paste router onboarding script\n"
        f"# الراوتر | Router: {name}  (#{p.router_id})\n"
        "# الصق هذا كاملًا مرّة واحدة في Terminal — كل سطر آمن لإعادة اللصق.\n"
        "# Paste this whole block once in Terminal — every line is re-paste safe.\n"
        "# ═══════════════════════════════════════════════════════════════════"
    )


def _section_tunnel(p: OnboardingParams) -> str:
    """PPP profile + SSTP mgmt client (Profile=default, verify-cert off) + the
    route to our RADIUS over the tunnel. Idempotent (find-guarded)."""
    host = _q(p.accel_host, field="accel_host")
    user = _q(p.tunnel_user, field="tunnel_user")
    pw = _q(p.tunnel_password, field="tunnel_password")
    iface = _q(p.mgmt_iface, field="mgmt_iface")
    radius_ip = _q(p.radius_ip, field="radius_ip")
    return "\n".join([
        _hdr("١) نفق الإدارة SSTP — المسار الذي نُدير منه الراوتر",
             "1) SSTP management tunnel — the path we manage the router over"),
        "# Profile=default عمدًا (NOT default-encryption): SSTP مُشفّر TLS سلفًا،",
        "# و MPPE فوقه يكسر الرابط. verify-server-certificate=no: شهادة موقّعة ذاتيًّا.",
        "# verify-server-address-from-certificate=no: نتّصل بالـIP وشهادتنا CN=اسم",
        "# لا IP، فلو بقي =yes (الافتراضي) تفشل إعادة التحقّق دوريًّا ويرفّ النفق.",
        f'/ppp profile remove [find name="{PPP_PROFILE}"]',
        f'/ppp profile add name="{PPP_PROFILE}" use-encryption=no use-mpls=no '
        f'comment="hr: mgmt tunnel profile"',
        f'/interface sstp-client remove [find name="{iface}"]',
        f'/interface sstp-client add name="{iface}" connect-to={host} '
        f'port={int(p.sstp_port)} user="{user}" password="{pw}" profile=default '
        f'verify-server-certificate=no verify-server-address-from-certificate=no '
        f'add-default-route=no disabled=no '
        f'keepalive-timeout=30 comment="hr: SSTP mgmt to HobeRadius"',
        "# مسار صريح إلى خادم RADIUS عبر النفق (لا يعتمد على المسار الافتراضي).",
        "# Explicit route to our RADIUS over the tunnel (never via the default route).",
        f'/ip route remove [find comment="hr: route to RADIUS"]',
        f'/ip route add dst-address={radius_ip}/32 gateway="{iface}" '
        f'distance=1 comment="hr: route to RADIUS"',
    ])


def _section_radius(p: OnboardingParams) -> str:
    """RADIUS for hotspot + PPPoE + accounting + incoming CoA (3799)."""
    radius_ip = _q(p.radius_ip, field="radius_ip")
    secret = _q(p.radius_secret, field="radius_secret")
    return "\n".join([
        _hdr("٢) RADIUS — مصادقة الهوتسبوت و PPPoE والمحاسبة و CoA الوارد",
             "2) RADIUS — hotspot + PPPoE auth, accounting, and incoming CoA"),
        "# السرّ فريد لهذا الراوتر (ليس ثابتًا مشتركًا). يُصادَق عبر النفق فقط.",
        "# The secret is UNIQUE to this router (not a shared constant).",
        f'/radius remove [find comment="hr: HobeRadius RADIUS"]',
        f'/radius add address={radius_ip} secret="{secret}" '
        f'service=hotspot,ppp,login src-address={p.tunnel_ip} '
        f'timeout=3000ms comment="hr: HobeRadius RADIUS"',
        "# تفعيل استخدام RADIUS للهوتسبوت و PPPoE.",
        "/ip hotspot profile set [find default=yes] use-radius=yes",
        "/ppp aaa set use-radius=yes accounting=yes interim-update=5m",
        "# CoA/Disconnect الوارد على 3799 — يَقبل فقط من خادمنا (جدار ناريّ أدناه).",
        "# Incoming CoA/Disconnect on 3799 — only from our server (firewalled below).",
        f"/radius incoming set accept=yes port={int(p.coa_port)}",
    ])


def _pool_range(cidr: str) -> str:
    """CIDR → RouterOS pool range, skipping the network + the first host
    (reserved as the gateway). e.g. 10.5.50.0/24 → 10.5.50.2-10.5.50.254."""
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = list(net.hosts())
    if len(hosts) < 3:
        raise OnboardingScriptError(f"مجمّع صغير جدًّا: {cidr}")
    return f"{hosts[1]}-{hosts[-1]}"


def _section_pools(p: OnboardingParams) -> str:
    """IP pools for hotspot + PPPoE (interface-agnostic). RADIUS assigns from
    these via the Framed-Pool reply; idempotent."""
    try:
        hs = _pool_range(p.hotspot_pool)
        pppoe = _pool_range(p.pppoe_pool)
    except (ValueError, OnboardingScriptError) as exc:
        raise OnboardingScriptError(f"مجمّع غير صالح: {exc}") from exc
    return "\n".join([
        _hdr("٣) مجمّعات العناوين — الهوتسبوت و PPPoE (يُسنِدها RADIUS)",
             "3) Address pools — hotspot + PPPoE (assigned by RADIUS)"),
        f'/ip pool remove [find name="hr-hotspot-pool"]',
        f'/ip pool add name="hr-hotspot-pool" ranges={hs} '
        f'comment="hr: hotspot pool"',
        f'/ip pool remove [find name="hr-pppoe-pool"]',
        f'/ip pool add name="hr-pppoe-pool" ranges={pppoe} '
        f'comment="hr: PPPoE pool"',
    ])


def _section_api_user(p: OnboardingParams) -> str:
    """API user restricted to our tunnel/RADIUS source IP only."""
    api_user = _q(p.api_user, field="api_user")
    api_pw = _q(p.api_password, field="api_password")
    radius_ip = _q(p.radius_ip, field="radius_ip")
    return "\n".join([
        _hdr("٤) مستخدم API مقصور على مصدر النفق وحده",
             "4) API user locked to the tunnel source address only"),
        "# يُسمح بهذا المستخدم فقط من عنوان خادمنا داخل النفق — لا وصول من الإنترنت.",
        "# This user is allowed ONLY from our server's tunnel IP — never the internet.",
        f'/user remove [find name="{api_user}"]',
        f'/user add name="{api_user}" password="{api_pw}" group=full '
        f'address={radius_ip}/32 comment="hr: panel API user (tunnel-only)"',
    ])


def _section_firewall(p: OnboardingParams) -> str:
    """THE critical section — see module docstring. Builds address-lists, then
    rebuilds our managed filter block idempotently and moves it to the TOP of
    each chain so the provider mgmt path can never be locked out."""
    iface = _q(p.mgmt_iface, field="mgmt_iface")
    radius_ip = _q(p.radius_ip, field="radius_ip")

    lines: List[str] = [
        _hdr("٥) الجدار الناريّ — الترتيب هو الأهمّ (مطابقة أوّل-تطابق من الأعلى)",
             "5) Firewall — ORDER IS EVERYTHING (first-match, top-down)"),
        "# المبدأ: مسار الإدارة (نفق SSTP + RADIUS) لا يُحجب أبدًا. قواعد السماح",
        "# أوّلًا، ثم معالجة الانتهاء/الحدّ، ثم الافتراضات الآمنة. نُعيد بناء كتلتنا",
        "# المُدارة (hr-fw:) ثم نرفعها إلى رأس كل سلسلة كي لا تُعاد ترتيبًا أو تُكرَّر.",
        "# Principle: the mgmt path (SSTP + RADIUS) is NEVER blocked. Allow rules",
        "# first, then expiry/limit handling, then safe defaults. We rebuild our",
        "# managed (hr-fw:) block and lift it to the top of each chain.",
        "",
        "# ─ قوائم العناوين | address-lists ─",
        f'/ip firewall address-list remove [find list="{WALLED_GARDEN_LIST}" comment="hr-wg"]',
    ]
    # Walled-garden allow-list: our domains/IPs + always the RADIUS + SSTP
    # server + the block-page host (so an expired user can reach the renewal
    # page; the dst-nat below redirects their HTTP to exactly this host).
    block_host = _url_host(p.block_page_url)
    wg = list(dict.fromkeys(
        [p.radius_ip, p.accel_host] + ([block_host] if block_host else [])
        + list(p.walled_garden)))
    for entry in wg:
        e = _q(entry, field="walled_garden")
        lines.append(
            f'/ip firewall address-list add list="{WALLED_GARDEN_LIST}" '
            f'address={e} comment="hr-wg"')
    lines += [
        "# قائمة المنتهين/المحدودين (hr-pool-expired) لا تُبذَر هنا عمدًا — يملؤها",
        "# RADIUS ديناميكيًّا عبر Mikrotik-Address-List لكل مشترك منتهٍ. القاعدة",
        "# أدناه تشير إليها بأمان حتى لو كانت فارغة (لا حجب لأحد قبل أن يضعه RADIUS).",
        "# the expired/limited pool is NOT seeded here — RADIUS fills it per expired",
        "# subscriber (Mikrotik-Address-List). The rule references it safely even",
        "# when empty (nobody is blocked until RADIUS lists them).",
        "",
        "# ─ نزع كتلتنا المُدارة (إعادة اللصق لا تُكرّر) | drop our managed rules ─",
        f'/ip firewall filter remove [find comment~"^{FW_TAG}"]',
        "",
    ]

    # The ORDERED rule set. Each tuple: (chain, comment-suffix, args, rationale).
    # NB: add-order == match-order after the move-to-top step below.
    rules = [
        # ── INPUT (traffic TO the router) — allow the mgmt path, never drop it ──
        ("input", "01 established/related",
         "connection-state=established,related action=accept",
         "الجلسات القائمة لا تُقطع | never break live sessions"),
        ("input", "02 mgmt SSTP iface",
         f'in-interface="{iface}" action=accept',
         "مسار الإدارة المطلق — أعلى أولويّة | the absolute mgmt path"),
        ("input", "03 from RADIUS server",
         f"src-address={radius_ip} action=accept",
         "RADIUS + CoA الوارد من خادمنا فقط | RADIUS/CoA from our server only"),
        ("input", "04 DNS to router",
         "protocol=udp dst-port=53 action=accept",
         "استعلامات DNS للعملاء | client DNS lookups"),
        ("input", "05 DNS to router tcp",
         "protocol=tcp dst-port=53 action=accept",
         "DNS عبر TCP | DNS over TCP"),
        ("input", "06 ICMP diag",
         "protocol=icmp action=accept",
         "تشخيص/بنج الإدارة | mgmt ping/diagnostics"),
        # ── FORWARD (subscriber traffic THROUGH the router) ──
        ("forward", "10 established/related",
         "connection-state=established,related action=accept",
         "الجلسات القائمة | live sessions"),
        ("forward", "11 walled-garden allow",
         f'dst-address-list="{WALLED_GARDEN_LIST}" action=accept',
         "السماح للحديقة المسوّرة دائمًا (صفحة التجديد/خوادمنا) | walled garden always reachable"),
        ("forward", "12 mgmt tunnel",
         f'out-interface="{iface}" action=accept',
         "حركة الإدارة عبر النفق | mgmt traffic over the tunnel"),
        ("forward", "13 to RADIUS server",
         f"dst-address={radius_ip} action=accept",
         "الوصول لخادم RADIUS | reach the RADIUS server"),
        ("forward", "14 DNS forward",
         "protocol=udp dst-port=53 action=accept",
         "DNS للحديقة المسوّرة حتى عند الحدّ | DNS even when limited"),
        # ── expiry/limit handling — AFTER the allow rules ──
        ("forward", "20 expired pool reject",
         f'src-address-list="{EXPIRED_LIST}" action=reject '
         "reject-with=icmp-network-unreachable",
         "المنتهون: مرفوضون لكل شيء عدا الحديقة المسوّرة (المسموحة أعلاه) | "
         "expired: rejected except the walled garden allowed above"),
        # ── safe defaults — LAST ──
        ("forward", "99 default active accept",
         "action=accept",
         "المشتركون الفاعلون: الإنترنت مسموح | active subscribers: internet allowed"),
    ]
    for chain, suffix, args, why in rules:
        comment = f"{FW_TAG} {suffix}"
        lines.append(f"# {why}")
        lines.append(
            f'/ip firewall filter add chain={chain} {args} comment="{comment}"')

    lines += [
        "",
        "# ─ رفع كتلتنا إلى رأس كل سلسلة بالترتيب (الأولويّة المطلقة لمسار الإدارة) ─",
        "# ─ lift our block to the top of each chain, preserving order ─",
        # ONE console line: the :local MUST share the line with the :foreach that
        # uses $hrPos. RouterOS scopes a :local to its own console command, so a
        # separate `:local hrPos 0` line goes out of scope before the next pasted
        # line runs → "syntax error" on $hrPos. Semicolon-joining survives a
        # line-by-line paste. (Verified on RouterOS 7.20.6 / CCR1009.)
        (f':local hrPos 0; :foreach r in='
         f'[/ip firewall filter find comment~"^{FW_TAG}"] '
         f'do={{ /ip firewall filter move $r destination=$hrPos; '
         f':set hrPos ($hrPos + 1) }}'),
    ]
    return "\n".join(lines)


def _section_block_redirect(p: OnboardingParams) -> str:
    """Redirect expired-pool HTTP to the «انتهى اشتراكك» page (phase 2 — ENABLED).

    dst-nat the expired pool's port-80 traffic to the block-page host. After the
    rewrite the destination IS the block-page host, which the walled-garden allow
    rule (filter 11) accepts BEFORE the expired-reject (filter 20) — so the page
    loads while everything else stays blocked. Only HTTP (:80) is redirected;
    HTTPS can't be transparently intercepted (cert mismatch), so it's left to be
    rejected — the page must be reached over HTTP."""
    url = ascii_comment(p.block_page_url, fallback="block-page-not-set")
    host = _url_host(p.block_page_url)
    is_ipv4 = bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host))
    lines = [
        _hdr("٦) إعادة توجيه المنتهين لصفحة «انتهى اشتراكك» (HTTP فقط)",
             "6) Redirect expired users to the «subscription expired» page (HTTP only)"),
        f"# صفحة التجديد المُهيّأة | configured renew page: {url}",
        f'/ip firewall nat remove [find comment~"^{NAT_TAG}"]',
    ]
    if not p.block_page_url:
        lines.append("# (لم تُضبط صفحة | no block page set — no redirect rule)")
    elif not is_ipv4:
        # dst-nat to-addresses must be an IP. With a domain we cannot NAT, but
        # the host is already in the walled garden so the user can still open it.
        lines += [
            f"# مضيف الصفحة '{host}' ليس IPv4 — تعذّر dst-nat (يحتاج IP). الصفحة",
            "# تبقى في الحديقة المسوّرة فيمكن للمشترك فتحها يدويًّا. اضبط الرابط",
            "# بعنوان IP للحصول على إعادة التوجيه التلقائيّة.",
            f"# block-page host '{host}' is not an IPv4 — dst-nat skipped (needs an",
            "# IP). The page stays walled-garden-reachable; set the URL to an IP",
            "# for automatic redirect.",
        ]
    else:
        hq = _q(host, field="block_page_url")
        lines += [
            "# إعادة توجيه HTTP(80) للمنتهين إلى صفحة التجديد. مُفعّل.",
            "# redirect expired HTTP(80) to the renew page. ENABLED.",
            f'/ip firewall nat add chain=dstnat protocol=tcp dst-port=80 '
            f'src-address-list="{EXPIRED_LIST}" action=dst-nat '
            f'to-addresses={hq} to-ports=80 '
            f'comment="{NAT_TAG} expired http -> renew page"',
        ]
    return "\n".join(lines)


def _section_service_lockdown(p: OnboardingParams) -> str:
    """Disable telnet/ftp/ssh/api-ssl; restrict the rest to the mgmt source."""
    radius_ip = _q(p.radius_ip, field="radius_ip")
    return "\n".join([
        _hdr("٧) تقليص الخدمات — أغلق ما لا نحتاجه، وقيّد الباقي بمصدر النفق",
             "7) Service lockdown — disable the unneeded, bind the rest to the tunnel"),
        "/ip service disable telnet,ftp,ssh,api-ssl",
        "# الخدمات المُبقاة تُقصَر على عنوان خادمنا داخل النفق.",
        "# kept services are bound to our server's tunnel address.",
        f'/ip service set api address={radius_ip}/32',
        f'/ip service set winbox address={radius_ip}/32',
        f'/ip service set www address={radius_ip}/32',
    ])


def _section_self_heal(p: OnboardingParams) -> str:
    """Self-heal that recovers a TRULY-dead tunnel but NEVER bounces a healthy
    one (the flapping bug: a single missed 2s probe used to disable/enable a
    perfectly-running interface and cascade).

    Two conservative, state-guarded actors:

      • Watchdog scheduler (startup + every 2m): existence/enabled only. Missing
        → log. Disabled → enable. **Running/connecting → do nothing.** It never
        disables and never touches a running interface.

      • netwatch on the RADIUS IP (timeout 5s, so a latency blip ≠ down): its
        down-script is GUARDED by the actual interface state — it re-enables only
        if the interface is disabled, and bounces (disable→delay→enable) ONLY if
        the interface is enabled but ``running=false`` (genuinely dead). If the
        interface is running, it does nothing, so a transient RADIUS blip can
        never flap a working tunnel.
    """
    iface = _q(p.mgmt_iface, field="mgmt_iface")
    radius_ip = _q(p.radius_ip, field="radius_ip")

    # Watchdog: ensure the client EXISTS and is ENABLED. Never bounces a running
    # interface (no `disable` here at all).
    watchdog = (
        f':local id [/interface sstp-client find name=\\"{iface}\\"]; '
        f':if ([:len $id]=0) '
        f'do={{:log warning \\"hr: SSTP mgmt missing — re-paste onboarding\\"}} '
        f'else={{:if ([/interface sstp-client get $id disabled]=true) '
        f'do={{/interface sstp-client enable $id}}}}'
    )
    # netwatch down-script: act ONLY on genuine failure. Re-enable if disabled;
    # bounce ONLY if enabled-but-not-running; do NOTHING if running.
    reheal = (
        f':local id [/interface sstp-client find name=\\"{iface}\\"]; '
        f':if ([:len $id]>0) do={{'
        f':if ([/interface sstp-client get $id disabled]=true) '
        f'do={{/interface sstp-client enable $id}} '
        f'else={{:if ([/interface sstp-client get $id running]=false) '
        f'do={{/interface sstp-client disable $id; :delay 3s; '
        f'/interface sstp-client enable $id}}}}'
        f'}}'
    )
    return "\n".join([
        _hdr("٨) الإصلاح الذاتيّ — يستعيد نفقًا ميّتًا فعلًا ولا يَرُفّ سليمًا",
             "8) Self-heal — recovers a dead tunnel, never flaps a healthy one"),
        "# الجدول: يضمن وجود العميل وتفعيله فقط — لا يَلمس واجهة شغّالة إطلاقًا.",
        "# Watchdog: ensure the client exists + is enabled. Never touches a running one.",
        f'/system scheduler remove [find name="hr-sstp-watchdog"]',
        f'/system scheduler add name="hr-sstp-watchdog" interval=2m '
        f'start-time=startup on-event="{watchdog}" '
        f'comment="hr: ensure mgmt tunnel exists+enabled (startup + every 2m)"',
        "# netwatch: مهلة 5s (بليب لا يعني سقوطًا)؛ السكربت مشروط بحالة الواجهة:",
        "# netwatch: 5s timeout (a blip ≠ down); the script is guarded by iface state:",
        "#   disabled → enable ؛ enabled لكن running=no → bounce ؛ running → لا شيء.",
        f'/tool netwatch remove [find comment="hr: RADIUS reachability"]',
        f'/tool netwatch add host={radius_ip} interval=60s timeout=5s '
        f'down-script="{reheal}" '
        f'comment="hr: RADIUS reachability"',
    ])


def _section_backup(p: OnboardingParams) -> str:
    """Auto-backup at the very end (binary + readable export)."""
    name = ascii_comment(f"hr-onboard-{p.router_id}", fallback="hr-onboard")
    return "\n".join([
        _hdr("٩) نسخة احتياطيّة تلقائيّة في النهاية",
             "9) Automatic backup at the end"),
        f'/system backup save name="{name}"',
        f'/export file="{name}"',
        "# تمّت التهيئة — راجع لوحة HobeRadius للتأكّد من ظهور الراوتر «متصل».",
        "# Onboarding done — check the HobeRadius panel for this router as «online».",
    ])


# ─── public entry point ─────────────────────────────────────────────────────

def build_onboarding_script(params: OnboardingParams) -> str:
    """Assemble the full, ordered, idempotent onboarding script."""
    params.validate()
    sections = [
        _section_banner(params),
        _section_tunnel(params),
        _section_radius(params),
        _section_pools(params),
        _section_api_user(params),
        _section_firewall(params),
        _section_block_redirect(params),
        _section_service_lockdown(params),
        _section_self_heal(params),
        _section_backup(params),
    ]
    return "\n\n".join(sections) + "\n"


# ─── ordering introspection (used by tests + the panel "explain order" view) ─

def firewall_rule_order(script: str) -> List[str]:
    """Extract the ordered list of our managed firewall rule comments, in the
    order they are ADDED (which equals match order after the move-to-top step).
    Used by tests to assert the ordering invariants."""
    out: List[str] = []
    for line in script.splitlines():
        m = re.search(r'comment="(' + re.escape(FW_TAG) + r'[^"]*)"', line)
        if m and "/ip firewall filter add" in line:
            out.append(m.group(1))
    return out


__all__ = [
    "OnboardingParams",
    "OnboardingScriptError",
    "build_onboarding_script",
    "firewall_rule_order",
    "MGMT_IFACE_DEFAULT",
    "WALLED_GARDEN_LIST",
    "EXPIRED_LIST",
    "FW_TAG",
]
