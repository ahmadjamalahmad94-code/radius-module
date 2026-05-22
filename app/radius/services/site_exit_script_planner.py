"""site_exit_script_planner — VX2 RouterOS v7 plan builder (pure).

Turns a policy + exit-node + targets into a **structured plan**
of MikroTik commands. The plan is data only — no text is
rendered here. VX2.3's renderer converts the plan into RouterOS
v7 `/import`-style script lines.

**Safety contract enforced by the planner:**

  1. Never generates a default route in the main routing
     table — every `0.0.0.0/0` route has `routing-table=` set
     to the policy-private table.
  2. Mangle rules always use `dst-address-list=`, never bare
     `dst-address=` for catch-all destinations.
  3. Every managed command's `attrs["comment"]` starts with
     the policy comment prefix (`HOBE_VX2_SITE_EXIT:<id>:`)
     so cleanup/rollback can find them safely.
  4. Refuses to plan when:
       - exit_node is disabled
       - exit_node has no wireguard_interface_name
       - 0 active targets
       - any target value would route all-traffic (defense-in-
         depth — the validator should already block this)
  5. Cleanup runs BEFORE the new adds in the script ordering,
     so re-running the same plan is idempotent.

The planner is fully pure — no DB calls, no network. Callers
fetch policy/node/targets via the repo layer and hand them in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ─── Stable naming convention ────────────────────────────────


# These prefixes are part of the public safety contract.
# Cleanup / rollback identifies "rules we own" by matching this
# exact substring in the comment.
COMMENT_TAG = "HOBE_VX2_SITE_EXIT"


# Operator-facing FastTrack advisory — emitted on every plan.
# Localized in Arabic to match the rest of the operator UI.
FASTTRACK_WARNING_AR = (
    "FastTrack قد يتجاوز قاعدة الـ mangle لهذه السياسة، فينفذ "
    "المسار عبر الـ WAN الأصلي بدل نفق VPS. استبعد عناوين "
    "الـ address-list هذه من FastTrack يدويًا، أو تأكَّد أنّ "
    "قاعدة FastTrack تأتي بعد قاعدة الـ mangle الخاصة بـ VX2."
)


def routing_table_name(policy_id: int) -> str:
    return f"HOBE_VX2_{int(policy_id)}"


def address_list_name(policy_id: int) -> str:
    return f"HOBE_VX2_DST_{int(policy_id)}"


def comment_prefix(policy_id: int) -> str:
    """Every managed command's comment STARTS with this. The
    trailing colon is intentional — it makes substring matches
    in cleanup unambiguous (`prefix42` doesn't match `prefix4`)."""
    return f"{COMMENT_TAG}:{int(policy_id)}:"


# ─── Structured plan dataclasses ─────────────────────────────


@dataclass(frozen=True)
class PlanCommand:
    """One structured RouterOS call. `kind` is the verb the
    renderer formats:
      - "add"    → ``/path add k=v k=v ... comment="..."``
      - "remove" → ``/path remove [find comment~"prefix"]``
    `path` follows the existing project convention with
    forward-slash segments (e.g. ``/ip/firewall/address-list``).
    """
    section: str         # "address-list" | "dns" | "routing-table"
                         # | "route" | "mangle" | "firewall-filter"
                         # | "cleanup"
    path: str            # "/ip/firewall/address-list"
    kind: str            # "add" | "remove"
    attrs: dict[str, str] = field(default_factory=dict)
    find_pattern: str = ""   # only for kind="remove" — used by
                              # renderer to emit `[find ...]`
    note: str = ""       # human-readable annotation for the
                         # preview pane; never lands in the
                         # actual RouterOS script.


@dataclass(frozen=True)
class SkippedTarget:
    value: str
    reason: str


@dataclass(frozen=True)
class ScriptPlan:
    policy_id: int
    address_list: str
    routing_table: str
    routing_mark: str
    comment_prefix: str

    cleanup_ops:         tuple[PlanCommand, ...] = ()
    routing_table_ops:   tuple[PlanCommand, ...] = ()
    address_list_ops:    tuple[PlanCommand, ...] = ()
    dns_ops:             tuple[PlanCommand, ...] = ()
    route_ops:           tuple[PlanCommand, ...] = ()
    mangle_ops:          tuple[PlanCommand, ...] = ()
    firewall_filter_ops: tuple[PlanCommand, ...] = ()

    rollback_ops:        tuple[PlanCommand, ...] = ()

    warnings:         tuple[str, ...] = ()
    blocking_errors:  tuple[str, ...] = ()
    targets_skipped:  tuple[SkippedTarget, ...] = ()

    @property
    def can_apply(self) -> bool:
        return not self.blocking_errors

    @property
    def all_managed_commands(self) -> tuple[PlanCommand, ...]:
        """Every forward-direction command — used by tests and
        the renderer to walk in a stable order."""
        return (
            self.cleanup_ops
            + self.routing_table_ops
            + self.route_ops
            + self.address_list_ops
            + self.dns_ops
            + self.mangle_ops
            + self.firewall_filter_ops
        )

    @property
    def total_commands(self) -> int:
        return len(self.all_managed_commands)


# ─── Constants ───────────────────────────────────────────────


_TARGET_STATUS_ACTIVE = "active"
_HARD_REJECT_DESTS = frozenset({"0.0.0.0/0"})


# ─── Public API ──────────────────────────────────────────────


def build_plan(
    *,
    policy: dict,
    exit_node: dict,
    targets: Iterable[dict],
    wan_interface_list: Optional[str] = None,
    enable_dns_helper: bool = False,
) -> ScriptPlan:
    """Build the plan. Never raises for operator-fixable input —
    issues land in `blocking_errors` or `warnings` instead.

    Args:
      policy   — site_exit_policies_repo row (dict).
      exit_node — vps_exit_nodes_repo row (dict). MUST be enabled
                  and have a wireguard_interface_name.
      targets   — iterable of site_exit_targets_repo rows. Only
                  rows with status='active' are included.
      wan_interface_list — name of the MikroTik interface-list
                  that represents WAN (e.g. "WAN"). Needed for
                  fail_mode=block_when_vps_down. When None and
                  fail_mode is block_when_vps_down, the planner
                  emits a warning and skips the failsafe rule.
      enable_dns_helper — opt-in DNS static helper for subdomain
                  coverage. Off by default — clients must use
                  router-controlled DNS for it to work.
    """
    pid = int(policy.get("id") or 0)
    if pid <= 0:
        return _blocking(
            policy_id=0,
            reason="policy.id missing or invalid",
        )

    rt_name      = routing_table_name(pid)
    al_name      = address_list_name(pid)
    cprefix      = comment_prefix(pid)
    fail_mode    = (policy.get("fail_mode")
                    or "block_when_vps_down").strip()
    include_subs = bool(policy.get("include_subdomains") or 0)
    include_outp = bool(policy.get("include_router_output") or 0)

    blocking: list[str] = []
    warnings: list[str] = []
    skipped:  list[SkippedTarget] = []

    # ── Exit node sanity ──
    if not exit_node:
        blocking.append("exit_node is missing")
    else:
        if not exit_node.get("enabled"):
            blocking.append(
                "exit_node is disabled — enable it before"
                " generating an apply plan."
            )
        if not (exit_node.get("wireguard_interface_name") or "").strip():
            blocking.append(
                "exit_node has no wireguard_interface_name —"
                " cannot route to the VPS tunnel."
            )

    # ── Targets ──
    active_targets: list[dict] = []
    seen_normalized: set[str] = set()
    for t in (targets or ()):
        nv = (t.get("normalized_value") or t.get("value") or "").strip().lower()
        if not nv:
            skipped.append(SkippedTarget(
                value=str(t.get("value") or ""),
                reason="empty normalized_value",
            ))
            continue
        if nv in _HARD_REJECT_DESTS:
            blocking.append(
                f"target {nv!r} is the catch-all 0.0.0.0/0 —"
                " refused (defense in depth; the validator"
                " should have already blocked this)."
            )
            continue
        if (t.get("status") or "active") != _TARGET_STATUS_ACTIVE:
            skipped.append(SkippedTarget(
                value=nv,
                reason=f"status={t.get('status')!r}",
            ))
            continue
        if nv in seen_normalized:
            skipped.append(SkippedTarget(
                value=nv, reason="duplicate normalized_value"))
            continue
        seen_normalized.add(nv)
        active_targets.append(t)

    if not active_targets and not blocking:
        blocking.append(
            "no active targets — add at least one before"
            " generating a script."
        )

    if blocking:
        return ScriptPlan(
            policy_id=pid,
            address_list=al_name,
            routing_table=rt_name,
            routing_mark=rt_name,
            comment_prefix=cprefix,
            blocking_errors=tuple(blocking),
            warnings=tuple(warnings),
            targets_skipped=tuple(skipped),
        )

    # ── Cleanup ── (runs first in forward script for idempotency)
    cleanup_ops = tuple(_cleanup_ops(cprefix))

    # ── Routing table ──
    routing_table_ops = (
        PlanCommand(
            section="routing-table",
            path="/routing/table",
            kind="add",
            attrs={
                "name": rt_name,
                "fib": "",  # `/routing table add ... fib` flag
                "comment": f"{cprefix}routing-table",
            },
            note="dedicated routing table for selected sites",
        ),
    )

    # ── Route into the custom table (NEVER in main) ──
    wg_iface = exit_node["wireguard_interface_name"].strip()
    wg_gw    = (exit_node.get("wireguard_gateway_ip") or "").strip()
    # Prefer gateway IP when known; fall back to interface name
    # (RouterOS resolves it). Interface name is the safer default
    # when WireGuard runs PtP without a fixed peer IP.
    gateway_value = wg_gw if wg_gw else wg_iface
    route_attrs = {
        "dst-address": "0.0.0.0/0",
        "gateway": gateway_value,
        "routing-table": rt_name,
        "comment": f"{cprefix}vps-route",
    }
    # Safety invariant: the routing-table MUST be set, otherwise
    # this would hijack the main table. Refuse if somehow unset.
    assert route_attrs["routing-table"] == rt_name
    assert route_attrs["routing-table"] != "main"
    route_ops = (
        PlanCommand(
            section="route",
            path="/ip/route",
            kind="add",
            attrs=route_attrs,
            note="default route inside the policy table only —"
                 " never installed in the main routing table",
        ),
    )

    # ── Address list ──
    address_list_ops: list[PlanCommand] = []
    for t in active_targets:
        tid = int(t.get("id") or 0)
        nv  = t["normalized_value"]
        grp = (t.get("group_name") or "manual_review")
        ttype = t.get("target_type") or "domain"
        address_list_ops.append(PlanCommand(
            section="address-list",
            path="/ip/firewall/address-list",
            kind="add",
            attrs={
                "list": al_name,
                "address": nv,
                "comment": f"{cprefix}target:{tid}:{grp}",
            },
            note=f"target #{tid} type={ttype} group={grp}",
        ))
        # Optionally also add the bare `www.<domain>` entry for
        # root domains. We *do not* generate fake wildcards.
        if (ttype == "domain"
            and bool(t.get("include_www") or 0)
            and _is_root_domain(nv)
        ):
            address_list_ops.append(PlanCommand(
                section="address-list",
                path="/ip/firewall/address-list",
                kind="add",
                attrs={
                    "list": al_name,
                    "address": f"www.{nv}",
                    "comment":
                        f"{cprefix}target:{tid}:{grp}:www",
                },
                note=f"target #{tid} www-prefix companion",
            ))

    # ── DNS helper (optional) ──
    dns_ops: tuple[PlanCommand, ...] = ()
    if include_subs and enable_dns_helper:
        dns_ops = tuple(_dns_helper_ops(
            active_targets, al_name, cprefix))
    elif include_subs and not enable_dns_helper:
        warnings.append(
            "include_subdomains=True but DNS helper mode is OFF —"
            " subdomain coverage is NOT guaranteed unless clients"
            " resolve through the router's own DNS."
        )

    # ── Mangle ──
    mangle_ops: list[PlanCommand] = [
        PlanCommand(
            section="mangle",
            path="/ip/firewall/mangle",
            kind="add",
            attrs={
                "chain": "prerouting",
                "dst-address-list": al_name,
                "action": "mark-routing",
                "new-routing-mark": rt_name,
                "passthrough": "no",
                "comment": f"{cprefix}mangle-prerouting",
            },
            note="mark client traffic destined to the address"
                 " list so it picks the custom routing table",
        ),
    ]
    if include_outp:
        mangle_ops.append(PlanCommand(
            section="mangle",
            path="/ip/firewall/mangle",
            kind="add",
            attrs={
                "chain": "output",
                "dst-address-list": al_name,
                "action": "mark-routing",
                "new-routing-mark": rt_name,
                "passthrough": "no",
                "comment": f"{cprefix}mangle-output",
            },
            note="also route router-originated test traffic"
                 " through the VPS",
        ))

    # ── Fail-mode protection ──
    firewall_filter_ops: list[PlanCommand] = []
    if fail_mode == "block_when_vps_down":
        if wan_interface_list:
            firewall_filter_ops.append(PlanCommand(
                section="firewall-filter",
                path="/ip/firewall/filter",
                kind="add",
                attrs={
                    "chain": "forward",
                    "action": "drop",
                    "dst-address-list": al_name,
                    "out-interface-list": wan_interface_list,
                    "comment": f"{cprefix}failsafe-block",
                },
                note=(
                    "drop selected-site traffic that escapes"
                    " via WAN — protects the original public"
                    " IP when the VPS/WireGuard route is down"
                ),
            ))
        else:
            warnings.append(
                "fail_mode=block_when_vps_down requested but no"
                " wan_interface_list provided — failsafe drop"
                " rule was NOT generated. Configure the WAN"
                " interface-list and re-plan, or accept that"
                " the original public IP may leak."
            )
    elif fail_mode == "fallback_to_wan":
        warnings.append(
            "fail_mode=fallback_to_wan: the ORIGINAL public IP"
            " WILL be used if the VPS/WireGuard route is"
            " unavailable. Selected sites are NOT protected"
            " from a tunnel outage."
        )
    else:
        warnings.append(
            f"unknown fail_mode {fail_mode!r} — defaulting to"
            " safe behaviour (no failsafe rule generated)."
        )

    # ── Rollback ── (same find-pattern shape as cleanup)
    rollback_ops = tuple(_rollback_ops(cprefix))

    # ── FastTrack advisory ──
    # MikroTik's FastTrack short-circuits connection-tracked
    # traffic past the mangle table. If a connection is
    # fast-tracked BEFORE this policy's mangle rule sees it,
    # the routing mark is never applied → the connection exits
    # via the main WAN, not the VPS tunnel. We do NOT touch
    # FastTrack automatically (it's a tenant-wide performance
    # knob). We just surface it loudly so operators know to
    # exclude VX2-managed destinations from FastTrack in their
    # /ip/firewall/filter table.
    warnings.append(FASTTRACK_WARNING_AR)

    return ScriptPlan(
        policy_id=pid,
        address_list=al_name,
        routing_table=rt_name,
        routing_mark=rt_name,
        comment_prefix=cprefix,
        cleanup_ops=cleanup_ops,
        routing_table_ops=routing_table_ops,
        address_list_ops=tuple(address_list_ops),
        dns_ops=dns_ops,
        route_ops=route_ops,
        mangle_ops=tuple(mangle_ops),
        firewall_filter_ops=tuple(firewall_filter_ops),
        rollback_ops=rollback_ops,
        warnings=tuple(warnings),
        targets_skipped=tuple(skipped),
    )


# ─── Helpers ─────────────────────────────────────────────────


def _blocking(*, policy_id: int, reason: str) -> ScriptPlan:
    return ScriptPlan(
        policy_id=policy_id,
        address_list=address_list_name(policy_id),
        routing_table=routing_table_name(policy_id),
        routing_mark=routing_table_name(policy_id),
        comment_prefix=comment_prefix(policy_id),
        blocking_errors=(reason,),
    )


def _cleanup_ops(cprefix: str) -> list[PlanCommand]:
    """Forward-script cleanup: remove every prior managed rule
    for this policy ID.

    The pattern is an **anchored, exact-prefix regex** —
    ``^HOBE_VX2_SITE_EXIT:<id>:`` — NOT a free substring. This
    matters for two distinct correctness reasons:

      1. Anchoring with ``^`` means a comment whose body just
         *contains* the tag (e.g. a hand-written
         "see HOBE_VX2_SITE_EXIT:1: notes" reminder on an
         unmanaged rule) is NOT matched.
      2. The trailing colon distinguishes policy ids — without
         it, cleaning up policy 1 would also delete policy 10's
         managed rules.
    """
    # `^PREFIX:` — RouterOS evaluates `comment~"..."` as POSIX
    # regex against the comment column, so `^` is honoured.
    pattern = f"^{cprefix}"
    # Order matters — remove rules that depend on others first.
    paths = [
        ("firewall-filter", "/ip/firewall/filter"),
        ("mangle",          "/ip/firewall/mangle"),
        ("dns",             "/ip/dns/static"),
        ("address-list",    "/ip/firewall/address-list"),
        ("route",           "/ip/route"),
        ("routing-table",   "/routing/table"),
    ]
    return [
        PlanCommand(
            section="cleanup",
            path=p,
            kind="remove",
            find_pattern=pattern,
            note=f"cleanup managed entries in {p}",
        )
        for _, p in paths
    ]


def _rollback_ops(cprefix: str) -> list[PlanCommand]:
    """Rollback is the same as cleanup — it removes only rows
    whose comment starts with our prefix. Same safety
    contract: never touches unmanaged rules."""
    return _cleanup_ops(cprefix)


def _dns_helper_ops(
    targets: list[dict], al_name: str, cprefix: str,
) -> list[PlanCommand]:
    """Build /ip/dns/static helper entries for domain targets
    when include_subdomains is requested AND the operator has
    explicitly enabled DNS helper mode.

    Each entry adds the resolved hostname to the destination
    address list as RouterOS performs the lookup, so clients
    that resolve through the router's own DNS pick up
    subdomains automatically."""
    out: list[PlanCommand] = []
    for t in targets:
        if (t.get("target_type") or "") != "domain":
            continue
        if not bool(t.get("include_subdomains") or 0):
            continue
        nv = t["normalized_value"]
        tid = int(t.get("id") or 0)
        # Build the RouterOS regexp as a clean Python string
        # with SINGLE backslashes (literal `\.` for each dot).
        # The renderer's escape pass doubles them at emit time
        # so the on-disk script ends up with `\\.`, which
        # RouterOS parses back to `\.`. Keeping that "two-stage"
        # discipline means tests can compare the Python string
        # against the obvious form.
        escaped = nv.replace(".", "\\.")
        regexp = "^.*\\." + escaped + "$"
        out.append(PlanCommand(
            section="dns",
            path="/ip/dns/static",
            kind="add",
            attrs={
                "regexp": regexp,
                "address-list": al_name,
                "address-list-timeout": "1h",
                "match-subdomain": "yes",
                "comment": f"{cprefix}dns:{tid}",
            },
            note=f"resolve subdomains of {nv} into address list",
        ))
    return out


def _is_root_domain(host: str) -> bool:
    """A root domain has exactly 2 labels (example.com).
    Subdomains (sub.example.com) get no www. companion."""
    return host.count(".") == 1


__all__ = [
    "COMMENT_TAG", "FASTTRACK_WARNING_AR",
    "routing_table_name", "address_list_name", "comment_prefix",
    "PlanCommand", "SkippedTarget", "ScriptPlan",
    "build_plan",
]
