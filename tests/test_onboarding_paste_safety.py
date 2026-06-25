# -*- coding: utf-8 -*-
"""Onboarding script — line-by-line paste safety (RouterOS scoping).

The onboarding script is pasted into the RouterOS console LINE BY LINE. RouterOS
scopes a `:local` to its own console command, so a construct that declares a
`:local` on one line and uses `$var`/`:set var` on the *next* line breaks with a
`syntax error` (hit live on RouterOS 7.20.6 / CCR1009 in the firewall move-to-top
loop). Every such construct must live on ONE semicolon-joined line.

These tests assert that invariant generically so future edits can't regress it.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import re

from app.radius.services.router_onboarding_script import (
    OnboardingParams, build_onboarding_script,
)


def _params(**over):
    base = dict(
        router_name="مقهى النور", router_id=7,
        accel_host="187.77.70.18", sstp_port=443,
        tunnel_user="rtr-cafe-noor", tunnel_password="Uniq-Pw-abc123XYZ",
        tunnel_ip="10.50.0.5", radius_ip="10.50.0.1",
        radius_secret="per-nas-secret-9931", api_user="hobe-api",
        api_password="api-uniq-77team",
        walled_garden=["renew.hoberadius.com", "1.2.3.4"],
        block_page_url="http://renew.hoberadius.com", hotspot_pool="10.5.50.0/24",
        pppoe_pool="10.5.60.0/24",
    )
    base.update(over)
    return OnboardingParams(**base)


def _script():
    return build_onboarding_script(_params())


# ── the core invariant: no :local declared on one line, used on the next ──
def test_no_local_var_used_on_a_separate_line():
    """For every `:local <name>` on a line, that SAME line must also reference
    `$<name>` (semicolon-joined). A declaration whose only uses are on later
    lines would go out of scope on a line-by-line paste."""
    lines = _script().splitlines()
    decl = re.compile(r":local\s+([A-Za-z_]\w*)")
    offenders = []
    for i, line in enumerate(lines):
        for name in decl.findall(line):
            # the declaration line must use the variable too
            if not re.search(r"\$" + re.escape(name) + r"\b", line):
                offenders.append((i + 1, name, line.strip()))
    assert not offenders, f"`:local` used across separate pasted lines: {offenders}"


def test_no_local_immediately_followed_by_use_line():
    """Belt-and-braces phrasing of the owner's spec: a `:local X` line is never
    immediately followed by a *separate* line that references `$X`."""
    lines = _script().splitlines()
    decl = re.compile(r"^\s*:local\s+([A-Za-z_]\w*)")
    for i, line in enumerate(lines[:-1]):
        m = decl.match(line)
        if m:
            nxt = lines[i + 1]
            assert not re.search(r"\$" + re.escape(m.group(1)) + r"\b", nxt), (
                f"line {i+1} `:local {m.group(1)}` is followed by a separate "
                f"line using ${m.group(1)}: {nxt.strip()!r}")


# ── no pasted line is a dangling/incomplete construct ──
def test_no_line_dangles_an_open_block_or_paren():
    """A line ending in `do={`, an unbalanced `{`/`(`, or a trailing `;` would
    be an incomplete console command on a line-by-line paste. Skip comments."""
    for i, line in enumerate(_script().splitlines()):
        st = line.strip()
        if not st or st.startswith("#"):
            continue
        assert not st.endswith("do={"), f"line {i+1} dangles `do={{`: {st!r}"
        assert st.count("{") == st.count("}"), \
            f"line {i+1} has unbalanced braces: {st!r}"
        assert st.count("(") == st.count(")"), \
            f"line {i+1} has unbalanced parens: {st!r}"


# ── the move-to-top loop specifically (the line that broke live) ──
def test_move_to_top_is_a_single_self_contained_line():
    matches = [ln for ln in _script().splitlines() if ":local hrPos" in ln]
    assert len(matches) == 1, f"expected exactly one move line, got {matches}"
    line = matches[0].strip()
    # everything the loop needs is on this one line
    assert line.startswith(":local hrPos 0;")
    assert ":foreach r in=" in line
    assert "/ip firewall filter move $r destination=$hrPos" in line
    assert ":set hrPos ($hrPos + 1)" in line
    # balanced + matches the owner's verified one-liner shape
    assert line.count("{") == line.count("}") == 1


# ── RouterOS-7 sanity: the find-comment regex anchor + scheduler/netwatch ──
def test_move_loop_targets_only_our_rules():
    line = [ln for ln in _script().splitlines() if ":local hrPos" in ln][0]
    # only rules tagged with our prefix get moved (anchored regex)
    assert 'find comment~"^hr-fw:"' in line


def test_self_heal_on_event_is_one_line():
    """The scheduler/netwatch `on-event`/`down-script` values must be single
    console lines (no embedded newline would survive the add command)."""
    sched = [ln for ln in _script().splitlines()
             if "/system scheduler add" in ln]
    assert len(sched) == 1 and "on-event=" in sched[0]
    net = [ln for ln in _script().splitlines() if "/tool netwatch add" in ln]
    assert len(net) == 1 and "down-script=" in net[0]
