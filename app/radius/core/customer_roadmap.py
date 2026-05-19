"""Customer roadmap domain constants and lifecycle policies.

This module is intentionally non-invasive. It does not change current delete
behavior or create schema. It gives future slices one canonical place to refer
to customer-requested domains, audit event names, and deletion policy classes.
"""
from __future__ import annotations

DOMAIN_SUBSCRIBER_LOANS = "subscriber_loans"
DOMAIN_RECYCLE_BIN = "recycle_bin"
DOMAIN_PROFILE_SPEED_CONTROL = "profile_speed_control"
DOMAIN_BANDWIDTH_SCHEDULES = "bandwidth_schedules"
DOMAIN_ACCOUNTING_LEDGER = "accounting_ledger"
DOMAIN_MANAGERS_DISTRIBUTORS = "managers_distributors"
DOMAIN_CARD_BATCHES = "card_batches"
DOMAIN_REPORTS = "reports"
DOMAIN_CARD_CHECKER = "card_checker"
DOMAIN_CARD_PRINTING = "card_printing"
DOMAIN_BACKUPS = "backups"
DOMAIN_ONLINE_USERS = "online_users"
DOMAIN_NAS_MANAGEMENT = "nas_management"
DOMAIN_OFFER_ADVANCED_OPTIONS = "offer_advanced_options"

CUSTOMER_ROADMAP_DOMAINS: tuple[str, ...] = (
    DOMAIN_SUBSCRIBER_LOANS,
    DOMAIN_RECYCLE_BIN,
    DOMAIN_PROFILE_SPEED_CONTROL,
    DOMAIN_BANDWIDTH_SCHEDULES,
    DOMAIN_ACCOUNTING_LEDGER,
    DOMAIN_MANAGERS_DISTRIBUTORS,
    DOMAIN_CARD_BATCHES,
    DOMAIN_REPORTS,
    DOMAIN_CARD_CHECKER,
    DOMAIN_CARD_PRINTING,
    DOMAIN_BACKUPS,
    DOMAIN_ONLINE_USERS,
    DOMAIN_NAS_MANAGEMENT,
    DOMAIN_OFFER_ADVANCED_OPTIONS,
)

DELETE_POLICY_UNSPECIFIED = "unspecified"
DELETE_POLICY_SOFT_DELETE = "soft_delete"
DELETE_POLICY_FINANCIAL_APPEND_ONLY = "financial_append_only"

SOFT_DELETE_TARGETS: tuple[str, ...] = (
    "subscribers",
    "cards",
    "card_batches",
    "access_plans",
    "nas_devices",
    "admins",
    "roles",
    "bandwidth_profiles",
    "ip_pools",
    "services",
    "share_groups",
    "reports",
)

FINANCIAL_APPEND_ONLY_TARGETS: tuple[str, ...] = (
    "invoices",
    "subscriber_recharges",
    "payments",
    "ledger_entries",
    "settlements",
    "subscriber_loans",
    "distributor_debts",
    "distributor_profits",
)

AUDIT_ACTION_LOAN_GRANTED = "loan.granted"
AUDIT_ACTION_LOAN_SETTLED = "loan.settled"
AUDIT_ACTION_FINANCIAL_VOIDED = "financial.voided"
AUDIT_ACTION_FINANCIAL_REVERSAL = "financial.reversal"
AUDIT_ACTION_SOFT_DELETED = "lifecycle.soft_deleted"
AUDIT_ACTION_RESTORED = "lifecycle.restored"
AUDIT_ACTION_CARD_CHECKED = "card.checked"
AUDIT_ACTION_SPEED_SCHEDULE_APPLIED = "speed_schedule.applied"

CUSTOMER_ROADMAP_AUDIT_ACTIONS: tuple[str, ...] = (
    AUDIT_ACTION_LOAN_GRANTED,
    AUDIT_ACTION_LOAN_SETTLED,
    AUDIT_ACTION_FINANCIAL_VOIDED,
    AUDIT_ACTION_FINANCIAL_REVERSAL,
    AUDIT_ACTION_SOFT_DELETED,
    AUDIT_ACTION_RESTORED,
    AUDIT_ACTION_CARD_CHECKED,
    AUDIT_ACTION_SPEED_SCHEDULE_APPLIED,
)


def deletion_policy_for(target_type: str) -> str:
    """Return the intended future deletion policy for a table/entity name."""
    normalized = (target_type or "").strip().lower()
    if normalized in FINANCIAL_APPEND_ONLY_TARGETS:
        return DELETE_POLICY_FINANCIAL_APPEND_ONLY
    if normalized in SOFT_DELETE_TARGETS:
        return DELETE_POLICY_SOFT_DELETE
    return DELETE_POLICY_UNSPECIFIED


def is_financial_append_only_target(target_type: str) -> bool:
    return deletion_policy_for(target_type) == DELETE_POLICY_FINANCIAL_APPEND_ONLY


def is_soft_delete_target(target_type: str) -> bool:
    return deletion_policy_for(target_type) == DELETE_POLICY_SOFT_DELETE
