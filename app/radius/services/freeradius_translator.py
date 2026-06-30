"""
FreeRADIUS Translator — يحوّل Subscriber + AccessPlan إلى rows في
radcheck / radreply / radgroupcheck / radgroupreply / radusergroup.

يُستدعى تلقائيًا من SqliteAdapter عند upsert/delete.
الهدف: عندما FreeRADIUS يستقبل auth request، يجد كل ما يلزم في DB.

ملاحظات الـ attributes (مفاتيح RADIUS قياسية):
- Cleartext-Password: كلمة المرور المخزَّنة plain (يقارنها FreeRADIUS).
- Auth-Type := Reject: يجبر FreeRADIUS على الرفض المباشر.
- Expiration: تاريخ نهاية الحساب بصيغة FreeRADIUS "DD MMM YYYY HH:MM:SS".
- Calling-Station-Id == <mac>: يربط الحساب بـ MAC محدّد.
- Mikrotik-Rate-Limit: vendor-specific لـ MikroTik (سرعة upload/download).
- Session-Timeout: ثواني قبل القطع التلقائي.
- Idle-Timeout: ثواني الخمول قبل القطع.
- Port-Limit: عدد الجلسات المتزامنة المسموحة.
- Acct-Interim-Interval: كم ثانية بين كل acct update من NAS.
- Reply-Message: نص يُعرض للمستخدم.
- MS-Primary-DNS-Server / MS-Secondary-DNS-Server: DNS لـ PPPoE/PPP.
- Mikrotik-Equalize-Rate: موازنة الحمل download/upload/download-upload.
- Mikrotik-Firewall-Chain: chain مرشّح MikroTik.
- Mikrotik-Address-List: قائمة عناوين MikroTik.
- Framed-Route: مسار IP مُضاف للجلسة.
- Mikrotik-Group: مجموعة المستخدم في MikroTik.
- Mikrotik-Winbox-Group: مجموعة Winbox.
- Mikrotik-Queue-Type: أولوية الـ queue.
- Framed-Pool: مجمّع عناوين IP.
"""
from __future__ import annotations

import json as _json
import logging
from datetime import datetime

from ..core.types import AccessPlan, NasDevice, Subscriber
from ..db.repos import freeradius_repo

_LOG = logging.getLogger(__name__)


def _fr_date(dt: datetime) -> str:
    """صيغة Expiration التي يفهمها FreeRADIUS (مثال: 31 Dec 2026 23:59:00)."""
    return dt.strftime("%d %b %Y %H:%M:%S")


# ─────────────── Subscriber → radcheck + radusergroup ───────────────


