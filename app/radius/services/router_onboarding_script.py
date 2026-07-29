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

2. **Idempotent + authoritative.** Every add is guarded (``find`` existence
   checks) or the managed block is removed-then-rebuilt, so re-pasting the whole
   script converges to the same state. It also takes OWNERSHIP of the config it
   manages before applying: it DISABLES any pre-existing RADIUS (leftover or a
   competitor's) so the router authenticates against ours ONLY, and disables any
   other SSTP client dialing OUR server endpoint (so two clients can't fight over
   the same ``rtr-*`` account). Cleanup is scoped strictly to objects we own — by
   our ``hr-``/``hobe-`` name, our ``hr-*:`` comment tag, or our server endpoint —
   so the customer's own unrelated VPN/services are never touched. (The one
   deliberately broad action is disabling *all* RADIUS, which the owner requested
   so a stale/competitor RADIUS cannot intercept auth; it is a reversible
   ``disable``, not a delete.)

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
    # RouterOS major version of the target router: '6' / '7' / '' (unknown).
    # Drives SSTP-command compatibility — RouterOS 6 legacy rejects several
    # SSTP properties that v7 accepts (see _section_tunnel). Unknown/empty
    # defaults to the v7 (full) command, which suits modern routers.
    ros_version: str = "7"

    def ros_major(self) -> int:
        """The RouterOS major version as an int (6 or 7). Unknown → 7."""
        m = re.match(r"\s*(\d+)", str(self.ros_version or ""))
        return int(m.group(1)) if m else 7

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
    """PPP profile + SSTP mgmt client (Profile=default-encryption per owner
    decision, verify-cert off) + the route to our RADIUS over the tunnel.
    Idempotent (find-guarded)."""
    host = _q(p.accel_host, field="accel_host")
    user = _q(p.tunnel_user, field="tunnel_user")
    pw = _q(p.tunnel_password, field="tunnel_password")
    iface = _q(p.mgmt_iface, field="mgmt_iface")
    radius_ip = _q(p.radius_ip, field="radius_ip")

    # ── SSTP client add — version-aware (RouterOS 6 legacy vs 7) ─────────────
    # RouterOS 6.x rejects several SSTP properties that v7 accepts; an
    # unknown property makes the whole `add` fail, so hr-sstp-mgmt is never
    # created (and then the RADIUS route, whose gateway IS that interface,
    # fails too). We therefore emit a v6-compatible command WITHOUT:
    #   • verify-server-address-from-certificate   (v6 has no such property)
    #   • port=443                                  (SSTP defaults to 443)
    #   • keepalive-timeout=30                      (unsupported on old builds)
    # verify-server-certificate=no is kept (supported on v6). On v7 we keep
    # the full command — verify-server-address-from-certificate=no is REQUIRED
    # there, else the default =yes re-verifies our IP against a name-CN cert
    # and the tunnel flaps.
    if p.ros_major() <= 6:
        # v6: نُبقي على default-encryption (مدعوم على v6) ونُسقط فقط الخصائص التي
        # يرفضها v6 (verify-server-address-from-certificate / port / keepalive).
        sstp_add = (
            f'/interface sstp-client add name="{iface}" connect-to={host} '
            f'user="{user}" password="{pw}" profile=default-encryption '
            f'verify-server-certificate=no add-default-route=no disabled=no '
            f'comment="hr: SSTP mgmt to HobeRadius"')
        sstp_note = ("# RouterOS 6 legacy: أمر SSTP مبسّط (بلا "
                     "verify-server-address-from-certificate / port / "
                     "keepalive-timeout — يرفضها v6). | v6-compatible SSTP add.")
    else:
        sstp_add = (
            f'/interface sstp-client add name="{iface}" connect-to={host} '
            f'port={int(p.sstp_port)} user="{user}" password="{pw}" profile=default-encryption '
            f'verify-server-certificate=no verify-server-address-from-certificate=no '
            f'add-default-route=no disabled=no '
            f'keepalive-timeout=30 comment="hr: SSTP mgmt to HobeRadius"')
        sstp_note = ("# RouterOS 7: الأمر الكامل (verify-server-address-from-"
                     "certificate=no إلزاميّ كيلا يرفّ النفق). | v7 full SSTP add.")

    return "\n".join([
        _hdr("١) نفق الإدارة SSTP — المسار الذي نُدير منه الراوتر",
             "1) SSTP management tunnel — the path we manage the router over"),
        "# Profile=default-encryption عمدًا (قرار المالك): تفعيل تشفير PPP/MPPE على",
        "# مستوى البروفايل للتوافق/الأمان مع سلوك SSTP في مايكروتيك — قرار صريح؛ لا",
        "# تُرجِعها إلى default. (سابقًا كان default تفاديًا لحادثة ccr4/ccr5؛ المالك",
        "# اختار default-encryption صراحةً — لا تعكس القرار بلا موافقته.)",
        "# Profile=default-encryption intentionally (owner decision): PPP/MPPE",
        "# encryption ON at profile level — do NOT revert to default without the owner.",
        "# verify-server-certificate=no: الشهادة قد تكون موقّعة ذاتيًّا (لا CA على الراوتر).",
        "# verify-server-address-from-certificate=no: نتّصل بالـIP وشهادتنا CN=اسم",
        "# لا IP، فلو بقي =yes (الافتراضي) تفشل إعادة التحقّق دوريًّا ويرفّ النفق.",
        "# تنظيف سلطويّ قبل الإنشاء — مقصور على ما نملكه (لا نلمس VPN العميل):",
        "#  • نُعطّل أيّ عميل SSTP آخر يتّصل بخادمنا نفسه (كيلا يتنازع عميلان على",
        "#    حساب rtr-* نفسه). مقصور على connect-to=خادمنا، مع استثناء اسمنا المُدار.",
        "#  • نُزيل عميل PPTP مُدارًا باسمنا متبقّيًا (لو هُيّئ الراوتر سابقًا عبر PPTP).",
        "# Authoritative cleanup BEFORE we create ours — scoped to what WE own",
        "# (never the customer's unrelated VPNs):",
        "#  • disable any OTHER SSTP client dialing OUR server (so two clients can't",
        "#    fight over the same rtr-* account). Scoped to connect-to=our host,",
        "#    excluding our managed name. Guarded :foreach → no error when none.",
        "#  • remove a stale OUR-named PPTP mgmt client (prior v6 transport).",
        (f':foreach c in=[/interface sstp-client find connect-to="{host}"] '
         f'do={{ :if ([/interface sstp-client get $c name] != "{iface}") '
         f'do={{ /interface sstp-client disable $c }} }}'),
        f'/interface pptp-client remove [find name="hr-pptp-mgmt"]',
        f'/ppp profile remove [find name="{PPP_PROFILE}"]',
        f'/ppp profile add name="{PPP_PROFILE}" use-encryption=no use-mpls=no '
        f'comment="hr: mgmt tunnel profile"',
        f'/interface sstp-client remove [find name="{iface}"]',
        sstp_note,
        sstp_add,
        "# مسار صريح إلى خادم RADIUS عبر النفق (لا يعتمد على المسار الافتراضي).",
        "# يُضاف فقط بعد وجود الواجهة hr-sstp-mgmt (بوّابته هي هذه الواجهة) —",
        "# فلو فشل إنشاء العميل (كأمر v7 على راوتر v6) لا نُنشئ مسارًا يتيمًا.",
        "# Explicit route to our RADIUS over the tunnel (never via the default),",
        "# added ONLY after hr-sstp-mgmt exists (its gateway IS that interface).",
        (f':if ([:len [/interface sstp-client find name="{iface}"]] > 0) do={{ '
         f'/ip route remove [find comment="hr: route to RADIUS"]; '
         f'/ip route add dst-address={radius_ip}/32 gateway="{iface}" '
         f'distance=1 comment="hr: route to RADIUS" }}'),
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
        "# سلطويّ: نُعطّل أيّ RADIUS موجود سلفًا (متبقٍّ أو لمنافس) كي لا يَعترض",
        "# المصادقة — يستخدم الراوتر RADIUS الخاص بنا فقط. تعطيل (قابل للعكس) لا حذف.",
        "# Authoritative: DISABLE any RADIUS already on the router (leftover or a",
        "# competitor's) so it can't intercept auth — the router uses ONLY ours.",
        "# Guarded :foreach → never errors when there is none. Disable, not delete.",
        ":foreach r in=[/radius find] do={ /radius disable $r }",
        "# ثم نزع مدخلنا الموسوم وإعادة إضافته (idempotent: إعادة اللصق لا تُكرّر).",
        "# Then remove our tagged entry and re-add it (idempotent; re-paste = no dup).",
        f'/radius remove [find comment="hr: HobeRadius RADIUS"]',
        f'/radius add address={radius_ip} secret="{secret}" '
        f'service=hotspot,ppp,login src-address={p.tunnel_ip} '
        f'timeout=3000ms comment="hr: HobeRadius RADIUS"',
        "",
        "# MT96 — جردٌ قبل القلب. السطران التاليان يُحوّلان مصادقة الهوتسبوت",
        "# وPPPoE إلى RADIUS. المستخدمون المحلّيّون في RouterOS يُفحَصون أوّلًا",
        "# ثمّ يُسأل RADIUS، فلا يُفترَض أن ينقطع أحد — لكنّ «لا يُفترَض» ليست",
        "# ضمانة على راوترٍ يخدم زبائن. لذلك نطبع ما هو قائمٌ قبل أن نمسّه:",
        "# إن رأيت أعدادًا غير صفريّة فأنت تُعدّل راوترًا عاملًا لا جديدًا —",
        "# راقب زبائنك بعد اللصق، وإن انقطعوا فأعِد: use-radius=no.",
        ':put ("[hr] مستخدمو الهوتسبوت المحلّيّون: " . [:len [/ip hotspot user find]] . " | خوادم هوتسبوت: " . [:len [/ip hotspot find]] . " | أسرار PPP: " . [:len [/ppp secret find]])',
        ':put ("[hr] use-radius للهوتسبوت قبل التغيير: " . [/ip hotspot profile get [find default=yes] use-radius])',
        "",
        "# تفعيل استخدام RADIUS للهوتسبوت و PPPoE.",
        "/ip hotspot profile set [find default=yes] use-radius=yes",
        "/ppp aaa set use-radius=yes accounting=yes interim-update=5m",
        "# CoA/Disconnect الوارد على 3799 — إعداد عامّ (set سلطويّ) يَقبل فقط من خادمنا.",
        "# Incoming CoA/Disconnect on 3799 — a global authoritative `set`; only our",
        "# server is accepted (enforced by the firewall input rule below).",
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
        # MT89 — أُزيلت «06 ICMP diag» (`protocol=icmp action=accept` بلا مصدر).
        # كانت تقبل البِنج من الإنترنت كلّه، وتُرفَع فوق قواعد الزبون فتُبطل أيّ
        # حجبٍ وضعه هو لـICMP. ولا تُضيف لنا شيئًا: القاعدة 02 تقبل *كلّ* شيء
        # من واجهة النفق (والبِنج منها)، و03 تقبل كلّ شيء من خادم RADIUS —
        # فتشخيصنا مُغطّى مرّتين. حذفها لا يمسّ الربط، ويُغلق تعرّضًا مجّانيًّا.
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
        # MT89 — أُزيلت «13 to RADIUS server»: مكرّرةٌ إثباتًا لا ترجيحًا.
        # `radius_ip` يُضاف بلا شرط إلى قائمة الحديقة المسوّرة أعلاه، والقاعدة
        # 11 تقبل كلّ ما يقصد تلك القائمة — فهذه تُطابق ما طُوبق سلفًا. قاعدةٌ
        # أقلّ فوق قواعد الزبون = مساحةٌ أقلّ للمفاجآت.
        ("forward", "14 DNS forward",
         "protocol=udp dst-port=53 action=accept",
         "DNS للحديقة المسوّرة حتى عند الحدّ | DNS even when limited"),
        # ── لا قاعدة رفض/حجب في forward إطلاقًا (طلب المالك) ──
        # أُزيلت «expired pool reject»: كانت REJECT تُرفَع فوق قواعد الهوت سبوت
        # الديناميكيّة فتحجب حركة العميل قبل أن تعترضه البوّابة الأسيرة (تكسر
        # صفحة الدخول/التجديد تمامًا مثل «default accept» سابقًا). والانتهاء
        # مُنفَّذ أصلًا عبر RADIUS (رفض المصادقة + فصل PoD) فلا حاجة لحجب ناريّ
        # مكرِّر. كتلة hr-fw صارت **سماحات فقط** (لا تحجب شيئًا) لمسار الإدارة
        # والحديقة المسوّرة وDNS — فلا تتعارض مع الهوت سبوت أبدًا.
        # ── NO broad forward accept ──
        # We deliberately do NOT add an unconditional `chain=forward
        # action=accept` here. The move-to-top step below lifts every hr-fw
        # rule above the router's OWN Hotspot dynamic forward rules
        # (hs-unauth / hs-auth). A broad accept in our block would therefore
        # sit ABOVE the captive-portal rules and short-circuit them —
        # unauthenticated clients would be "accepted" before the Hotspot
        # could intercept, so captive.apple.com etc. never redirect and the
        # login page never appears (iPhone shows "server cannot be found").
        # Active subscribers get internet from RouterOS's implicit end-of-chain
        # accept (or the Hotspot's own hs-auth accept); our block is now
        # allow-only (walled-garden / mgmt / RADIUS / DNS) — no accept-all and
        # no reject/drop, so it can never short-circuit or block the captive
        # portal. Subscriber expiry/limits are enforced by RADIUS, not firewall.
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
    """Disable telnet/ftp/ssh/api-ssl; restrict the rest to the mgmt source.

    The kept services bind to a COMBINED allow-list — the SSTP/RADIUS gateway
    (this script's tunnel) AND the WireGuard management subnet — because
    ``/ip service set address=`` REPLACES the value. Without the WG subnet,
    pasting this SSTP script would clobber WinBox-over-WireGuard (and vice
    versa). Strictly tunnel-only — never the WAN. See :mod:`mgmt_acl`."""
    from . import mgmt_acl
    radius_ip = _q(p.radius_ip, field="radius_ip")
    # The service-lockdown ACL block comes from the ONE shared source
    # (mgmt_acl.service_lockdown_lines) — SSTP gateway leads (this is the SSTP
    # script); the WG subnet is appended so a re-paste never removes the
    # WireGuard management path. Identical block across every generator.
    return "\n".join([
        _hdr("٧) تقليص الخدمات — أغلق ما لا نحتاجه، وقيّد الباقي بمصدر النفق",
             "7) Service lockdown — disable the unneeded, bind the rest to the tunnel"),
        "/ip service disable telnet,ftp,ssh,api-ssl",
        "# الخدمات المُبقاة تُقصَر على بوّابتَي الإدارة: نفق SSTP وشبكة WireGuard.",
        "# kept services bind to BOTH mgmt gateways: the SSTP tunnel + the WG subnet.",
        *mgmt_acl.service_lockdown_lines(sstp_gateway_ip=radius_ip),
    ])


#: RouterOS /system script names that hold the self-heal logic (ASCII, stable,
#: unquoted-safe — letters/digits/hyphens only).
WATCHDOG_FN = "hr-sstp-watchdog-fn"
REHEAL_FN = "hr-sstp-reheal-fn"
WATCHDOG_SCHED = "hr-sstp-watchdog"
NETWATCH_COMMENT = "hr: RADIUS reachability"


def _section_self_heal(p: OnboardingParams) -> str:
    """Self-heal — the FIELD-VERIFIED run-by-name form. The owner pasted exactly
    this on the real ccr5 / RouterOS 7.20 and confirmed the every-2-minute
    ``missing value(s) of argument(s) value`` error is GONE, then said «اعتمد»
    (adopt it). So this is the authoritative watchdog; main must equal it.

    (A later inline ``global hrSstpIf``/``get [find ...] running`` mirror was
    plausible but never field-tested on our ROS 7.20, so it was reverted to this
    proven form to avoid re-introducing the bug on the next onboarded router.)

    Why this survives ROS 7.20 store+reparse:
      1. The scheduler/netwatch stored values are just ``/system script run
         <name>`` — NO ``[...]`` command-substitution, NO ``$`` variable, NO
         nested double-quotes. Nothing for ROS to mis-substitute/mis-escape.
      2. The logic lives in ``/system script ... source={ ... }``. The brace
         ``{}`` form is RouterOS's LITERAL code-block: stored verbatim, only
         interpreted when the script RUNS. Inside: **no double-quotes** (the
         iface name is an unquoted bare token), **no ``$``** (re-run ``find``,
         never stash ``$id``), **no ``get``** (match with
         ``find name=<iface> disabled=yes`` then ``enable``).
      3. Guarded: every enable/disable is inside ``:if ([:len [find ...]] > 0)``.
      4. Non-flapping: watchdog only enables a client ``find`` reports
         ``disabled=yes``; reheal bounces ONLY a ``disabled=no`` AND
         ``running=no`` (genuinely stuck) one.
      5. Authoritative + idempotent: removes the old scheduler AND both old
         ``/system script`` fns before re-adding.

    The interface name is our controlled constant (``hr-sstp-mgmt``) — a bare
    token, valid unquoted in ``find name=...``.
    """
    iface = p.mgmt_iface          # our controlled, unquoted-safe name
    radius_ip = _q(p.radius_ip, field="radius_ip")

    # Logic bodies — LITERAL source={...} blocks. No ", no $, no get. ASCII.
    sc = "/interface sstp-client"
    # Watchdog: enable our client ONLY if it is currently disabled.
    wd_src = (
        "{:if ([:len [" + sc + " find name=" + iface + " disabled=yes]] > 0)"
        " do={" + sc + " enable [" + sc + " find name=" + iface + " disabled=yes]}}"
    )
    # Reheal: disabled -> enable; else enabled-but-not-running -> bounce; running
    # -> nothing. (else makes the two cases mutually exclusive.)
    rh_src = (
        "{:if ([:len [" + sc + " find name=" + iface + " disabled=yes]] > 0)"
        " do={" + sc + " enable [" + sc + " find name=" + iface + " disabled=yes]}"
        " else={:if ([:len [" + sc + " find name=" + iface + " running=no]] > 0)"
        " do={" + sc + " disable [" + sc + " find name=" + iface + "];"
        " :delay 3s; " + sc + " enable [" + sc + " find name=" + iface + "]}}}"
    )
    return "\n".join([
        _hdr("٨) الإصلاح الذاتيّ — الشكل المُثبَت ميدانيًّا (run-by-name) الذي اعتمده المالك",
             "8) Self-heal — the field-verified run-by-name form the owner adopted"),
        "# المنطق في /system script (كتلة {} حرفيّة: لا اقتباس متداخل، لا $، لا get)،",
        "# والجدول/الـnetwatch ينادي الاسم فقط — لا هشاشة في أيّ قيمة مُخزَّنة تُعاد قراءتها.",
        "# Logic lives in /system script (literal {} block: no nested quotes, no $,",
        "# no get); the scheduler/netwatch just run it by name — zero fragility in",
        "# any stored+reparsed value. Authoritative cleanup removes old objects first.",
        f'/system scheduler remove [find name={WATCHDOG_SCHED}]',
        f'/system script remove [find name={WATCHDOG_FN}]',
        f'/system script remove [find name={REHEAL_FN}]',
        "# الجدول: يُفعّل العميل فقط إن كان معطّلًا — لا يَلمس واجهة شغّالة إطلاقًا.",
        "# Watchdog: enable the client only if disabled — never touches a running one.",
        f'/system script add name={WATCHDOG_FN} source={wd_src}',
        f'/system scheduler add name={WATCHDOG_SCHED} interval=2m start-time=startup '
        f'on-event="/system script run {WATCHDOG_FN}" '
        f'comment="hr: re-enable mgmt tunnel if disabled"',
        "# netwatch: مهلة 5s (بليب لا يعني سقوطًا)؛ يُفعّل المعطّل أو يَرتدّ العالق فقط.",
        "# netwatch: 5s timeout (a blip is not a down); enable-if-disabled or",
        "# bounce-only-if-stuck (running=no). A running tunnel is never touched.",
        f'/system script add name={REHEAL_FN} source={rh_src}',
        f'/tool netwatch remove [find comment="{NETWATCH_COMMENT}"]',
        f'/tool netwatch add host={radius_ip} interval=60s timeout=5s '
        f'down-script="/system script run {REHEAL_FN}" '
        f'comment="{NETWATCH_COMMENT}"',
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


def split_sections(script: str) -> "List[dict]":
    """Split a built onboarding script into its labelled sections for the UI.

    Detects each :func:`_hdr` block (a 4-line header: bar / ``# {ar}`` /
    ``# {en}`` / bar) plus the leading banner, and slices the script by line
    ranges. Each section ``body`` is an **exact substring** of ``script`` (sliced
    by line index — never re-assembled), so rendering or per-section copy can
    never drift from the canonical text. Returns
    ``[{"title", "title_en", "start_line", "body"}]`` (1-based ``start_line``).

    Presentation-only: the canonical full script remains the source of truth for
    the master copy; this just gives the page a readable outline.
    """
    bar = "# " + "─" * 70
    lines = script.split("\n")
    n = len(lines)
    starts: "List[tuple]" = []          # (line_index, title_ar, title_en)
    i = 0
    while i < n:
        # an _hdr top bar is a bar line whose matching closing bar is 3 lines
        # below, with the two title lines in between.
        if lines[i] == bar and i + 3 < n and lines[i + 3] == bar:
            title_ar = lines[i + 1].lstrip("# ").strip()
            title_en = lines[i + 2].lstrip("# ").strip()
            starts.append((i, title_ar, title_en))
            i += 4
            continue
        i += 1

    if not starts:
        return [{"title": "السكربت", "title_en": "Script",
                 "start_line": 1, "body": script.rstrip("\n")}]

    sections: "List[dict]" = []
    first = starts[0][0]
    if first > 0:                       # leading banner block
        sections.append({
            "title": "الترويسة", "title_en": "Banner", "start_line": 1,
            "body": "\n".join(lines[0:first]).rstrip("\n"),
        })
    for idx, (li, t_ar, t_en) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else n
        sections.append({
            "title": t_ar or t_en or f"قسم {idx + 1}",
            "title_en": t_en,
            "start_line": li + 1,
            "body": "\n".join(lines[li:end]).rstrip("\n"),
        })
    return sections


# ─── شرح الكود — per-section explanations (the praised onboarding treatment) ──
#
# The visual code card is only HALF the design the owner praised; the other half
# is a plain-Arabic explanation of WHAT each section does and WHY. Keyed by the
# section's leading marker (the banner, then the Arabic ordinals ١..٩ that the
# _hdr() titles start with) so it stays correct even if wording is tweaked.

_SECTION_EXPLAIN = {
    "الترويسة":
        "تعليقات افتتاحية تعرّف بالسكربت والراوتر — توثيق فقط، لا تُنفَّذ أوامر هنا.",
    "١":
        "ينشئ عميل نفق الإدارة (SSTP) الذي يصل الراوتر بخادم اللوحة عبر قناة "
        "مشفّرة — هذا هو المسار الذي تُدار منه الراوتر عن بُعد. يضبط "
        "<code>verify-server-certificate=no</code> (الشهادة موقّعة ذاتيًّا) "
        "و<code>keep-alive</code> ليبقى النفق حيًّا.",
    "٢":
        "يضيف خادم RADIUS لمصادقة مستخدمي الهوتسبوت وPPPoE، وتسجيل المحاسبة، "
        "وقبول أوامر CoA الواردة (قطع/تعديل الجلسة لحظيًّا). يعطّل أيّ إعداد "
        "RADIUS قديم متضارب أولًا حتى لا تتضاعف المصادر.",
    "٣":
        "يعرّف مجمّعات العناوين (نطاقات IP) التي يوزّعها الهوتسبوت وPPPoE — "
        "RADIUS هو الذي يُسند كلّ مستخدم إلى المجمّع المناسب عند الدخول.",
    "٤":
        "ينشئ حساب API مقصورًا على مصدر النفق وحده؛ اللوحة تستخدمه لقراءة الحالة "
        "وإدارة الراوتر، ولا يُقبَل هذا الحساب من أيّ عنوان آخر.",
    "٥":
        "قلب السكربت: قواعد الجدار الناريّ مرتّبة بعناية (السماح للجلسات القائمة "
        "+ واجهة الإدارة + RADIUS + DNS + الحديقة المسوّرة) <b>قبل</b> أيّ رفض أو "
        "توجيه. الترتيب «أوّل تطابق من الأعلى» يضمن ألّا يُحجَب مسار الإدارة أبدًا.",
    "٦":
        "يعيد توجيه مستخدمي الاشتراكات المنتهية إلى صفحة «انتهى اشتراكك» "
        "(HTTP فقط) بدل قطعهم بصمت — تجربة أوضح للمشترك.",
    "٧":
        "يقلّص الخدمات: يُغلق خدمات RouterOS غير المستخدمة ويقيّد الباقي على "
        "مصدر النفق — تقليل سطح الهجوم على الراوتر.",
    "٨":
        "الإصلاح الذاتيّ: يثبّت السكربت/الجدول الذي يعيد تفعيل نفق الإدارة "
        "تلقائيًّا إن سقط (نمط run-by-name المُثبَت ميدانيًّا) فيبقى الراوتر قابلًا "
        "للإدارة دون تدخّل يدويّ.",
    "٩":
        "يأخذ نسخة احتياطيّة كاملة من إعداد الراوتر في نهاية التنفيذ — نقطة رجوع "
        "آمنة بعد التهيئة.",
}


def _explain_key(title: str) -> str:
    """Map a section title to its explanation key (banner or leading ordinal)."""
    t = (title or "").strip()
    if t.startswith("الترويسة"):
        return "الترويسة"
    return t[:1] if t[:1] in "١٢٣٤٥٦٧٨٩" else ""


def explain_sections(sections: "List[dict]") -> "List[dict]":
    """Pair each section from :func:`split_sections` with its plain-Arabic
    explanation + the exact line range, so the page can render a «شرح الكود»
    panel that doubles as a clickable table of contents.

    Returns ``[{title, body, start_line, end_line, sec}]`` where ``body`` is the
    explanation, ``sec`` is the 0-based section index (matches the code card's
    ``#<id>-sec-N`` per-section copy hooks), and ``[start_line, end_line]`` is
    1-based inclusive (computed exactly from the section body's own line count)."""
    out: "List[dict]" = []
    for idx, sec in enumerate(sections):
        body = _SECTION_EXPLAIN.get(_explain_key(str(sec.get("title") or "")))
        if not body:
            continue
        start = int(sec.get("start_line") or 1)
        end = start + str(sec.get("body") or "").count("\n")
        out.append({"title": sec.get("title"), "body": body,
                    "start_line": start, "end_line": end, "sec": idx})
    return out


__all__ = [
    "OnboardingParams",
    "OnboardingScriptError",
    "build_onboarding_script",
    "firewall_rule_order",
    "split_sections",
    "explain_sections",
    "MGMT_IFACE_DEFAULT",
    "WALLED_GARDEN_LIST",
    "EXPIRED_LIST",
    "FW_TAG",
]
