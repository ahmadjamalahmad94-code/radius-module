"""P2-T6 — deterministic fixed Framed-IP allocator (CHR Fleet).

Proves the contract in radius-proxy/docs/chr_fleet/04_FIXED_IP_AND_SESSIONS.md:
  * idempotent — same user always gets the same IP;
  * unique — no two users ever share an IP (collisions probe to distinct
    addresses; a forced duplicate is rejected by the UNIQUE constraint);
  * deterministic per-customer /N slicing + cross-customer isolation;
  * network/gateway/broadcast addresses are never handed out;
  * a full slice raises FixedIpExhausted instead of reusing an address.
"""
from __future__ import annotations

import ipaddress
import os
import tempfile

import pytest

from app.radius.db import connection
from app.radius import fixed_ip
from app.radius.core.errors import RadiusConflict, RadiusValidationError


@pytest.fixture
def db(monkeypatch):
    """Fresh isolated SQLite DB per test (the allocator self-creates its table)."""
    tmp = tempfile.mkdtemp(prefix="hr_fixedip_")
    path = os.path.join(tmp, "fixedip.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", path)
    connection.reset_for_tests(path)
    yield
    connection.close_thread_conn()
    connection.reset_for_tests(None)


# A tiny slice to exercise probe + exhaustion deterministically:
# /29 → 8 addresses, usable = {.2 .. .6} = 5 hosts.
TINY = fixed_ip.FixedIpConfig(supernet="10.0.0.0/8", customer_prefix=29)


# ─── idempotency ──────────────────────────────────────────────────────


def test_same_user_always_same_ip(db):
    ip1 = fixed_ip.allocate_fixed_ip("bob@client5", customer_id=5)
    ip2 = fixed_ip.allocate_fixed_ip("bob@client5", customer_id=5)
    assert ip1 == ip2
    assert fixed_ip.framed_ip_for("bob@client5") == ip1


def test_lookup_returns_none_before_allocation(db):
    assert fixed_ip.framed_ip_for("ghost@client5") is None


def test_ip_is_inside_the_customer_slice(db):
    ip = fixed_ip.allocate_fixed_ip("alice@client9", customer_id=9)
    net = fixed_ip.customer_network(9)
    assert ipaddress.ip_address(ip) in net


# ─── uniqueness ───────────────────────────────────────────────────────


def test_many_users_get_distinct_ips(db):
    ips = {fixed_ip.allocate_fixed_ip(f"u{i}@c1", customer_id=1)
           for i in range(300)}
    assert len(ips) == 300  # no duplicates


def test_collisions_probe_to_distinct_addresses(db):
    # 5 usable hosts in the tiny slice; allocate exactly 5 distinct users.
    # Even if some hash to the same start offset, the probe must yield 5
    # *distinct* IPs (uniqueness preserved).
    ips = [fixed_ip.allocate_fixed_ip(f"user{i}@c5", customer_id=5, cfg=TINY)
           for i in range(5)]
    assert len(set(ips)) == 5


def test_full_slice_raises_exhausted(db):
    for i in range(5):
        fixed_ip.allocate_fixed_ip(f"user{i}@c5", customer_id=5, cfg=TINY)
    with pytest.raises(fixed_ip.FixedIpExhausted):
        fixed_ip.allocate_fixed_ip("one-too-many@c5", customer_id=5, cfg=TINY)


def test_reserved_network_gateway_broadcast_excluded(db):
    # Exhaust the /29 slice and assert none of the reserved addresses leaked.
    net = fixed_ip.customer_network(5, TINY)  # 10.0.0.40/29
    reserved = {
        str(net.network_address),                 # .40 network
        str(net.network_address + 1),             # .41 gateway/local-address
        str(net.broadcast_address),               # .47 broadcast
    }
    ips = {fixed_ip.allocate_fixed_ip(f"user{i}@c5", customer_id=5, cfg=TINY)
           for i in range(5)}
    assert reserved.isdisjoint(ips)
    assert ips == {"10.0.0.42", "10.0.0.43", "10.0.0.44", "10.0.0.45", "10.0.0.46"}


def test_assign_specific_duplicate_is_rejected(db):
    ip = fixed_ip.allocate_fixed_ip("owner@c1", customer_id=1)
    # A different user cannot grab the same IP — UNIQUE rejects it.
    with pytest.raises(RadiusConflict):
        fixed_ip.assign_specific_ip("thief@c1", ip, customer_id=1)


# ─── determinism / isolation ──────────────────────────────────────────


def test_cross_customer_slices_isolated(db):
    # Usernames are realm-qualified (globally unique), so two customers'
    # users land in disjoint /16 slices — addresses never collide across
    # customers (04 §4.2).
    a = fixed_ip.allocate_fixed_ip("alice@c1", customer_id=1)
    b = fixed_ip.allocate_fixed_ip("alice@c2", customer_id=2)
    net1 = fixed_ip.customer_network(1)
    net2 = fixed_ip.customer_network(2)
    assert not net1.overlaps(net2)
    assert ipaddress.ip_address(a) in net1
    assert ipaddress.ip_address(b) in net2
    assert a != b


def test_username_is_global_pk_idempotent_regardless_of_customer(db):
    # username is the primary key: the same username string maps to exactly
    # one IP, so a second call (even with a different customer_id) returns the
    # already-assigned address — never a second IP for one user.
    first = fixed_ip.allocate_fixed_ip("bob@client5", customer_id=5)
    again = fixed_ip.allocate_fixed_ip("bob@client5", customer_id=7)
    assert again == first


def test_offset_derivation_is_stable_and_pure():
    # Pure function: identical inputs → identical offset, across processes.
    o1 = fixed_ip._start_offset("bob@client5", 5, 65533)
    o2 = fixed_ip._start_offset("bob@client5", 5, 65533)
    assert o1 == o2
    assert 0 <= o1 < 65533


def test_release_frees_the_mapping(db):
    ip = fixed_ip.allocate_fixed_ip("temp@c1", customer_id=1)
    assert fixed_ip.framed_ip_for("temp@c1") == ip
    assert fixed_ip.release_fixed_ip("temp@c1") is True
    assert fixed_ip.framed_ip_for("temp@c1") is None
    # Re-allocation works again (and is deterministic → same IP).
    assert fixed_ip.allocate_fixed_ip("temp@c1", customer_id=1) == ip


def test_empty_username_rejected(db):
    with pytest.raises(RadiusValidationError):
        fixed_ip.allocate_fixed_ip("   ", customer_id=1)
