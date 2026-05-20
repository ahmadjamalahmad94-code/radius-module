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
"""
from __future__ import annotations

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

    # ربط MAC إن وُجد
    if sub.mac_lock:
        checks.append(("Calling-Station-Id", "==", sub.mac_lock.upper()))

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

    # Interim-Update كل 60 ثانية افتراضيًا (للحصول على acct بيانات حيّة)
    reply.append(("Acct-Interim-Interval", ":=", "60"))

    # رسالة ترحيب صغيرة (تظهر في سجل FreeRADIUS، بعض NAS تعرضها)
    reply.append(("Reply-Message", "=", f"Plan: {plan.name}"))

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