def sync_subscriber(sub: Subscriber, plan: AccessPlan | None = None) -> None:
    """
    يكتب radcheck (per-user) ويربطه بـ radusergroup (group = اسم الـ plan).
    radreply لا يحتاج: نضع الـ reply attrs على الـ group بدل أن نكرّرها لكل user.
    """
    tid = sub.tenant_id
    username = sub.username

    # ─ radcheck (per-user) ─
    checks: list[tuple[str, str, str]] = []

    # كلمة المرور: لو الـ status enabled نضع password، وإلا نضع Auth-Type := Reject
    if sub.status == "enabled":
        checks.append(("Cleartext-Password", ":=", sub.password))
    else:
        checks.append(("Auth-Type", ":=", "Reject"))
        # لكن نُبقي كلمة المرور (لإمكانية إعادة التفعيل دون إعادة الإدخال)
        if sub.password:
            checks.append(("Cleartext-Password", ":=", sub.password))

    # انتهاء الصلاحية
    if sub.expire_at:
        checks.append(("Expiration", ":=", _fr_date(sub.expire_at)))

    # ربط MAC — يدعم قفل متعدد عبر قائمة مفصولة بفواصل.
    # • قيمة واحدة → 'Calling-Station-Id == "AA:BB:..."' (تحقّق صارم).
    # • قيم متعدّدة → 'Calling-Station-Id =~ "^(MAC1|MAC2|...)$"' حتى
    #   تقبل FreeRADIUS أيًّا منها (مع `==` المتكرّر يُقيِّمها AND مما
    #   يُعطّل الجلسة كليًّا).
    if sub.mac_lock:
        raw = sub.mac_lock.replace(";", ",").replace("\n", ",")
        macs = sorted({
            m.strip().upper().replace("-", ":")
            for m in raw.split(",")
            if m.strip()
        })
        if len(macs) == 1:
            checks.append(("Calling-Station-Id", "==", macs[0]))
        elif len(macs) > 1:
            pattern = "^(?:" + "|".join(macs) + ")$"
            checks.append(("Calling-Station-Id", "=~", pattern))

    # ربط IP إن وُجد static_ip
    if sub.static_ip:
        # framed-ip — يُكتب كـ reply attr عادة، لكن نضعه كـ per-user reply override
        pass  # سيُضاف للـ radreply أدناه

    # تجاوز عدد الجلسات (per-user override)
    # لو override_concurrent > 0 نضعه، وإلا plan.concurrent_sessions
    concurrent = sub.override_concurrent or (plan.concurrent_sessions if plan else 0)
    if concurrent and concurrent > 0:
        checks.append(("Simultaneous-Use", ":=", str(concurrent)))

    freeradius_repo.replace_user_check(tid, username, checks)

    # ─ radreply (per-user — فقط للأشياء الخاصة) ─
    user_reply: list[tuple[str, str, str]] = []
    if sub.static_ip:
        user_reply.append(("Framed-IP-Address", ":=", sub.static_ip))
    if sub.vlan_id and sub.vlan_id > 0:
        # MikroTik VLAN attribute
        user_reply.append(("Tunnel-Type", ":=", "VLAN"))
        user_reply.append(("Tunnel-Medium-Type", ":=", "IEEE-802"))
        user_reply.append(("Tunnel-Private-Group-Id", ":=", str(sub.vlan_id)))

    # feat/accel-ppp-radius-attrs — transport branch. A ``vps_accel``
    # subscriber is served DIRECTLY by accel-ppp on the customer RADIUS VPS
    # (Filter-Id 5 Mbit shaper ONLY — unlimited data, no quota/Disconnect,
    # no CHR/proxy), so it gets a DIFFERENT reply set. The ``chr_mikrotik``
    # branch (else) is the ORIGINAL code, byte-for-byte unchanged — a
    # subscriber is one transport or the other, never both, so the two
    # never collide in radreply.
    if getattr(sub, "transport", "chr_mikrotik") == "vps_accel":
        from . import accel_attributes
        user_reply.extend(accel_attributes.accel_reply_attrs(sub, plan))
    else:
        # Per-user speed override — covers BOTH card-level override (migration
        # 024) and the legacy subscriber-level bandwidth_control fields. The
        # row gets written into radreply, which beats the plan-level
        # radgroupreply for the same attribute. Skipped silently when both
        # values are 0 (no override → plan default applies).
        if (sub.bandwidth_control_enabled
                and sub.download_speed_kbps > 0
                and sub.upload_speed_kbps > 0):
            rate = f"{int(sub.upload_speed_kbps)}k/{int(sub.download_speed_kbps)}k"
            user_reply.append(("Mikrotik-Rate-Limit", "=", rate))

    # ── PART 1A: DNS (PPPoE/PPP only) ──
    if sub.service_type and sub.service_type.lower() in ("pppoe", "ppp"):
        if sub.primary_dns_ppp:
            user_reply.append(("MS-Primary-DNS-Server", ":=", sub.primary_dns_ppp))
        if sub.secondary_dns_ppp:
            user_reply.append(("MS-Secondary-DNS-Server", ":=", sub.secondary_dns_ppp))

    # ── PART 1B: Equal-share (موازنة الحمل) ──
    if sub.equal_share_download and sub.equal_share_upload:
        user_reply.append(("Mikrotik-Equalize-Rate", "=", "download-upload"))
    elif sub.equal_share_download:
        user_reply.append(("Mikrotik-Equalize-Rate", "=", "download"))
    elif sub.equal_share_upload:
        user_reply.append(("Mikrotik-Equalize-Rate", "=", "upload"))

    # ── PART 1C: Subscriber metadata attrs ──
    _meta = _json.loads(sub.metadata or "{}")
    _mt   = _meta.get("mikrotik", {})
    _rad  = _meta.get("radius", {})

    if _mt.get("mikrotik_filter_chain"):
        user_reply.append(("Mikrotik-Firewall-Chain", "=", str(_mt["mikrotik_filter_chain"])))
    if _mt.get("mikrotik_address_list"):
        user_reply.append(("Mikrotik-Address-List", "=", str(_mt["mikrotik_address_list"])))
    if _mt.get("mikrotik_framed_route"):
        user_reply.append(("Framed-Route", "=", str(_mt["mikrotik_framed_route"])))
    if _mt.get("mikrotik_user_group"):
        user_reply.append(("Mikrotik-Group", "=", str(_mt["mikrotik_user_group"])))
    if _mt.get("mikrotik_winbox_group"):
        user_reply.append(("Mikrotik-Winbox-Group", "=", str(_mt["mikrotik_winbox_group"])))
    if _mt.get("mikrotik_queue_priority"):
        user_reply.append(("Mikrotik-Queue-Type", "=", str(_mt["mikrotik_queue_priority"])))
    if _rad.get("framed_pool"):
        user_reply.append(("Framed-Pool", ":=", str(_rad["framed_pool"])))

    # ppp_attributes_extra: one "Attr-Name op value" per line
    for _line in str(_rad.get("ppp_attributes_extra") or "").splitlines():
        _parts = _line.strip().split(None, 2)
        if len(_parts) == 3:
            user_reply.append((_parts[0], _parts[1], _parts[2]))

    # acct_interim_interval_sec override (per-user wins over plan-level group reply)
    _aii = _rad.get("acct_interim_interval_sec")
    if _aii:
        try:
            _aii_int = int(_aii)
            if _aii_int > 0:
                user_reply.append(("Acct-Interim-Interval", ":=", str(_aii_int)))
        except (TypeError, ValueError):
            pass

    freeradius_repo.replace_user_reply(tid, username, user_reply)

    # ─ radusergroup (link to plan) ─
    if plan:
        group_name = _plan_group_name(plan)
        freeradius_repo.link_user_group(tid, username, group_name, priority=1)
    else:
        # لا plan: نزيل أي ربط مجموعة
        freeradius_repo.link_user_group(tid, username, "default", priority=99)

    _LOG.info("freeradius_translator: synced user=%s plan=%s checks=%d",
              username, plan.name if plan else "—", len(checks))


