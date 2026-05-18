"""
DTOs إضافية للـ SaaS — Invoice, Voucher, Ticket, Service, Bandwidth, IpPool, …

كل DTO هنا مأخوذ من SAAS_DATA_MODEL.md وله tenant_id إجباري.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

from .tenant import DEFAULT_TENANT_ID


# ─────────────── Bandwidth Profile ───────────────

@dataclass(frozen=True)
class BandwidthProfile:
    id: Optional[int]
    name: str
    tenant_id: int = DEFAULT_TENANT_ID
    rate_down: int = 0
    rate_down_unit: str = "Kbps"                # Kbps/Mbps
    rate_up: int = 0
    rate_up_unit: str = "Kbps"
    burst: str = ""                              # raw MT burst string
    priority: int = 0
    created_at: Optional[datetime] = None


# ─────────────── IP Pool ───────────────

@dataclass(frozen=True)
class IpPool:
    id: Optional[int]
    pool_name: str
    range_ip: str                                # 10.0.0.10-10.0.0.250
    tenant_id: int = DEFAULT_TENANT_ID
    local_ip: str = ""
    router_id: Optional[int] = None
    created_at: Optional[datetime] = None


# ─────────────── Voucher (شحن نقدي) ───────────────

@dataclass(frozen=True)
class Voucher:
    id: Optional[int]
    code: str
    amount: float
    tenant_id: int = DEFAULT_TENANT_ID
    plan_id: Optional[int] = None
    status: str = "active"                       # active / used / expired / revoked
    used_by_subscriber_id: Optional[int] = None
    used_at: Optional[datetime] = None
    expire_at: Optional[datetime] = None
    generated_by: int = 0
    created_at: Optional[datetime] = None


# ─────────────── Invoice / Transaction ───────────────

INVOICE_DIR_CHARGE = "charge"
INVOICE_DIR_REFUND = "refund"
INVOICE_DIR_DEPOSIT = "deposit"
INVOICE_DIR_WITHDRAW = "withdraw"
INVOICE_DIR_CREDIT = "credit"

INVOICE_STATUSES = ("pending", "paid", "failed", "refunded", "canceled")


@dataclass(frozen=True)
class Invoice:
    id: Optional[int]
    invoice_number: str                          # F-2026-001
    subscriber_id: int
    username: str
    amount: float
    tenant_id: int = DEFAULT_TENANT_ID
    admin_id: int = 0
    plan_id: Optional[int] = None
    plan_name: str = ""
    service_type: str = "Hotspot"
    router_id: Optional[int] = None
    direction: str = INVOICE_DIR_CHARGE
    balance_before: float = 0.0
    balance_after: float = 0.0
    recharged_on: Optional[datetime] = None      # date+time مدمجَين
    expiration_at: Optional[datetime] = None
    payment_method: str = "cash"
    payment_gateway_id: Optional[int] = None
    status: str = "paid"
    note: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class PaymentGateway:
    id: Optional[int]
    name: str
    type: str                                    # stripe/paypal/manual/cash/credit
    tenant_id: int = DEFAULT_TENANT_ID
    config: dict = field(default_factory=dict)
    enabled: bool = True
    created_at: Optional[datetime] = None


# ─────────────── Recharge (سجل تجديد اشتراك) ───────────────

@dataclass(frozen=True)
class SubscriberRecharge:
    id: Optional[int]
    subscriber_id: int
    username: str
    plan_id: int
    plan_name: str
    tenant_id: int = DEFAULT_TENANT_ID
    recharged_at: Optional[datetime] = None
    expiration_at: Optional[datetime] = None
    status: str = "completed"                    # completed/pending/failed
    payment_method: str = ""
    router_id: Optional[int] = None
    service_type: str = "Hotspot"
    admin_id: int = 0


# ─────────────── Ticket / Complaint ───────────────

TICKET_STATUSES = ("open", "pending", "in_progress", "resolved", "closed")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")


@dataclass(frozen=True)
class Ticket:
    id: Optional[int]
    subscriber_id: int
    subject: str
    tenant_id: int = DEFAULT_TENANT_ID
    category: str = "general"                    # general/billing/connection/hardware
    priority: str = "normal"
    status: str = "open"
    assignee_admin_id: Optional[int] = None
    body: str = ""
    attachments: Tuple[str, ...] = field(default_factory=tuple)
    created_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class TicketReply:
    id: Optional[int]
    ticket_id: int
    body: str
    author_type: str                             # admin / subscriber
    author_id: int
    tenant_id: int = DEFAULT_TENANT_ID
    created_at: Optional[datetime] = None


# ─────────────── Service / Hardware ───────────────

@dataclass(frozen=True)
class Service:
    """معدّات مُؤجَّرة (راوتر منزلي، ONU، مودم...)."""
    id: Optional[int]
    subscriber_id: int
    name: str
    tenant_id: int = DEFAULT_TENANT_ID
    serial: str = ""
    mac: str = ""
    type: str = "router"                          # router/onu/modem/cable
    rent_per_month: float = 0.0
    status: str = "given"                         # given/returned/lost/damaged
    given_at: Optional[datetime] = None
    returned_at: Optional[datetime] = None
    notes: str = ""
    created_at: Optional[datetime] = None


# ─────────────── Subscriber Inbox (رسائل من الإدارة) ───────────────

@dataclass(frozen=True)
class InboxMessage:
    id: Optional[int]
    subscriber_id: int
    subject: str
    body: str
    tenant_id: int = DEFAULT_TENANT_ID
    type: str = "in_app"                          # in_app / sms / email
    read_at: Optional[datetime] = None
    sent_by_admin_id: int = 0
    created_at: Optional[datetime] = None


# ─────────────── Subscriber Custom Field (per-tenant extensibility) ───────────────

@dataclass(frozen=True)
class SubscriberField:
    id: Optional[int]
    subscriber_id: int
    field_name: str
    field_value: str = ""
    tenant_id: int = DEFAULT_TENANT_ID


# ─────────────── Bandwidth Usage Log (daily aggregate للرسومات) ───────────────

@dataclass(frozen=True)
class BandwidthUsageDaily:
    id: Optional[int]
    subscriber_id: int
    day: str                                      # ISO date 2026-05-18
    bytes_in: int
    bytes_out: int
    tenant_id: int = DEFAULT_TENANT_ID
    sessions_count: int = 0
    peak_mbps: float = 0.0


# ─────────────── Notification (للأدمن داخل النظام) ───────────────

@dataclass(frozen=True)
class Notification:
    id: Optional[int]
    admin_id: int
    title: str
    body: str
    tenant_id: int = DEFAULT_TENANT_ID
    type: str = "info"                            # info / success / warn / error
    link: str = ""
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ─────────────── ApiToken ───────────────

@dataclass(frozen=True)
class ApiToken:
    id: Optional[int]
    token_hash: str
    name: str
    tenant_id: int = DEFAULT_TENANT_ID
    scopes: Tuple[str, ...] = field(default_factory=tuple)
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked: bool = False
    created_by: int = 0
    created_at: Optional[datetime] = None


# ─────────────── Webhook Subscription + Delivery ───────────────

@dataclass(frozen=True)
class WebhookSubscription:
    id: Optional[int]
    target_url: str
    secret: str
    tenant_id: int = DEFAULT_TENANT_ID
    enabled_events: Tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class WebhookDelivery:
    id: Optional[int]
    subscription_id: int
    event: str
    event_id: str
    payload: dict
    status: str                                   # queued/delivering/delivered/failed
    tenant_id: int = DEFAULT_TENANT_ID
    attempts: int = 0
    last_status_code: int = 0
    last_response_excerpt: str = ""
    next_attempt_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ─────────────── Scheduled Task ───────────────

@dataclass(frozen=True)
class ScheduledTask:
    id: Optional[int]
    name: str
    kind: str                                     # expire_check/quota_reset/backup/sync_mt
    cron_expr: str
    tenant_id: int = DEFAULT_TENANT_ID
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    enabled: bool = True
    created_at: Optional[datetime] = None
