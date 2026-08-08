"""DHCP-lease → device fingerprint sync.

Pulls `/ip/dhcp-server/lease/print` from every enabled MikroTik router
for every active tenant, parses the raw `host-name` + DHCP class-id
(option 60) into structured fields, and upserts into
`device_fingerprints` (migration 026).

Used in two modes:
  • background worker — every N seconds (default 120s, sees full table)
  • on-demand from card-checker — sync for a specific MAC list

The parsers are intentionally conservative. They prefer ``''`` over
guessing, and the raw values are always preserved alongside the parsed
ones so future parser improvements can run again without re-querying
the router.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from ..db.repos import device_fingerprints_repo, mikrotik_repo, nas_repo

_LOG = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Parsers
# ────────────────────────────────────────────────────────────────────

# DHCP option 60 (Vendor Class Identifier) common patterns.
# Examples actually seen in the wild:
#   android-dhcp-11
#   android-dhcp-12.1
#   MSFT 5.0
#   dhcpcd-9.4.1:Linux-6.1.0:armv8l:Cortex-A53
#   udhcp 1.36.1
_RE_ANDROID_CLASS = re.compile(r"^android-dhcp-(\d+(?:\.\d+)?)", re.IGNORECASE)
_RE_MSFT_CLASS    = re.compile(r"^MSFT\s+([0-9.]+)", re.IGNORECASE)
_RE_DHCPCD_LINUX  = re.compile(r"dhcpcd.*?Linux", re.IGNORECASE)
_RE_UDHCP         = re.compile(r"udhcp", re.IGNORECASE)


def parse_class_id(raw: str) -> tuple[str, str]:
    """(os_family, os_version) — both '' when unknown."""
    if not raw:
        return ("", "")
    s = raw.strip()

    m = _RE_ANDROID_CLASS.match(s)
    if m:
        return ("android", m.group(1))

    m = _RE_MSFT_CLASS.match(s)
    if m:
        # MSFT 5.0 → Windows family, version is the DHCP-claimed schema
        return ("windows", m.group(1))

    if _RE_DHCPCD_LINUX.search(s) or _RE_UDHCP.search(s):
        return ("linux", "")

    # Heuristic catch-alls
    low = s.lower()
    if "android" in low:
        return ("android", "")
    if "iphone" in low or "ipad" in low or "ios" in low:
        return ("ios", "")
    if "mac" in low and "os" in low:
        return ("macos", "")

    return ("", "")


# Hostname → brand/model heuristics. Hostnames are user-changeable so
# this is a hint, not authoritative.
_BRAND_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(redmi|poco|mi|xiaomi)", re.IGNORECASE),    "xiaomi"),
    (re.compile(r"^(galaxy|samsung|sm-)",   re.IGNORECASE),    "samsung"),
    (re.compile(r"^(iphone|ipad|ipod|macbook|imac)", re.IGNORECASE), "apple"),
    (re.compile(r"^(huawei|honor|nova|mate)", re.IGNORECASE),  "huawei"),
    (re.compile(r"^(oppo|realme|oneplus)", re.IGNORECASE),     "oppo"),
    (re.compile(r"^(vivo|iqoo)",   re.IGNORECASE),             "vivo"),
    (re.compile(r"^(nokia|lumia)", re.IGNORECASE),             "nokia"),
    (re.compile(r"^(lenovo|moto)", re.IGNORECASE),             "lenovo"),
    (re.compile(r"^(asus|rog|zenfone)", re.IGNORECASE),        "asus"),
    (re.compile(r"^(infinix|tecno|itel)", re.IGNORECASE),      "transsion"),
    (re.compile(r"^(desktop-|laptop-|win-|pc-)", re.IGNORECASE), "windows-pc"),
]


def parse_hostname(raw: str) -> tuple[str, str]:
    """(device_brand, device_model) — model is the hostname itself."""
    if not raw:
        return ("", "")
    s = raw.strip()
    model = s  # the hostname is our best 'model' label
    for pat, brand in _BRAND_HINTS:
        if pat.match(s):
            return (brand, model)
    return ("", model)


# ────────────────────────────────────────────────────────────────────
# MAC normalization — MikroTik returns AA:BB:CC:DD:EE:FF, but be
# defensive: some platforms emit '-' separators or no separators.
# ────────────────────────────────────────────────────────────────────

def _normalize_mac(mac: str) -> str:
    if not mac:
        return ""
    s = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(s) != 12:
        return ""
    pairs = [s[i:i+2] for i in range(0, 12, 2)]
    return ":".join(pairs).lower()


# ────────────────────────────────────────────────────────────────────
# MikroTik fetch
# ────────────────────────────────────────────────────────────────────

def fetch_leases_for_router(router_cfg: dict) -> list[dict]:
    """Returns parsed lease rows from one router. Empty on failure.

    Output rows have keys:
      mac, hostname, dhcp_class_id, os_family, os_version,
      device_brand, device_model, ip_address
    """
    from ..integration.mikrotik.errors import MikrotikError
    from ..integration.mikrotik.pool import acquire as acquire_mt

    out: list[dict] = []
    seen_keys = set()  # diagnostic — record what MT actually returned
    try:
        with acquire_mt(router_cfg) as client:
            # RouterOS field names (RouterOS 6.x → 7.x):
            #   mac-address, active-mac-address
            #   host-name, active-host-name
            #   client-id, active-client-id  (this is DHCP option 60 class)
            #   address, active-address
            # The `active-*` variants are only set when the lease is
            # currently bound to a client — exactly what we want.
            for raw in client.print_("/ip/dhcp-server/lease/print"):
                if not seen_keys:
                    seen_keys.update(raw.keys())  # record one sample
                mac = _normalize_mac(
                    raw.get("active-mac-address") or raw.get("mac-address") or ""
                )
                if not mac:
                    continue
                hostname = (
                    raw.get("active-host-name")
                    or raw.get("host-name")
                    or raw.get("comment")  # some setups put hostname here
                    or ""
                ).strip()
                class_id = (
                    raw.get("active-client-id")
                    or raw.get("client-id")
                    or ""
                ).strip()
                ip_address = (raw.get("active-address") or raw.get("address") or "").strip()

                os_family, os_version = parse_class_id(class_id)
                brand, model = parse_hostname(hostname)

                out.append({
                    "mac": mac,
                    "hostname": hostname,
                    "dhcp_class_id": class_id,
                    "os_family": os_family,
                    "os_version": os_version,
                    "device_brand": brand,
                    "device_model": model,
                    "ip_address": ip_address,
                })
    except MikrotikError as e:
        _LOG.warning("dhcp-lease sync: router=%s failed: %s",
                     router_cfg.get("host"), e)
        return []
    except Exception:  # noqa: BLE001
        _LOG.exception("dhcp-lease sync: unexpected error router=%s",
                       router_cfg.get("host"))
        return []
    # First-fetch diagnostic — log the field names MT actually returns
    # so we can audit field-name drift across RouterOS versions.
    if seen_keys:
        _LOG.debug("dhcp-lease sync: router=%s sample lease keys: %s",
                   router_cfg.get("host"), sorted(seen_keys))
    return out


# ────────────────────────────────────────────────────────────────────
# Top-level sync entry points
# ────────────────────────────────────────────────────────────────────

def _collect_router_configs(tenant_id: int) -> list[dict]:
    """Build a unified router-config list from BOTH tables.

    Some deployments populate only `nas_devices` (the RADIUS NAS table,
    which also carries api_port/api_user/api_password since RM-H5), while
    others use the dedicated `mikrotik_configs` table managed via the
    /admin/radius/mt screen. We pull from both, de-dup by host, and
    return a list of dicts matching the shape the MT connection pool
    expects (id, host, port, username, password, use_tls, verify_tls,
    timeout_sec).

    Without this, a tenant who configured only NAS rows would have an
    empty DHCP-lease cache — the worker would silently see "no routers"
    every cycle.
    """
    out: dict[str, dict] = {}

    # Source A — mikrotik_configs (preferred when present, since the
    # router list there is API-purpose).
    try:
        for r in mikrotik_repo.list_configs(int(tenant_id)):
            if not r.get("enabled"):
                continue
            host = (r.get("host") or "").strip()
            if not host:
                continue
            out[host] = {
                "id":          r["id"],
                "host":        host,
                "port":        int(r.get("port") or 8728),
                "username":    r.get("username") or "admin",
                "password":    r.get("password") or "",
                "use_tls":     bool(r.get("use_tls")),
                "verify_tls":  bool(r.get("verify_tls")),
                "timeout_sec": int(r.get("timeout_sec") or 20),
                "_source":     "mikrotik_configs",
            }
    except Exception:  # noqa: BLE001
        _LOG.exception("dhcp-lease sync: listing mikrotik_configs failed tenant=%s",
                       tenant_id)

    # Source B — nas_devices (fallback / additional). Only include
    # devices that look like MikroTik (have an api_user set, vendor
    # might also be 'mikrotik').
    try:
        for nas in nas_repo.list_nas(int(tenant_id), limit=1000):
            if not getattr(nas, "enabled", False):
                continue
            host = (getattr(nas, "address", "") or "").strip()
            api_user = getattr(nas, "api_user", "") or ""
            api_pwd  = getattr(nas, "api_password", "") or ""
            if not host or not api_user:
                continue  # no MT API on this NAS row
            if host in out:
                continue  # already covered by mikrotik_configs
            out[host] = {
                "id":          nas.id,
                "host":        host,
                "port":        int(getattr(nas, "api_port", 8728) or 8728),
                "username":    api_user,
                "password":    api_pwd,
                "use_tls":     bool(getattr(nas, "api_use_tls", False)),
                "verify_tls":  True,  # nas_devices doesn't track verify flag
                "timeout_sec": 20,
                "_source":     "nas_devices",
            }
    except Exception:  # noqa: BLE001
        _LOG.exception("dhcp-lease sync: listing nas_devices failed tenant=%s",
                       tenant_id)

    return list(out.values())


def sync_tenant(tenant_id: Any) -> int:
    """Pulls leases from every enabled router for a tenant and upserts.

    Returns the number of distinct MACs ingested across all routers.
    Last write wins per (tenant, mac) — fine, because the upsert merges
    instead of overwriting empty values.
    """
    routers = _collect_router_configs(int(tenant_id))
    if not routers:
        _LOG.info("dhcp-lease sync: no MT routers configured for tenant=%s "
                  "(neither mikrotik_configs nor nas_devices with api_user)",
                  tenant_id)
        return 0

    # 🔴 اجمع أوّلًا ثمّ اكتب دفعةً واحدة — ولا تكتب وأنت تُحاور الراوتر.
    #
    #    كان لكلّ عقد إيجارٍ `upsert()` بمعاملته الخاصّة، أي ٥٦٥ معاملة كتابةٍ
    #    كلّ دورة، **متداخلةً مع نداءات الشبكة إلى الراوتر**. فتُمسك القفل
    #    وتُفلته بالتناوب طوال المسح، وتصطدم بكتابات المصادقة والمحاسبة:
    #    `database is locked` عشرات المرّات في الساعة على الإنتاج.
    #
    #    الآن: كلّ عمل الشبكة يقع أوّلًا (بلا أيّ قفل)، ثمّ كتابةٌ واحدة.
    seen: set[str] = set()
    batch: list[dict] = []
    leases_total = 0
    for cfg in routers:
        rows = fetch_leases_for_router(cfg)
        leases_total += len(rows)
        for row in rows:
            batch.append(row)
            seen.add(row["mac"])

    written = device_fingerprints_repo.upsert_many(tenant_id, batch) if batch else 0
    _LOG.info("dhcp-lease sync: tenant=%s routers=%d leases=%d unique_macs=%d written=%d",
              tenant_id, len(routers), leases_total, len(seen), written)
    return len(seen)


def sync_all_tenants() -> dict[str, int]:
    """For the background worker. {tenant_id: macs_seen}."""
    from ..db.connection import db
    out: dict[str, int] = {}
    rows = db().execute(
        "SELECT id FROM tenants WHERE status = 'active'"
    ).fetchall()
    for r in rows:
        tid = str(r["id"])
        try:
            out[tid] = sync_tenant(r["id"])
        except Exception:  # noqa: BLE001
            _LOG.exception("dhcp-lease sync failed for tenant=%s", tid)
            out[tid] = 0
    return out


def sync_macs_for_tenant(tenant_id: Any, macs: Iterable[str]) -> int:
    """On-demand: trigger a sync and report how many of the requested
    MACs were found. Used by Card Checker on page load to refresh the
    fingerprints of the card's MACs specifically.

    For now this re-fetches the whole table (cheap on small networks
    and de-duplicated by upsert). We can optimize to a `?mac-address=`
    query later if it becomes a bottleneck.
    """
    target = {_normalize_mac(m) for m in (macs or []) if _normalize_mac(m)}
    if not target:
        return 0
    sync_tenant(tenant_id)
    found = device_fingerprints_repo.get_many_by_macs(tenant_id, list(target))
    return len(found)