def delete_subscriber(tenant_id: int, username: str) -> None:
    """يحذف كل rows الـ FreeRADIUS للـ user."""
    freeradius_repo.delete_user(tenant_id, username)


# ─────────────── Plan → radgroupreply ───────────────


def _plan_group_name(plan: AccessPlan) -> str:
    """اسم الـ group في radgroupreply = "plan_<id>" أو الاسم لو ASCII."""
    # نستخدم plan_<id> دائمًا لتفادي مشكلات الترميز والـ uniqueness
    return f"plan_{plan.id}"


def sync_plan(plan: AccessPlan) -> None:
    """يكتب radgroupreply (attrs الـ Access-Accept للـ plan)."""
    tid = plan.tenant_id
    group = _plan_group_name(plan)

    reply: list[tuple[str, str, str]] = []

    # السرعة (MikroTik vendor-specific) — صيغة "up/down k" أو "up/down k <burst...>"
    if plan.speed_down_kbps or plan.speed_up_kbps:
        if plan.burst_raw:
            rate = plan.burst_raw
        else:
            rate = f"{plan.speed_up_kbps}k/{plan.speed_down_kbps}k"
        reply.append(("Mikrotik-Rate-Limit", "=", rate))

    # Session timeout (ثواني)
    timeout_sec = plan.session_timeout_sec
    # لو هناك duration_minutes للخطط الزمنية، نُحسبها لو لم يُحدَّد timeout
    if not timeout_sec and plan.duration_minutes:
        timeout_sec = plan.duration_minutes * 60
    if timeout_sec and timeout_sec > 0:
        reply.append(("Session-Timeout", ":=", str(timeout_sec)))

    # Idle timeout
    if plan.idle_timeout_sec and plan.idle_timeout_sec > 0:
        reply.append(("Idle-Timeout", ":=", str(plan.idle_timeout_sec)))

    # Concurrent sessions (يُكرَّر على المستوى الـ user أيضًا)
    if plan.concurrent_sessions and plan.concurrent_sessions > 0:
        reply.append(("Port-Limit", ":=", str(plan.concurrent_sessions)))

    # MikroTik Address Pool — لو الخطة فيها address_pool
    if plan.address_pool:
        reply.append(("Mikrotik-Address-List", "=", plan.address_pool))

    # Interim-Update interval — استخدم plan.acct_interim_interval لو موجود، 60 fallback.
    # ملاحظة: الـ per-user override في radreply (sync_subscriber) يكسب دائمًا على هذه القيمة.
    _plan_aii = getattr(plan, "acct_interim_interval", None)
    if _plan_aii and int(_plan_aii) > 0:
        reply.append(("Acct-Interim-Interval", ":=", str(int(_plan_aii))))
    else:
        reply.append(("Acct-Interim-Interval", ":=", "60"))

    # رسالة ترحيب صغيرة (تظهر في سجل FreeRADIUS، بعض NAS تعرضها)
    reply.append(("Reply-Message", "=", f"Plan: {plan.name}"))

    # ── PART 2: Plan metadata attrs ──
    _pmeta = _json.loads(plan.metadata or "{}")
    _pmt   = _pmeta.get("mikrotik", {})

    if _pmt.get("mikrotik_filter_chain_name"):
        reply.append(("Mikrotik-Firewall-Chain", "=", str(_pmt["mikrotik_filter_chain_name"])))
    # mikrotik_address_list only if address_pool is not already set (avoid duplicate)
    if _pmt.get("mikrotik_address_list") and not plan.address_pool:
        reply.append(("Mikrotik-Address-List", "=", str(_pmt["mikrotik_address_list"])))
    if _pmt.get("mikrotik_user_group"):
        reply.append(("Mikrotik-Group", "=", str(_pmt["mikrotik_user_group"])))
    if _pmt.get("mikrotik_queue_priority_simple_queue"):
        reply.append(("Mikrotik-Queue-Type", "=", str(_pmt["mikrotik_queue_priority_simple_queue"])))

    # Plan framed_pool (direct field)
    if getattr(plan, "framed_pool", None):
        reply.append(("Framed-Pool", ":=", plan.framed_pool))

    freeradius_repo.replace_group_reply(tid, group, reply)
    # check للـ group: فارغة الآن — كل القرارات تتم بالـ user check أو policy engine
    freeradius_repo.replace_group_check(tid, group, [])

    _LOG.info("freeradius_translator: synced plan=%s group=%s attrs=%d",
              plan.name, group, len(reply))


