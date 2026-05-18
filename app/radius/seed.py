"""seed — بيانات ديمو في DB (idempotent)."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from .core.constants import (
    NAS_TYPE_HOTSPOT, NAS_TYPE_PPPOE, NAS_VENDOR_MIKROTIK,
    PLAN_TYPE_HYBRID, PLAN_TYPE_QUOTA, PLAN_TYPE_RECURRING, PLAN_TYPE_TIME, PLAN_TYPE_UNLIMITED,
    ROLE_SUPER_ADMIN, STATUS_DISABLED, STATUS_ENABLED, STATUS_EXPIRED,
    USER_TYPE_SUBSCRIBER,
)
from .core.tenant import DEFAULT_TENANT_ID, TenantMembership, Tenant, TENANT_TIER_PRO
from .core.types import AccessPlan, NasDevice, Subscriber
from .db.repos import admins_repo, nas_repo, plans_repo, subscribers_repo, tenants_repo

_LOG = logging.getLogger(__name__)


def _already_seeded() -> bool:
    """نعتبر النظام مبذورًا لو فيه plans على الـ default tenant."""
    return len(plans_repo.list_plans(DEFAULT_TENANT_ID, limit=1)) > 0


def seed_demo_data() -> None:
    if _already_seeded():
        return

    # ─── tenant ثانٍ للديمو (acme) ───
    if not tenants_repo.get_by_slug("acme"):
        tenants_repo.create_tenant(Tenant(
            id=None, slug="acme", name="ACME ISP", display_name="ACME ISP",
            email="ops@acme.local", currency="USD",
            plan_tier=TENANT_TIER_PRO,
        ))

    # ─── admins ───
    if not admins_repo.list_admins():
        sa_role = admins_repo.get_role_by_name(ROLE_SUPER_ADMIN)
        op_role = admins_repo.get_role_by_name("operator")
        sa = admins_repo.create_admin(
            username="admin", password="admin", full_name="المدير العام",
            email="admin@hoberadius.local",
            role_id=sa_role.id if sa_role else None,
            is_super_admin=True,
        )
        op = admins_repo.create_admin(
            username="operator", password="operator", full_name="مشغل تجريبي",
            email="op@hoberadius.local",
            role_id=op_role.id if op_role else None,
        )
        tenants_repo.add_membership(TenantMembership(
            id=None, tenant_id=DEFAULT_TENANT_ID, admin_id=op.id,
            role_id=op.role_id, status="active",
        ))

    # ─── NAS ───
    for n in [
        NasDevice(id=None, name="MT-HQ-Core", address="10.10.0.1", secret="hq-secret",
                  vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_HOTSPOT,
                  location="المقر الرئيسي", description="MikroTik CCR1009"),
        NasDevice(id=None, name="MT-Branch-Aqaba", address="10.20.0.1", secret="aq-secret",
                  vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_HOTSPOT, location="فرع العقبة"),
        NasDevice(id=None, name="MT-Branch-Irbid", address="10.30.0.1", secret="ir-secret",
                  vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_PPPOE, location="فرع إربد"),
        NasDevice(id=None, name="MT-Hotspot-Mall", address="10.40.0.1", secret="ml-secret",
                  vendor=NAS_VENDOR_MIKROTIK, nas_type=NAS_TYPE_HOTSPOT,
                  location="مول الإلكترون", enabled=False),
    ]:
        nas_repo.upsert_nas(n)

    # ─── Plans ───
    plans_payload = [
        ("⏱ نصف ساعة","T30",PLAN_TYPE_TIME, dict(duration_minutes=30, speed_down_kbps=4000, speed_up_kbps=2000, price=0.25)),
        ("⏱ ساعة","T60",PLAN_TYPE_TIME, dict(duration_minutes=60, speed_down_kbps=8000, speed_up_kbps=4000, price=0.50)),
        ("⏱ يوم","T1D",PLAN_TYPE_TIME, dict(duration_minutes=24*60, validity_days=1, speed_down_kbps=10000, speed_up_kbps=5000, price=1.50)),
        ("📦 5 جيجا","Q5G",PLAN_TYPE_QUOTA, dict(quota_total_mb=5*1024, validity_days=30, speed_down_kbps=15000, speed_up_kbps=8000, price=3.00)),
        ("📦 20 جيجا","Q20G",PLAN_TYPE_QUOTA, dict(quota_total_mb=20*1024, validity_days=30, speed_down_kbps=25000, speed_up_kbps=12000, price=8.00)),
        ("🔁 عائلي شهري","RFAM",PLAN_TYPE_RECURRING, dict(validity_days=30, quota_monthly_mb=100*1024, speed_down_kbps=30000, speed_up_kbps=15000, concurrent_sessions=3, price=20.00)),
        ("⚡ غير محدود VIP","VIP",PLAN_TYPE_UNLIMITED, dict(validity_days=30, speed_down_kbps=50000, speed_up_kbps=25000, concurrent_sessions=4, price=40.00, color="#7c3aed")),
        ("🧪 تجريبي","TRIAL",PLAN_TYPE_HYBRID, dict(duration_minutes=24*60, quota_total_mb=1024, speed_down_kbps=4000, speed_up_kbps=2000, price=0.0)),
    ]
    plan_ids: list[int] = []
    for name, code, ptype, extra in plans_payload:
        saved = plans_repo.upsert_plan(AccessPlan(id=None, name=name, code=code, plan_type=ptype, **extra))
        plan_ids.append(saved.id)

    # ─── Subscribers ───
    statuses = [STATUS_ENABLED]*10 + [STATUS_EXPIRED]*2 + [STATUS_DISABLED]
    first = ["أحمد","يوسف","سارة","ليلى","محمد","ندى","عمر","رنا","خالد","هبة","ربا","فادي"]
    last  = ["العواد","الزعبي","الحداد","السعيد","الكردي","المومني","الشرع","الخطيب","العبد"]
    random.seed(7)
    for i in range(36):
        username = f"user{1000 + i}"
        full_name = f"{random.choice(first)} {random.choice(last)}"
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username=username, password="seed-pwd",
            user_type=USER_TYPE_SUBSCRIBER, plan_id=random.choice(plan_ids),
            full_name=full_name, mobile=f"07{random.randint(70000000, 99999999)}",
            email=f"{username}@example.com", status=random.choice(statuses),
            expire_at=datetime.utcnow() + timedelta(days=random.randint(-5, 60)),
            used_seconds=random.randint(0, 600_000),
            used_bytes_in=random.randint(0, 50_000_000_000),
            used_bytes_out=random.randint(0, 10_000_000_000),
        ))

    _LOG.info("seed_demo_data: completed")
