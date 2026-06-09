"""Idempotent demo data for local HobeRadius previews.

The seeder is intentionally conservative:
- it never deletes or resets existing data;
- it tops up empty/undersized demo tables only;
- production auto-seeding is blocked by app.__init__ unless explicitly enabled.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from .core.constants import (
    NAS_TYPE_HOTSPOT,
    NAS_TYPE_PPPOE,
    NAS_VENDOR_MIKROTIK,
    PLAN_TYPE_HYBRID,
    PLAN_TYPE_QUOTA,
    PLAN_TYPE_RECURRING,
    PLAN_TYPE_TIME,
    PLAN_TYPE_UNLIMITED,
    ROLE_SUPER_ADMIN,
    STATUS_DISABLED,
    STATUS_ENABLED,
    STATUS_EXPIRED,
    USER_TYPE_SUBSCRIBER,
)
from .core.tenant import (
    DEFAULT_TENANT_ID,
    TENANT_TIER_PRO,
    Tenant,
    TenantMembership,
)
from .core.types import AccessPlan, CardBatch, NasDevice, Subscriber
from .db.connection import db, transaction
from .db.helpers import now_iso
from .db.repos import (
    accounting_repo,
    admins_repo,
    cards_repo,
    nas_repo,
    plans_repo,
    subscribers_repo,
    tenants_repo,
)

_LOG = logging.getLogger(__name__)
_TENANT = DEFAULT_TENANT_ID


def _count(table: str, *, tenant_id: int | None = _TENANT) -> int:
    if tenant_id is None:
        row = db().execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    else:
        row = db().execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
    return int(row["c"] or 0) if row else 0


def _plans() -> list[int]:
    ids = [p.id for p in plans_repo.list_plans(_TENANT, limit=100) if p.id]
    if ids:
        return ids

    payload = [
        ("نصف ساعة", "T30", PLAN_TYPE_TIME, dict(duration_minutes=30, speed_down_kbps=4000, speed_up_kbps=2000, price=0.25)),
        ("ساعة", "T60", PLAN_TYPE_TIME, dict(duration_minutes=60, speed_down_kbps=8000, speed_up_kbps=4000, price=0.50)),
        ("يوم", "T1D", PLAN_TYPE_TIME, dict(duration_minutes=24 * 60, validity_days=1, speed_down_kbps=10000, speed_up_kbps=5000, price=1.50)),
        ("5 جيجا", "Q5G", PLAN_TYPE_QUOTA, dict(quota_total_mb=5 * 1024, validity_days=30, speed_down_kbps=15000, speed_up_kbps=8000, price=3.00)),
        ("20 جيجا", "Q20G", PLAN_TYPE_QUOTA, dict(quota_total_mb=20 * 1024, validity_days=30, speed_down_kbps=25000, speed_up_kbps=12000, price=8.00)),
        ("عائلي شهري", "RFAM", PLAN_TYPE_RECURRING, dict(validity_days=30, quota_monthly_mb=100 * 1024, speed_down_kbps=30000, speed_up_kbps=15000, concurrent_sessions=3, price=20.00)),
        ("VIP غير محدود", "VIP", PLAN_TYPE_UNLIMITED, dict(validity_days=30, speed_down_kbps=50000, speed_up_kbps=25000, concurrent_sessions=4, price=40.00, color="#7c3aed")),
        ("تجريبي", "TRIAL", PLAN_TYPE_HYBRID, dict(duration_minutes=24 * 60, quota_total_mb=1024, speed_down_kbps=4000, speed_up_kbps=2000, price=0.0)),
    ]
    created: list[int] = []
    for name, code, ptype, extra in payload:
        saved = plans_repo.upsert_plan(
            AccessPlan(id=None, name=name, code=code, plan_type=ptype, **extra)
        )
        if saved.id:
            created.append(saved.id)
    return created


def _seed_tenant_and_admins() -> None:
    if not tenants_repo.get_by_slug("acme"):
        tenants_repo.create_tenant(
            Tenant(
                id=None,
                slug="acme",
                name="ACME ISP",
                display_name="ACME ISP",
                email="ops@acme.local",
                currency="USD",
                plan_tier=TENANT_TIER_PRO,
            )
        )

    if admins_repo.list_admins():
        return

    sa_role = admins_repo.get_role_by_name(ROLE_SUPER_ADMIN)
    op_role = admins_repo.get_role_by_name("operator")
    sa = admins_repo.create_admin(
        username="admin",
        password="admin",
        full_name="المدير العام",
        email="admin@hoberadius.local",
        role_id=sa_role.id if sa_role else None,
        is_super_admin=True,
    )
    op = admins_repo.create_admin(
        username="operator",
        password="operator",
        full_name="مشغل تجريبي",
        email="op@hoberadius.local",
        role_id=op_role.id if op_role else None,
    )
    tenants_repo.add_membership(
        TenantMembership(
            id=None,
            tenant_id=_TENANT,
            admin_id=op.id,
            role_id=op.role_id,
            status="active",
        )
    )


def _seed_nas() -> None:
    if _count("nas_devices") >= 4:
        return
    for n in [
        NasDevice(id=None, name="MT-HQ-Core", address="10.10.0.1", secret="hq-secret", vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_HOTSPOT, location="المقر الرئيسي", description="Demo MikroTik core"),
        NasDevice(id=None, name="MT-Branch-A", address="10.20.0.1", secret="branch-secret", vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_HOTSPOT, location="فرع A"),
        NasDevice(id=None, name="MT-PPPoE", address="10.30.0.1", secret="pppoe-secret", vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_PPPOE, location="PPPoE"),
        NasDevice(id=None, name="MT-Lab", address="10.40.0.1", secret="lab-secret", vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_HOTSPOT, location="مختبر", enabled=False),
    ]:
        try:
            nas_repo.upsert_nas(n)
        except Exception:  # noqa: BLE001
            _LOG.debug("demo NAS already exists: %s", n.name)


def _seed_subscribers(plan_ids: list[int]) -> None:
    if _count("subscribers") >= 25:
        return
    statuses = [STATUS_ENABLED] * 16 + [STATUS_EXPIRED] * 4 + [STATUS_DISABLED] * 2
    first = ["أحمد", "يوسف", "سارة", "ليلى", "محمد", "ندى", "عمر", "رنا", "خالد", "هبة"]
    last = ["العواد", "الزعبي", "الحداد", "السعيد", "الكردي", "المومني", "الخطيب"]
    random.seed(7)
    existing = _count("subscribers")
    for i in range(existing, 28):
        username = f"user{1000 + i}"
        if subscribers_repo.get_subscriber(_TENANT, username, include_deleted=True):
            continue
        subscribers_repo.upsert_subscriber(
            Subscriber(
                id=None,
                username=username,
                password="seed-pwd",
                user_type=USER_TYPE_SUBSCRIBER,
                plan_id=random.choice(plan_ids),
                full_name=f"{random.choice(first)} {random.choice(last)}",
                mobile=f"07{random.randint(70000000, 99999999)}",
                email=f"{username}@example.com",
                status=random.choice(statuses),
                expire_at=datetime.utcnow() + timedelta(days=random.randint(-5, 60)),
                used_seconds=random.randint(0, 600_000),
                used_bytes_in=random.randint(0, 50_000_000_000),
                used_bytes_out=random.randint(0, 10_000_000_000),
            )
        )


def _seed_card_batches(plan_ids: list[int]) -> None:
    if _count("cards") >= 25 and _count("card_batches") >= 3:
        return
    batch_specs = [
        ("حزمة ساعة تجريبية", "D1-", 12, 0.50, "generated"),
        ("حزمة يوم تجريبية", "D2-", 10, 1.50, "imported"),
        ("ملف خارجي للمحاسبة", "EXT-", 8, 0.75, "external"),
    ]
    for idx, (name, prefix, count, price, source_type) in enumerate(batch_specs, start=1):
        batch = cards_repo.create_batch(
            CardBatch(
                id=None,
                batch_code="",
                plan_id=plan_ids[(idx - 1) % len(plan_ids)],
                count=count,
                original_count=count,
                settlement_count=count,
                source_type=source_type,
                package_name=name,
                username_prefix=prefix,
                username_length=8,
                password_length=6,
                price_per_card=price,
                total_price=price * count,
                created_by="demo-seed",
                time_value=1 if idx == 1 else 30,
                time_unit="hours" if idx == 1 else "days",
                device_count=1 + (idx % 2),
            )
        )
        cards = cards_repo.generate_cards(
            tenant_id=_TENANT,
            batch_id=batch.id,
            plan_id=batch.plan_id,
            count=count,
            username_prefix=prefix,
            username_length=8,
            password_length=6,
            expire_at=datetime.utcnow() + timedelta(days=idx * 7),
        )
        _mark_demo_card_states(cards, batch_index=idx)


def _mark_demo_card_states(cards, *, batch_index: int) -> None:
    now = datetime.utcnow()
    with transaction() as conn:
        for idx, card in enumerate(cards):
            mac = f"9E:49:36:{batch_index:02X}:{idx:02X}:A4"
            if idx % 5 == 0:
                conn.execute(
                    """
                    UPDATE cards
                    SET used = 1, first_used_at = ?, used_by_mac = ?, expire_at = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (
                        (now - timedelta(days=idx + 1)).isoformat(),
                        mac,
                        (now - timedelta(days=1)).isoformat() if idx % 10 == 0 else (now + timedelta(days=20)).isoformat(),
                        _TENANT,
                        card.id,
                    ),
                )
            if idx % 9 == 0:
                conn.execute(
                    "UPDATE cards SET revoked = 1 WHERE tenant_id = ? AND id = ?",
                    (_TENANT, card.id),
                )
            if idx % 13 == 0:
                conn.execute(
                    """
                    UPDATE cards
                    SET deleted_at = ?, deleted_by = ?, delete_reason = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (now_iso(), "demo-seed", "demo archived card", _TENANT, card.id),
                )
            _insert_demo_session(conn, username=card.username, mac=mac, index=idx)


def _insert_demo_session(conn, *, username: str, mac: str, index: int) -> None:
    started = datetime.utcnow() - timedelta(hours=index + 1)
    stopped = None if index % 4 == 0 else started + timedelta(minutes=20 + index)
    conn.execute(
        """
        INSERT INTO radacct(
            tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
            nasportid, acctstarttime, acctupdatetime, acctstoptime,
            acctsessiontime, acctinputoctets, acctoutputoctets,
            callingstationid, framedipaddress, servicetype
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            _TENANT,
            f"demo-{username}-{index}",
            f"demo-{username}-{index}",
            username,
            "10.10.0.1",
            f"ether{(index % 8) + 1}",
            started.isoformat(),
            (stopped or datetime.utcnow()).isoformat(),
            stopped.isoformat() if stopped else None,
            int(((stopped or datetime.utcnow()) - started).total_seconds()),
            1024 * (100 + index),
            1024 * (250 + index * 2),
            mac,
            f"10.20.30.{50 + (index % 150)}",
            "Login-User",
        ),
    )