def delete_plan(plan: AccessPlan) -> None:
    freeradius_repo.delete_group(plan.tenant_id, _plan_group_name(plan))


# ─────────────── NAS → FreeRADIUS clients ───────────────


def sync_nas(nas: NasDevice) -> None:
    """يكتب الـ NAS كـ FreeRADIUS client."""
    if not nas.enabled:
        freeradius_repo.delete_nas_client(nas.tenant_id, nas.address)
        return
    freeradius_repo.upsert_nas_client(
        nas.tenant_id,
        nasname=nas.address,
        shortname=nas.name,
        secret=nas.secret,
        nas_type="mikrotik" if nas.vendor == "mikrotik" else "other",
        description=nas.description or nas.name,
    )


def delete_nas(nas: NasDevice) -> None:
    freeradius_repo.delete_nas_client(nas.tenant_id, nas.address)


# ─────────────── full re-sync (للحالات الطارئة) ───────────────


def resync_all(tenant_id: int) -> dict:
    """يُعيد بناء كل rows الـ FreeRADIUS من الـ subscribers/plans/nas."""
    from ..db.repos import plans_repo, subscribers_repo, nas_repo
    counts = {"plans": 0, "subscribers": 0, "nas": 0}

    plans = plans_repo.list_plans(tenant_id, limit=10_000)
    plans_by_id = {p.id: p for p in plans}
    for p in plans:
        sync_plan(p)
        counts["plans"] += 1

    for s in subscribers_repo.list_subscribers(tenant_id, limit=100_000):
        plan = plans_by_id.get(s.plan_id) if s.plan_id else None
        sync_subscriber(s, plan)
        counts["subscribers"] += 1

    for n in nas_repo.list_nas(tenant_id, limit=1000):
        sync_nas(n)
        counts["nas"] += 1

    _LOG.info("resync_all tenant=%d: %s", tenant_id, counts)
    return counts
