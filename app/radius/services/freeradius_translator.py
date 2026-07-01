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
    # تُحلّ عبر bandwidth_rate.plan_rate_limit: لو الخطّة تُشير لملفّ سرعة
    # (bandwidth_id) موجود فالملفّ هو المصدر (إنفاذ Finding-1 → option A)، وإلّا
    # حقول الخطّة. الخطط بلا ملفّ تبقى كما هي.
    from .bandwidth_rate import plan_rate_limit
    rate = plan_rate_limit(plan)
    if rate:
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


def _radius_source_ip(nas: NasDevice) -> str:
    """The address FreeRADIUS actually sees as the UDP source of
    this router's Access-Request. For a VPN-mode router that is
    its tunnel-side IP (10.10.0.x, stored in nas_devices.
    vpn_peer_address); for a direct router it's nas.address.

    The NasDevice dataclass omits the VPN columns, so read
    vpn_peer_address raw and fall back to nas.address. Best-effort:
    any lookup miss (missing column in an old snapshot, no request
    context) just uses nas.address — the wizard sets address ==
    vpn_peer_address for VPN routers anyway."""
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT vpn_peer_address FROM nas_devices "
            "WHERE id=? AND tenant_id=?",
            (int(nas.id), int(nas.tenant_id)),
        ).fetchone()
        if row:
            vpn = row["vpn_peer_address"] if not isinstance(row, dict) \
                else row.get("vpn_peer_address")
            vpn = str(vpn or "").strip()
            if vpn:
                return vpn
    except Exception:  # noqa: BLE001
        pass
    return (nas.address or "").strip()


def sync_nas(nas: NasDevice) -> None:
    """Register (or revoke) the NAS as a FreeRADIUS client.

    The client is written as ONE `$INCLUDE` file per NAS row
    (nas-<id>.conf) in the shared clients directory — the same
    live-reload mechanism the Setup Wizard uses. FreeRADIUS picks
    it up within ~5s (entrypoint reload-trigger watcher) with no
    restart, no clients.conf edit, no redeploy.

    Files are the SINGLE source of truth for NAS clients — we
    deliberately do NOT also write the SQL `nas` table, because a
    client defined in BOTH the SQL table (read_clients=yes) and a
    file with the same ipaddr makes FreeRADIUS abort with a
    duplicate-client error at reload. Keyed on the router's real
    RADIUS source IP (tunnel 10.10.0.x for a VPN router).

    Enabled → write the client file. Disabled → remove it.
    Best-effort: a provisioning failure never propagates (the DB
    row is already saved); it's logged so the operator can see the
    router won't authenticate until the file is written."""
    source_ip = _radius_source_ip(nas)
    try:
        from .setup_wizard_v3_radius_server_provisioning import (
            write_client_for_nas,
            remove_client_for_nas,
            FreeRadiusProvisioningError,
        )
    except Exception:  # noqa: BLE001
        return
    try:
        if nas.enabled and source_ip and nas.secret:
            write_client_for_nas(
                nas_id=int(nas.id),
                ipaddr=source_ip,
                secret=nas.secret,
                shortname=nas.shortname or nas.name,
                require_message_authenticator=bool(
                    nas.require_message_authenticator,
                ),
            )
        else:
            remove_client_for_nas(nas_id=int(nas.id))
    except FreeRadiusProvisioningError as exc:
        _LOG.warning(
            "freeradius client file sync failed for nas=%s: %s "
            "(row saved; FreeRADIUS will not answer this router "
            "until the file is written)", nas.id, exc,
        )
    except Exception:  # noqa: BLE001
        _LOG.warning(
            "freeradius client file sync crashed for nas=%s",
            nas.id, exc_info=True,
        )


def delete_nas(nas: NasDevice) -> None:
    """Revoke the NAS's FreeRADIUS client file."""
    try:
        from .setup_wizard_v3_radius_server_provisioning import (
            remove_client_for_nas,
        )
        remove_client_for_nas(nas_id=int(nas.id))
    except Exception:  # noqa: BLE001
        _LOG.warning(
            "freeradius client file removal failed for nas=%s",
            nas.id, exc_info=True,
        )


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
