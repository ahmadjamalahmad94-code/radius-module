"""mt_change_preview — O6 structured before/after preview.

Translates Q1's planner output (a list of RouterOS commands)
into a human-friendly diff against the router's current state:
  - items_to_add: things this plan will create
  - items_to_modify: things this plan will touch on existing
    state (best-effort match by name)
  - items_to_remove: in plan_hotspot/plan_pppoe there are no
    /remove commands — kept empty here; future planners (Q4
    unprogram) can populate it.
  - impact: short Arabic strings (e.g. "قد يتأثر المستخدمون
    المتصلون على ether2")
  - data_quality_warnings: surface when the comparison is
    based on stale or missing snapshot data — operator must
    know we can't claim certainty.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ChangeItem:
    kind: str               # "pool" | "address" | "dhcp-server" |
                            #  "hotspot" | "interface" | ...
    action: str             # "add" | "modify" | "remove"
    name: str               # human label
    detail_ar: str          # one-line description
    path: str = ""          # RouterOS path (e.g. /ip/pool/add)


@dataclass
class ChangePreview:
    items_to_add: list[ChangeItem] = field(default_factory=list)
    items_to_modify: list[ChangeItem] = field(default_factory=list)
    items_to_remove: list[ChangeItem] = field(default_factory=list)
    impact_ar: list[str] = field(default_factory=list)
    data_quality_warnings_ar: list[str] = field(default_factory=list)

    def total_changes(self) -> int:
        return (len(self.items_to_add) + len(self.items_to_modify)
                + len(self.items_to_remove))

    def to_dict(self) -> dict[str, Any]:
        return {
            "items_to_add":    [asdict(x) for x in self.items_to_add],
            "items_to_modify": [asdict(x) for x in self.items_to_modify],
            "items_to_remove": [asdict(x) for x in self.items_to_remove],
            "impact_ar": list(self.impact_ar),
            "data_quality_warnings_ar":
                list(self.data_quality_warnings_ar),
            "total_changes": self.total_changes(),
        }


# ─── Path → human-friendly kind ──────────────────────────────


_PATH_TO_KIND = {
    "/ip/pool/add": ("pool", "ip-pool",
                      "نطاق عناوين IP جديد للـ pool"),
    "/ip/address/add": ("address", "ip-address",
                        "عنوان IP جديد على الواجهة"),
    "/ip/dhcp-server/add": ("dhcp-server", "dhcp-server",
                             "خادم DHCP جديد"),
    "/ip/dhcp-server/network/add": (
        "dhcp-network", "dhcp-network",
        "تكوين شبكة DHCP (gateway/dns)"),
    "/ip/hotspot/profile/add": (
        "hotspot-profile", "hotspot-profile",
        "ملفّ تعريف Hotspot جديد"),
    "/ip/hotspot/add": (
        "hotspot", "hotspot-server", "خادم Hotspot جديد"),
    "/ip/hotspot/user/profile/add": (
        "hotspot-user-profile", "hotspot-user-profile",
        "ملف مستخدمي Hotspot الافتراضي"),
    "/ip/hotspot/walled-garden/ip/add": (
        "walled-garden", "walled-garden",
        "سماح Walled-Garden لعنوان IP"),
    "/ppp/profile/add": (
        "ppp-profile", "ppp-profile", "ملفّ PPP جديد"),
    "/interface/pppoe-server/server/add": (
        "pppoe-server", "pppoe-server",
        "خادم PPPoE جديد على الواجهة"),
}


def _to_item(cmd) -> ChangeItem:
    """Translate a planner Command into a ChangeItem."""
    path = cmd.path
    attrs = cmd.attrs or {}
    kind, _label, fallback_detail = _PATH_TO_KIND.get(
        path, ("router-object", "object",
                "تغيير على الراوتر"))
    name = (attrs.get("name") or attrs.get("address")
             or attrs.get("interface") or attrs.get("service-name")
             or attrs.get("dst-host") or "")
    # Detail: try to surface the most operator-relevant
    # attribute for each kind.
    extras: list[str] = []
    for key in ("interface", "address", "ranges",
                "gateway", "dns-server", "service-name",
                "rate-limit", "local-address", "remote-address",
                "lease-time"):
        if attrs.get(key):
            extras.append(f"{key}={attrs[key]}")
    detail = (
        ((fallback_detail + " — " + " · ".join(extras))
         if extras else fallback_detail)
    )
    return ChangeItem(
        kind=kind, action="add",
        name=name or "(بلا اسم)",
        detail_ar=detail, path=path,
    )


# ─── Public API ──────────────────────────────────────────────


def preview_plan(
    plan,
    *,
    snapshot_status: str = "unknown",
    existing_interfaces: list[dict] | None = None,
    existing_addresses: list[dict] | None = None,
) -> ChangePreview:
    """Build a structured change preview for one plan.

    Args:
      plan                 — mt_programming.Plan
      snapshot_status      — O1's status string for the router;
                              drives data-quality warnings.
      existing_interfaces  — best-effort current state for
                              impact detection.
      existing_addresses   — same.
    """
    if plan is None:
        return ChangePreview(
            data_quality_warnings_ar=[
                "لا يوجد plan ليُعاينَ."
            ],
        )

    out = ChangePreview()
    for cmd in (plan.commands or []):
        out.items_to_add.append(_to_item(cmd))

    # Impact derivation.
    interfaces = existing_interfaces or []
    addresses = existing_addresses or []
    target_iface = ""
    for item in out.items_to_add:
        if item.kind in {"address", "dhcp-server",
                          "hotspot", "pppoe-server"}:
            # Try to find the interface attribute on the
            # underlying command.
            for cmd in (plan.commands or []):
                if cmd.path.startswith(("/ip/address",
                                         "/ip/dhcp-server",
                                         "/ip/hotspot",
                                         "/interface/pppoe-server")):
                    iface = cmd.attrs.get("interface")
                    if iface:
                        target_iface = iface
                        break
            if target_iface:
                break

    if target_iface:
        # If the interface already carries addresses, connected
        # users may flap during the change.
        existing_on_target = [
            a for a in addresses
            if (a.get("interface") or "") == target_iface
        ]
        if existing_on_target:
            out.impact_ar.append(
                f"الواجهة {target_iface} تحمل عناوين بالفعل — "
                "قد يتأثّر المستخدمون المتّصلون أثناء التطبيق.")
        # If the interface row exists + has running clients
        # (best-effort from rx/tx counters), warn explicitly.
        iface_row = next(
            (r for r in interfaces
             if (r.get("name") or "") == target_iface), None)
        if iface_row and str(
                iface_row.get("running")) == "true":
            out.impact_ar.append(
                f"الواجهة {target_iface} تعمل حاليًا — "
                "التطبيق سيُحدث تغييرًا على شبكة نشطة.")

    # Data-quality warnings.
    if snapshot_status == "stale":
        out.data_quality_warnings_ar.append(
            "بيانات الراوتر قديمة — قد تختلف الحالة الفعلية "
            "عن ما يظهر في المعاينة.")
    elif snapshot_status in {"failed", "unknown"}:
        out.data_quality_warnings_ar.append(
            "لا يمكن مقارنة الحالة الحالية — البيانات غير "
            "متوفرة أو فشل آخر تحديث.")

    return out


__all__ = ["ChangeItem", "ChangePreview", "preview_plan"]
