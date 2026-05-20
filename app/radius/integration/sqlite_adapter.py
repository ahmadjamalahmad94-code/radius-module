"""
SqliteAdapter — RadiusAdapter مدعوم بـ SQLite + MikroTik live integration.

- الكتابة: تحفظ في DB أولًا، ثم enqueue sync لـ MT routers.
- القراءة: من DB، عدا live sessions تأتي مباشرة من MT.
- disconnect: enqueue (يُنفّذ خلال ثوانٍ من sync_worker).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Optional, Sequence

from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import (
    AccessPlan,
    AccountingSession,
    NasDevice,
    OnlineSession,
    RadiusPolicy,
    RadiusSettings,
    Subscriber,
)
from ..db.repos import cards_repo, mikrotik_repo, nas_repo, plans_repo, subscribers_repo
from .adapter import RadiusAdapter, register_adapter

_LOG = logging.getLogger(__name__)


def _tid() -> int:
    try:
        from flask import g
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (ImportError, RuntimeError):
        return DEFAULT_TENANT_ID


class SqliteAdapter(RadiusAdapter):
    mode = "sqlite"

    def settings(self) -> RadiusSettings:
        return RadiusSettings(
            mode=self.mode, api_ready=True, api_writes_enabled=True,
            base_url="sqlite", timeout_sec=0,
        )

    def healthcheck(self) -> bool:
        from ..db.connection import db
        try:
            db().execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    # ─────────────── NAS ───────────────

    def list_nas(self, *, limit: int = 100, offset: int = 0) -> Sequence[NasDevice]:
        return nas_repo.list_nas(_tid(), limit=limit, offset=offset)

    def get_nas(self, nas_id: int) -> NasDevice:
        d = nas_repo.get_nas(_tid(), nas_id)
        if not d:
            from ..core.errors import RadiusNotFound
            raise RadiusNotFound(f"NAS {nas_id} غير موجود")
        return d

    def upsert_nas(self, device: NasDevice) -> NasDevice:
        d = replace(device, tenant_id=device.tenant_id or _tid())
        return nas_repo.upsert_nas(d)

    def delete_nas(self, nas_id: int) -> None:
        nas_repo.delete_nas(_tid(), nas_id)

    # ─────────────── Plans ───────────────

    def list_profiles(self, *, limit: int = 100, offset: int = 0) -> Sequence[AccessPlan]:
        return plans_repo.list_plans(_tid(), limit=limit, offset=offset)

    def get_profile(self, profile_id: int) -> AccessPlan:
        p = plans_repo.get_plan(_tid(), profile_id)
        if not p:
            from ..core.errors import RadiusNotFound
            raise RadiusNotFound(f"plan {profile_id} غير موجود")
        return p

    def upsert_profile(self, profile: AccessPlan) -> AccessPlan:
        p = replace(profile, tenant_id=profile.tenant_id or _tid())
        saved = plans_repo.upsert_plan(p)
        # enqueue sync لـ MT
        from .router_sync import enqueue_plan_upsert
        try: enqueue_plan_upsert(saved)
        except Exception:  # noqa: BLE001
            _LOG.exception("enqueue plan sync failed (saved in DB, MT pending)")
        return saved

    def delete_profile(self, profile_id: int) -> None:
        # احتجز الاسم قبل الحذف لتمريره
        plans_repo.archive_plan(_tid(), profile_id, actor="adapter")
        archived = plans_repo.get_plan(_tid(), profile_id, include_deleted=True)
        if archived:
            from .router_sync import enqueue_plan_upsert
            try: enqueue_plan_upsert(archived)
            except Exception:  # noqa: BLE001
                _LOG.exception("enqueue archived plan sync failed")

    # ─────────────── Accounts ───────────────

    def list_accounts(self, *, beneficiary_id: Optional[int] = None,
                       status: Optional[str] = None,
                       user_type: Optional[str] = None,
                       search: Optional[str] = None,
                       limit: int = 100, offset: int = 0) -> Sequence[Subscriber]:
        # R9.0: user_type + search يُمرَّران إلى SQL في الـ repo.
        items = subscribers_repo.list_subscribers(
            _tid(), status=status, user_type=user_type, search=search,
            limit=limit, offset=offset,
        )
        if beneficiary_id is not None:
            items = [s for s in items if s.beneficiary_ref == str(beneficiary_id)]
        return items

    def get_account(self, username: str) -> Subscriber:
        s = subscribers_repo.get_subscriber(_tid(), username)
        if not s:
            from ..core.errors import RadiusNotFound
            raise RadiusNotFound(f"account {username!r} غير موجود")
        return s

    def upsert_account(self, account: Subscriber) -> Subscriber:
        s = replace(account, tenant_id=account.tenant_id or _tid())
        saved = subscribers_repo.upsert_subscriber(s)
        from .router_sync import enqueue_subscriber_upsert
        try: enqueue_subscriber_upsert(saved)
        except Exception:  # noqa: BLE001
            _LOG.exception("enqueue subscriber sync failed (saved in DB, MT pending)")
        # webhook event
        try:
            from app.webhooks.dispatcher import dispatch_event
            dispatch_event("account.updated" if account.id else "account.created",
                           {"username": saved.username, "plan_id": saved.plan_id,
                            "status": saved.status},
                           tenant_id=saved.tenant_id)
        except Exception:  # noqa: BLE001
            _LOG.exception("dispatch event failed")
        return saved

    def delete_account(self, username: str) -> None:
        tenant_id = _tid()
        subscribers_repo.archive_subscriber(tenant_id, username, actor="adapter")
        archived = subscribers_repo.get_subscriber(tenant_id, username, include_deleted=True)
        if not archived:
            return
        from .router_sync import enqueue_subscriber_upsert
        try: enqueue_subscriber_upsert(archived)
        except Exception:  # noqa: BLE001
            _LOG.exception("enqueue archived subscriber sync failed")

    def reset_password(self, username: str, new_password: str) -> None:
        if not new_password:
            from ..core.errors import RadiusValidationError
            raise RadiusValidationError("new password required")
        tenant_id = _tid()
        subscribers_repo.reset_password(tenant_id, username, new_password)
        from .router_sync import enqueue_reset_password
        try: enqueue_reset_password(tenant_id, username, new_password)
        except Exception:  # noqa: BLE001
            _LOG.exception("enqueue reset password sync failed")

    # ─────────────── Sessions ───────────────

    def list_online_from_radacct(self, *, limit: int = 200) -> Sequence[OnlineSession]:
        """R8.2: المصدر القياسي للجلسات الحيّة بعد أن أصبح FreeRADIUS
        rlm_sql يكتب radacct على كل Acct-Start/Interim/Stop (R1→R7).

        يقرأ مباشرة من SQLite — لا اتصال بـ MikroTik، microseconds بدل
        عشرات الثواني. الجلسة "حيّة" = `acctstoptime IS NULL`.

        لا يرفع أبدًا — fallback إلى [] عند أي خطأ كي لا نُكسر render
        صفحة /admin/radius/online.
        """
        from ..db.connection import db
        from ..db.helpers import parse_dt
        try:
            rows = db().execute(
                "SELECT acctsessionid, acctuniqueid, username, nasipaddress, "
                "       nasporttype, framedipaddress, callingstationid, "
                "       acctstarttime, acctupdatetime, "
                "       acctinputoctets, acctoutputoctets, tenant_id "
                "  FROM radacct "
                " WHERE tenant_id = ? AND acctstoptime IS NULL "
                " ORDER BY acctstarttime DESC "
                " LIMIT ?",
                (_tid(), limit),
            ).fetchall()
        except Exception:  # noqa: BLE001
            _LOG.exception("list_online_from_radacct: query failed — returning []")
            return []
        return [_radacct_row_to_session(r, parse_dt=parse_dt) for r in rows]

    def list_online(self, *, limit: int = 200) -> Sequence[OnlineSession]:
        """LEGACY: يضرب MT API مباشرة لكل router (synchronous).

        ⚠ لا تستدعِها من request handlers — قد تستغرق دقائق لو router
        غير قابل للوصول. R8.1/R8.2 حوّلا render paths (dashboard + online
        list) لـ radacct عبر list_online_from_radacct(). نُبقي هذه
        الـ method لـ diagnostics / enrichment وقت طلب يدوي.
        """
        from .mikrotik.errors import MikrotikError
        from .mikrotik.pool import acquire as acquire_mt
        out: list[OnlineSession] = []
        tenant_id = _tid()
        for cfg in mikrotik_repo.list_configs(tenant_id):
            if not cfg["enabled"]:
                continue
            try:
                with acquire_mt(cfg) as c:
                    rows = list(c.print_("/ip/hotspot/active/print"))
            except MikrotikError as e:
                _LOG.warning("list_online: router=%s failed: %s", cfg["host"], e)
                continue
            for r in rows:
                out.append(_mt_row_to_session(r, nas_name=cfg["name"], nas_addr=cfg["host"]))
                if len(out) >= limit:
                    return out
        return out

    def disconnect(self, username: str, *, session_id: Optional[str] = None) -> None:
        """enqueue — sync_worker سيُنفّذ خلال ثوانٍ."""
        from .router_sync import enqueue_disconnect
        enqueue_disconnect(_tid(), username)
        try:
            from app.webhooks.dispatcher import dispatch_event
            dispatch_event("session.disconnected", {"username": username},
                           tenant_id=_tid())
        except Exception: pass

    # ─────────────── Accounting (من radacct المحلي) ───────────────

    def list_accounting(self, *, username: Optional[str] = None,
                         limit: int = 100, offset: int = 0) -> Sequence[AccountingSession]:
        from ..db.connection import db
        from ..db.helpers import parse_dt
        sql = "SELECT * FROM radacct WHERE tenant_id = ?"
        vals: list = [_tid()]
        if username:
            sql += " AND username = ?"; vals.append(username)
        sql += " ORDER BY radacctid DESC LIMIT ? OFFSET ?"
        vals += [limit, offset]
        rows = db().execute(sql, vals).fetchall()
        out: list[AccountingSession] = []
        for r in rows:
            out.append(AccountingSession(
                id=r["radacctid"], tenant_id=r["tenant_id"],
                username=r["username"], session_id=r["acctsessionid"],
                nas_id=r["nasipaddress"],
                started_at=parse_dt(r["acctstarttime"]) or datetime.utcnow(),
                stopped_at=parse_dt(r["acctstoptime"]),
                duration_sec=r["acctsessiontime"] or 0,
                bytes_in=r["acctinputoctets"] or 0,
                bytes_out=r["acctoutputoctets"] or 0,
                acct_unique_id=r["acctuniqueid"] or "",
                nas_port_type=r["nasporttype"] or "",
                called_station_id=r["calledstationid"] or "",
                calling_station_id=r["callingstationid"] or "",
                terminate_cause=r["acctterminatecause"] or "",
                service_type=r["servicetype"] or "",
                framed_protocol=r["framedprotocol"] or "",
                framed_ip=r["framedipaddress"] or "",
            ))
        return out

    # ─────────────── Policies (لاحقًا) ───────────────

    def list_policies(self) -> Sequence[RadiusPolicy]:
        return []

    def upsert_policy(self, policy: RadiusPolicy) -> RadiusPolicy:
        return policy

    def delete_policy(self, policy_id: int) -> None:
        return None


# ─────────────── mappers ───────────────


def _mt_row_to_session(r: dict, *, nas_name: str, nas_addr: str) -> OnlineSession:
    """يحوّل /ip/hotspot/active/print row إلى OnlineSession."""
    return OnlineSession(
        username=r.get("user") or "",
        session_id=r.get(".id") or "",
        nas_id=nas_name, nas_address=nas_addr,
        framed_ip=r.get("address") or "",
        mac_address=r.get("mac-address") or "",
        started_at=datetime.utcnow(),  # MT يعطي uptime relative؛ نضع وقت الالتقاط
        last_update_at=datetime.utcnow(),
        bytes_in=_safe_int(r.get("bytes-in")),
        bytes_out=_safe_int(r.get("bytes-out")),
        rate_down_kbps=_parse_rate_kbps(r.get("rate-limit-rx")),
        rate_up_kbps=_parse_rate_kbps(r.get("rate-limit-tx")),
    )


def _radacct_row_to_session(r, *, parse_dt) -> OnlineSession:
    """R8.2: يحوّل radacct row إلى OnlineSession.

    البنية المُتاحة في radacct تغطي كل الحقول المطلوبة في DTO ما عدا:
      - plan_name + user_type: ليس عمودًا في radacct؛ يبقى افتراضيًا.
      - rate_down/up_kbps: RADIUS لا ينقل rate في acct؛ صفر.
    نُعيد استخدام nasipaddress كـ nas_id و nas_address (لا join مع
    nas_devices لتجنّب query إضافي على كل صفّ — تحسين قابل لاحقاً).
    started_at fallback إلى utcnow() لو الـ DB row فاسد.
    """
    started = parse_dt(r["acctstarttime"]) or datetime.utcnow()
    updated = parse_dt(r["acctupdatetime"]) or started
    nas_ip = r["nasipaddress"] or ""
    return OnlineSession(
        username=r["username"] or "",
        session_id=r["acctsessionid"] or "",
        nas_id=nas_ip, nas_address=nas_ip,
        framed_ip=r["framedipaddress"] or "",
        mac_address=r["callingstationid"] or "",
        started_at=started,
        last_update_at=updated,
        tenant_id=int(r["tenant_id"]) if r["tenant_id"] is not None else 1,
        bytes_in=_safe_int(r["acctinputoctets"]),
        bytes_out=_safe_int(r["acctoutputoctets"]),
        nas_port_type=r["nasporttype"] or "",
    )


def _safe_int(v) -> int:
    try: return int(v or 0)
    except (TypeError, ValueError): return 0


def _parse_rate_kbps(s) -> int:
    if not s: return 0
    s = str(s).strip()
    mul = 1
    if s.endswith("k") or s.endswith("K"): s = s[:-1]
    elif s.endswith("M") or s.endswith("m"): mul = 1000; s = s[:-1]
    elif s.endswith("G") or s.endswith("g"): mul = 1_000_000; s = s[:-1]
    try: return int(float(s) * mul)
    except ValueError: return 0


register_adapter("sqlite", SqliteAdapter)
__all__ = ["SqliteAdapter"]
