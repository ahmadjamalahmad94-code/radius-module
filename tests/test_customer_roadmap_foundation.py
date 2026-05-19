from __future__ import annotations

from pathlib import Path

from app.radius.core import customer_roadmap as roadmap


def test_customer_roadmap_domains_are_unique_and_complete():
    assert len(roadmap.CUSTOMER_ROADMAP_DOMAINS) == len(set(roadmap.CUSTOMER_ROADMAP_DOMAINS))
    assert roadmap.DOMAIN_SUBSCRIBER_LOANS in roadmap.CUSTOMER_ROADMAP_DOMAINS
    assert roadmap.DOMAIN_ACCOUNTING_LEDGER in roadmap.CUSTOMER_ROADMAP_DOMAINS
    assert roadmap.DOMAIN_CARD_CHECKER in roadmap.CUSTOMER_ROADMAP_DOMAINS


def test_delete_policy_targets_do_not_overlap():
    overlap = set(roadmap.SOFT_DELETE_TARGETS) & set(roadmap.FINANCIAL_APPEND_ONLY_TARGETS)
    assert overlap == set()


def test_delete_policy_classifies_sensitive_and_financial_targets():
    assert roadmap.deletion_policy_for("subscribers") == roadmap.DELETE_POLICY_SOFT_DELETE
    assert roadmap.deletion_policy_for("card_batches") == roadmap.DELETE_POLICY_SOFT_DELETE
    assert roadmap.deletion_policy_for("invoices") == roadmap.DELETE_POLICY_FINANCIAL_APPEND_ONLY
    assert roadmap.deletion_policy_for("subscriber_loans") == roadmap.DELETE_POLICY_FINANCIAL_APPEND_ONLY
    assert roadmap.deletion_policy_for("unknown_entity") == roadmap.DELETE_POLICY_UNSPECIFIED


def test_customer_roadmap_audit_actions_are_namespaced_and_unique():
    actions = roadmap.CUSTOMER_ROADMAP_AUDIT_ACTIONS
    assert len(actions) == len(set(actions))
    assert all("." in action for action in actions)
    assert roadmap.AUDIT_ACTION_SOFT_DELETED in actions
    assert roadmap.AUDIT_ACTION_FINANCIAL_REVERSAL in actions


def test_customer_roadmap_docs_exist():
    root = Path(__file__).resolve().parents[1]
    requirements = root / "docs" / "roadmap" / "CUSTOMER_RADIUS_REQUIREMENTS.md"
    traceability = root / "docs" / "roadmap" / "CUSTOMER_REQUIREMENTS_TRACEABILITY.md"

    assert requirements.exists()
    assert traceability.exists()
    assert "not part of `radius-module-admin`" in requirements.read_text(encoding="utf-8")
    assert "Subscriber loan/credit system" in traceability.read_text(encoding="utf-8")
