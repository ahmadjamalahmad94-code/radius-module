"""
DTOs لـ HobeRadius — dataclasses خالصة، لا I/O.
كل DTO يحمل tenant_id (default = DEFAULT_TENANT_ID).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

from .tenant import DEFAULT_TENANT_ID


@dataclass(frozen=True)
class NasDevice:
    id: Optional[int]
    name: str
    address: str
    secret: str
    vendor: str
    tenant_id: int = DEFAULT_TENANT_ID
    nas_type: str = "hotspot"
    shortname: str = ""
    ports: int = 0
    snmp_community: str = ""
    auth_port: int = 1812
    acct_port: int = 1813
    coa_port: int = 3799
    api_port: int = 8728
    api_user: str = ""
    api_password: str = ""
    api_use_tls: bool = False
    location: str = ""
    coordinates: str = ""
    monitoring_enabled: bool = True
    description: str = ""
    enabled: bool = True
    last_seen_at: Optional[datetime] = None
    # ── RM-H5: AdvRadius extension (migration 014) ──
    last_check_at: Optional[datetime] = None
    last_check_status: str = ""              # reachable / timeout / unreachable / ""
    require_message_authenticator: bool = False
    ssh_port: int = 22
    tags: str = ""                           # CSV
    metadata: str = "{}"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class AccessPlan:
    id: Optional[int]
    name: str
    tenant_id: int = DEFAULT_TENANT_ID
    code: str = ""
    plan_type: str = "time"
    service_type: str = "Hotspot"             # Hotspot/PPPoE/Balance/Voucher
    typebp: str = "Limited"                   # Limited/Unlimited
    limit_type: str = "Time_Limit"            # Time_Limit/Data_Limit/Both_Limit
    # وقت
    duration_value: int = 0
    duration_unit: str = "Mins"               # Mins/Hrs/Days/Months/Period
    duration_minutes: int = 0                 # محسوب: للتسوية مع MikroTik
    validity_value: int = 0
    validity_unit: str = "Days"
    validity_days: int = 0
    max_daily_minutes: int = 0
    max_weekly_minutes: int = 0
    max_monthly_minutes: int = 0
    session_timeout_sec: int = 0
    idle_timeout_sec: int = 0
    # كوتا
    data_value: int = 0
    data_unit: str = "MB"                     # MB/GB
    quota_total_mb: int = 0
    quota_daily_mb: int = 0
    quota_monthly_mb: int = 0
    quota_reset_strategy: str = "rolling"
    # سرعة
    bandwidth_id: Optional[int] = None
    speed_up_kbps: int = 0
    speed_down_kbps: int = 0
    burst_up_kbps: int = 0
    burst_down_kbps: int = 0
    burst_threshold_kbps: int = 0
    burst_time_sec: int = 0
    burst_raw: str = ""
    # شبكة
    concurrent_sessions: int = 1
    address_pool: str = ""
    framed_pool: str = ""
    pool_id: Optional[int] = None
    vlan_id: int = 0
    ipv6_pool: str = ""
    bind_mac: bool = False
    bind_ip: bool = False
    force_mac_address: bool = False
    allowed_devices_count: int = 1
    allowed_days: Tuple[str, ...] = field(default_factory=lambda: ("mon","tue","wed","thu","fri","sat","sun"))
    allowed_hours_from: str = ""
    allowed_hours_to: str = ""
    # تشغيل
    on_login: str = ""
    on_logout: str = ""
    auto_renew: bool = False
    router_ids: Tuple[int, ...] = field(default_factory=tuple)
    # تجاري
    price_card: float = 0.0
    price_bulk: float = 0.0
    price: float = 0.0
    currency: str = "JOD"
    plan_tier: str = "Personal"              # Personal/Business
    prepaid: bool = True
    project: str = ""
    description: str = ""
    enabled: bool = True
    priority: int = 100
    color: str = "#2BAACC"
    # ── RM-H3: AdvRadius extension fields (migration 012) ──
    # سرعة متقدمة + CIR + bursts
    speed_control_enabled: bool = False
    cir_down_kbps: int = 0
    cir_up_kbps: int = 0
    burst_enabled: bool = False
    nightly_unlimited_enabled: bool = False
    # كوتا مفصَّلة شهري/يومي (download+upload+combined)
    monthly_download_quota_mb: int = 0
    monthly_upload_quota_mb: int = 0
    monthly_combined_quota_mb: int = 0
    daily_download_quota_mb: int = 0
    daily_upload_quota_mb: int = 0
    daily_combined_quota_mb: int = 0
    # سلوك الاستخدام
    single_use_once: bool = False
    max_consumption_times: int = 0
    ticket_validity_days: int = 0
    working_hours_limit: int = 0
    # خدمات NAS
    hotspot_enabled: bool = False
    ppp_enabled: bool = False
    service_scope: str = "both"               # hotspot/broadband/both
    loan_enabled: bool = False
    max_loan_minutes: int = 0
    speed_override_allowed: bool = False
    # ساعات العرض
    offer_hours_from: str = ""
    offer_hours_to: str = ""
    # metadata JSON ـ مُجمَّعة {general, subscription, advanced, mikrotik, notifications}
    metadata: str = "{}"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class Subscriber:
    id: Optional[int]
    username: str
    password: str
    tenant_id: int = DEFAULT_TENANT_ID
    user_type: str = "subscriber"             # subscriber/card/trial
    service_type: str = "Hotspot"             # Hotspot/PPPoE/Others
    plan_id: Optional[int] = None
    photo_url: str = "/user.default.jpg"
    # PPPoE specifics
    pppoe_username: str = ""
    pppoe_password: str = ""
    pppoe_ip: str = ""
    # شخصي
    full_name: str = ""
    father_name: str = ""
    mobile: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    district: str = ""
    state: str = ""
    zip: str = ""
    coordinates: str = ""
    national_id: str = ""
    account_type: str = "Personal"            # Personal/Business
    # رصيد + تجديد
    balance: float = 0.0
    auto_renewal: bool = True
    # حالة
    status: str = "enabled"                   # enabled/disabled/expired/suspended/pending/banned
    manager_id: Optional[int] = None
    group: str = ""
    pool: str = ""
    first_login_at: Optional[datetime] = None
    expire_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    # شبكة
    mac_lock: Optional[str] = None
    static_ip: Optional[str] = None
    vlan_id: int = 0
    override_concurrent: int = 0
    # ── RM-H1: AdvRadius extension fields ──
    # حساب الإنترنت — overrides per-user للسرعة
    bandwidth_control_enabled: bool = False
    download_speed_kbps: int = 0
    upload_speed_kbps: int = 0
    custom_speed: bool = False
    temporary_speed: bool = False
    # شبكة — DNS/Caller/Connection file
    caller_id: str = ""
    primary_dns_ppp: str = ""
    secondary_dns_ppp: str = ""
    device_connection_file: str = ""
    # معلومات شخصية إضافية
    nationality: str = ""
    country: str = ""
    payment_method: str = ""          # مرجعي فقط
    payment_reference: str = ""       # مرجعي فقط
    # الكوتا والوقت overrides
    total_connection_time_min: int = 0
    daily_connection_time_min: int = 0
    download_quota_mb: int = 0
    upload_quota_mb: int = 0
    combined_quota_mb: int = 0
    connection_time_limit_enabled: bool = False
    quota_limit_enabled: bool = False
    equal_share_download: bool = False
    equal_share_upload: bool = False
    # أيام + أجهزة إضافية
    working_days: str = ""            # CSV: sat,sun,mon...
    device_count: int = 1
    allowed_macs: str = ""            # CSV
    # metadata JSON ـ مُجمَّعة {mikrotik,radius,advanced,notifications}
    # نُخزّنها هنا كنص خام؛ الـ helpers في types.py + repo يحوّلوها.
    metadata: str = "{}"
    # استخدام
    used_seconds: int = 0
    used_bytes_in: int = 0
    used_bytes_out: int = 0
    online_count: int = 0
    # ربط
    beneficiary_ref: str = ""
    card_batch_id: Optional[int] = None
    # ميتا
    remark: str = ""
    created_by: int = 0
    updated_by: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class CardBatch:
    id: Optional[int]
    batch_code: str
    plan_id: int
    count: int
    tenant_id: int = DEFAULT_TENANT_ID
    package_name: str = ""
    generated: int = 0
    used: int = 0
    price_per_card: float = 0.0
    price_bulk: float = 0.0
    total_quota_mb: int = 0
    # توليد
    username_prefix: str = ""
    username_suffix: str = ""
    username_length: int = 8
    include_batch_number: bool = False
    password_length: int = 6
    password_charset: str = "digits"           # digits/alpha/mixed
    # صلاحية
    expire_at: Optional[datetime] = None
    validity_after_first_login_days: int = 0
    # تشغيل
    count_by_seconds: bool = False
    count_from_first_connect: bool = True
    on_quota_exhaust: str = "stop"             # stop/reduce_speed/notify
    switch_to_mac_on_connect: bool = False
    lock_to_mac_on_close: bool = False
    phone_only_login: bool = False
    service_name: str = ""
    notes: str = ""
    manager_id: int = 0
    created_by: str = ""
    created_at: Optional[datetime] = None
    status: str = "active"                     # active / exhausted / revoked
    # ── RM-H4: AdvRadius extension (migration 013) ──
    # توليد
    password_generation_type: str = "medium"   # weak/medium/strong/digits
    random_generation_enabled: bool = True
    starts_with_or_ends_with: str = ""         # prefix/suffix/""
    prefix_or_suffix_value: str = ""
    # وقت
    time_value: int = 0
    time_unit: str = "days"                    # days/hours/minutes
    device_count: int = 1
    duration_mode: str = "time_unit"           # seconds/time_unit
    # سلوك
    auto_renew_after_first_use: bool = False
    transfer_to_student_status_on_connect: bool = False
    close_user_session_on_disconnect: bool = False
    allow_entry_by_previous_card_palestine: bool = False
    # تجاري (مرجعي)
    total_price: float = 0.0
    # metadata JSON
    metadata: str = "{}"
    # R1 lifecycle foundation. These are additive and not globally filtered yet.
    deleted_at: Optional[datetime] = None
    deleted_by: str = ""
    delete_reason: str = ""
    assigned_to: str = ""
    distributor_id: Optional[int] = None


@dataclass(frozen=True)
class Card:
    id: Optional[int]
    batch_id: int
    username: str
    password: str
    plan_id: int
    tenant_id: int = DEFAULT_TENANT_ID
    used: bool = False
    first_used_at: Optional[datetime] = None
    used_by_mac: str = ""
    used_by_subscriber_id: Optional[int] = None
    expire_at: Optional[datetime] = None
    revoked: bool = False
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class OnlineSession:
    username: str
    session_id: str
    nas_id: str
    nas_address: str
    framed_ip: str
    mac_address: str
    started_at: datetime
    last_update_at: datetime
    tenant_id: int = DEFAULT_TENANT_ID
    bytes_in: int = 0
    bytes_out: int = 0
    plan_name: str = ""
    user_type: str = "subscriber"
    nas_port_type: str = ""
    rate_down_kbps: int = 0
    rate_up_kbps: int = 0


@dataclass(frozen=True)
class AccountingSession:
    """مطابق لـ FreeRADIUS radacct + extras."""
    id: Optional[int]
    username: str
    session_id: str
    nas_id: str
    started_at: datetime
    duration_sec: int
    bytes_in: int
    bytes_out: int
    tenant_id: int = DEFAULT_TENANT_ID
    stopped_at: Optional[datetime] = None
    acct_unique_id: str = ""
    groupname: str = ""
    realm: str = ""
    nas_port_id: str = ""
    nas_port_type: str = ""
    update_at: Optional[datetime] = None
    acct_interval: int = 0
    acct_authentic: str = ""
    connectinfo_start: str = ""
    connectinfo_stop: str = ""
    called_station_id: str = ""        # NAS MAC
    calling_station_id: str = ""       # client MAC
    terminate_cause: str = ""
    service_type: str = ""
    framed_protocol: str = ""
    framed_ip: str = ""
    framed_ipv6: str = ""


@dataclass(frozen=True)
class Admin:
    id: Optional[int]
    username: str
    password_hash: str
    full_name: str = ""
    email: str = ""
    mobile: str = ""
    role_id: Optional[int] = None
    is_super_admin: bool = False              # عابر للـ tenants
    enabled: bool = True
    last_login_at: Optional[datetime] = None
    # ── RM-H6: profile + RBAC fields (migration 015) ──
    phone: str = ""
    last_login_ip: str = ""
    profile_notes: str = ""
    avatar_url: str = ""
    tags: str = ""                           # CSV
    metadata: str = "{}"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class Role:
    id: Optional[int]
    name: str
    tenant_id: int = DEFAULT_TENANT_ID
    display_name: str = ""
    description: str = ""
    permissions: Tuple[str, ...] = field(default_factory=tuple)
    is_system: bool = False
    # ── RM-H6: visual ──
    color: str = "#2BAACC"
    metadata: str = "{}"
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class RadiusPolicy:
    id: Optional[int]
    name: str
    policy_type: str
    tenant_id: int = DEFAULT_TENANT_ID
    params: dict = field(default_factory=dict)
    enabled: bool = True
    priority: int = 100
    description: str = ""


@dataclass(frozen=True)
class RadiusAuditEntry:
    id: Optional[int]
    actor: str
    action: str
    target_type: str
    target_id: str
    tenant_id: int = DEFAULT_TENANT_ID
    payload: dict = field(default_factory=dict)
    ip_address: str = ""
    user_agent: str = ""
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class RadiusSettings:
    mode: str
    api_ready: bool
    api_writes_enabled: bool
    base_url: str = ""
    timeout_sec: int = 10


@dataclass(frozen=True)
class DashboardSnapshot:
    total_subscribers: int = 0
    enabled_subscribers: int = 0
    expired_subscribers: int = 0
    total_cards: int = 0
    used_cards: int = 0
    online_now: int = 0
    nas_total: int = 0
    nas_online: int = 0
    plans_total: int = 0
    admins_total: int = 0
    bytes_today_in: int = 0
    bytes_today_out: int = 0
    revenue_today: float = 0.0
    revenue_month: float = 0.0
    recent_actions: tuple = field(default_factory=tuple)
    top_plans: tuple = field(default_factory=tuple)


# Aliases — حفاظًا على الاستيرادات
AccessProfile = AccessPlan
RadiusAccount = Subscriber