def _seed_finance() -> None:
    if _count("payment_transactions") >= 15:
        return
    subs = [
        dict(r)
        for r in db().execute(
            """
            SELECT * FROM subscribers
            WHERE tenant_id = ? AND deleted_at IS NULL
            ORDER BY id LIMIT 20
            """,
            (_TENANT,),
        ).fetchall()
    ]
    for idx, sub in enumerate(subs[:18], start=1):
        plan = accounting_repo.resolve_plan(_TENANT, sub.get("plan_id"))
        try:
            accounting_repo.create_payment(
                tenant_id=_TENANT,
                subscriber=sub,
                plan=plan,
                amount=float(3 + (idx % 7)),
                currency="JOD",
                method="cash" if idx % 2 else "manual",
                created_by="demo-seed",
                plan_price=float((plan or {}).get("price") or 10),
                custom_price=None,
                discount_amount=0.0 if idx % 3 else 1.0,
                discount_reason="demo discount" if idx % 3 == 0 else "",
                effective_price=float((plan or {}).get("price") or 10),
                earned_minutes=60 * (idx % 8 + 1),
                rounding_mode="floor",
                notes="demo payment",
                metadata={"demo": True},
            )
        except Exception:  # noqa: BLE001
            _LOG.debug("demo payment skipped for %s", sub.get("username"))
    if _count("loan_entries") >= 6:
        return
    for idx, sub in enumerate(subs[:8], start=1):
        starts = datetime.utcnow()
        ends = starts + timedelta(hours=idx)
        try:
            accounting_repo.create_loan(
                tenant_id=_TENANT,
                subscriber=sub,
                duration_minutes=idx * 60,
                amount=float(idx),
                currency="JOD",
                reason="demo loan",
                created_by="demo-seed",
                starts_at=starts.isoformat(),
                ends_at=ends.isoformat(),
                max_limit_snapshot=24 * 60,
                metadata={"demo": True},
            )
        except Exception:  # noqa: BLE001
            _LOG.debug("demo loan skipped for %s", sub.get("username"))


def seed_demo_data(*, force: bool = False) -> dict[str, int]:
    """Top up local/demo data and return a visible summary."""
    _seed_tenant_and_admins()
    _seed_nas()
    plan_ids = _plans()
    if not plan_ids:
        return {"plans": 0}

    # `force` means "top up all demo domains", not destructive reset.
    _seed_subscribers(plan_ids)
    _seed_card_batches(plan_ids)
    _seed_finance()

    summary = {
        "tenants": _count("tenants", tenant_id=None),
        "admins": _count("admins", tenant_id=None),
        "plans": _count("access_plans"),
        "subscribers": _count("subscribers"),
        "nas": _count("nas_devices"),
        "card_batches": _count("card_batches"),
        "cards": _count("cards"),
        "sessions": _count("radacct"),
        "payments": _count("payment_transactions"),
        "loans": _count("loan_entries"),
    }
    if force:
        _LOG.info("seed_demo_data(force=True): %s", summary)
    else:
        _LOG.info("seed_demo_data: %s", summary)
    return summary
