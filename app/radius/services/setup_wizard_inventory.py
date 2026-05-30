"""Read-only router inventory snapshots and risk analysis for setup wizard."""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..db.connection import db, transaction
from .setup_wizard_common import SetupWizardValidationError


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


_SECRET_KEYS = ("secret", "password", "passphrase", "private-key", "private_key", "token")


def sanitize_inventory(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if any(s in str(k).lower() for s in _SECRET_KEYS) else sanitize_inventory(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize_inventory(v) for v in value]
    if isinstance(value, str):
        text = value
        text = re.sub(r'(secret=)"[^"]*"', r'\1"***"', text, flags=re.I)
        text = re.sub(r'(password=)"[^"]*"', r'\1"***"', text, flags=re.I)
        text = re.sub(r'(private-key=)"[^"]*"', r'\1"***"', text, flags=re.I)
        return text
    return value


@dataclass(frozen=True)
class RouterSnapshot:
    id: int
    wizard_run_id: int
    source: str
    identity: dict[str, Any]
    interfaces: list[dict[str, Any]]
    addresses: list[dict[str, Any]]
    routes: list[dict[str, Any]]
    pools: list[dict[str, Any]]
    nat: list[dict[str, Any]]
    radius: list[dict[str, Any]]
    hotspot: list[dict[str, Any]]
    ppp: list[dict[str, Any]]
    wireguard: list[dict[str, Any]]
    risk_report: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "wizard_run_id": self.wizard_run_id,
            "source": self.source,
            "identity": self.identity,
            "interfaces": self.interfaces,
            "addresses": self.addresses,
            "routes": self.routes,
            "pools": self.pools,
            "nat": self.nat,
            "radius": self.radius,
            "hotspot": self.hotspot,
            "ppp": self.ppp,
            "wireguard": self.wireguard,
            "risk_report": self.risk_report,
            "created_at": self.created_at,
        }


def _row_to_snapshot(row: Any) -> RouterSnapshot:
    data = dict(row)
    return RouterSnapshot(
        id=int(data["id"]),
        wizard_run_id=int(data["wizard_run_id"]),
        source=str(data.get("source") or ""),
        identity=_json_loads(data.get("identity_json"), {}),
        interfaces=_json_loads(data.get("interfaces_json"), []),
        addresses=_json_loads(data.get("addresses_json"), []),
        routes=_json_loads(data.get("routes_json"), []),
        pools=_json_loads(data.get("pools_json"), []),
        nat=_json_loads(data.get("nat_json"), []),
        radius=_json_loads(data.get("radius_json"), []),
        hotspot=_json_loads(data.get("hotspot_json"), []),
        ppp=_json_loads(data.get("ppp_json"), []),
        wireguard=_json_loads(data.get("wireguard_json"), []),
        risk_report=_json_loads(data.get("risk_report_json"), {}),
        created_at=str(data.get("created_at") or ""),
    )


class RouterInventoryParser:
    """Small tolerant parser for pasted RouterOS print output.

    It does not try to fully parse RouterOS. It extracts enough structure for
    safety decisions and keeps unknown details as sanitized raw records.
    """

    def parse(self, output: str) -> dict[str, Any]:
        sections = {
            "identity": {},
            "interfaces": [],
            "addresses": [],
            "routes": [],
            "pools": [],
            "nat": [],
            "radius": [],
            "hotspot": [],
            "ppp": [],
            "wireguard": [],
            "raw_summary": {},
        }
        current = "raw"
        raw_lines: dict[str, list[str]] = {}
        for raw in str(output or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            low = line.lower()
            if "interface wireguard" in low:
                current = "wireguard"
            elif low.startswith("/interface") or "interface print" in low:
                current = "interfaces"
            elif "ip address" in low:
                current = "addresses"
            elif "ip route" in low:
                current = "routes"
            elif "ip pool" in low:
                current = "pools"
            elif "firewall nat" in low:
                current = "nat"
            elif low.startswith("/radius") or "radius print" in low:
                current = "radius"
            elif "ip hotspot" in low:
                current = "hotspot"
            elif "ppp" in low or "pppoe" in low:
                current = "ppp"
            raw_lines.setdefault(current, []).append(line)

            record = _parse_key_values(line)
            if current in sections and isinstance(sections[current], list):
                if record:
                    sections[current].append(record)
                else:
                    sections[current].append({"raw": sanitize_inventory(line)})
            elif current == "raw":
                if "routeros" in low:
                    sections["identity"]["routeros_version"] = line
        sections["raw_summary"] = {k: len(v) for k, v in raw_lines.items()}
        return sanitize_inventory(sections)


def _parse_key_values(line: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, raw_value in re.findall(r'([A-Za-z0-9._-]+)=("[^"]*"|\S+)', line):
        value = raw_value.strip('"') if raw_value.startswith('"') else raw_value
        record[key] = value
    if " name=" not in f" {line}" and re.match(r"^\d+\s+[\w.-]+", line):
        parts = line.split()
        if len(parts) >= 2:
            record.setdefault("name", parts[1])
    return sanitize_inventory(record)


class RouterRiskAnalyzer:
    def analyze(
        self,
        *,
        snapshot: dict[str, Any],
        selected_wan_interface: str = "",
        vpn_interface: str = "hr-wg",
        candidate_cidrs: list[str] | None = None,
    ) -> dict[str, Any]:
        interfaces = list(snapshot.get("interfaces") or [])
        addresses = list(snapshot.get("addresses") or [])
        routes = list(snapshot.get("routes") or [])
        pools = list(snapshot.get("pools") or [])
        nat = list(snapshot.get("nat") or [])
        hotspot = list(snapshot.get("hotspot") or [])
        ppp = list(snapshot.get("ppp") or [])
        wireguard = list(snapshot.get("wireguard") or [])

        wan = selected_wan_interface or _detect_wan_interface(routes, interfaces)
        vpn = vpn_interface if any(_rec_name(item) == vpn_interface for item in interfaces + wireguard) else vpn_interface
        subnets = _extract_subnets(addresses, pools, routes)
        warnings: list[dict[str, Any]] = []
        if wan:
            warnings.append({"code": "wan_interface_excluded", "interface": wan, "message_ar": "WAN interface is excluded from service setup"})
        if vpn:
            warnings.append({"code": "vpn_interface_excluded", "interface": vpn, "message_ar": "VPN interface is excluded from service setup"})
        if hotspot:
            warnings.append({"code": "existing_hotspot_detected", "message_ar": "Existing Hotspot configuration was detected"})
        if ppp:
            warnings.append({"code": "existing_pppoe_detected", "message_ar": "Existing PPP/PPPoE configuration was detected"})
        if any(str(item.get("dst-address") or item.get("dst_address") or "") in {"0.0.0.0/0", "0.0.0.0"} for item in routes):
            warnings.append({"code": "existing_default_route", "message_ar": "Existing default route detected"})
        overlaps = _candidate_overlaps(subnets, candidate_cidrs or [])
        for item in overlaps:
            warnings.append({
                "code": "subnet_overlap",
                "candidate": item["candidate"],
                "existing": item["existing"],
                "message_ar": "الشبكة المرشحة تتداخل مع شبكة موجودة على الراوتر",
            })
        return {
            "wan_interface": wan,
            "vpn_interface": vpn,
            "excluded_interfaces": [x for x in [wan, vpn] if x],
            "existing_subnets": subnets,
            "existing_pool_names": [_rec_name(item) for item in pools if _rec_name(item)],
            "existing_nat_count": len(nat),
            "existing_hotspot": bool(hotspot),
            "existing_pppoe": bool(ppp),
            "subnet_overlaps": overlaps,
            "warnings": warnings,
        }


def _rec_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("interface") or item.get("default-name") or "").strip()


def _detect_wan_interface(routes: list[dict[str, Any]], interfaces: list[dict[str, Any]]) -> str:
    for route in routes:
        dst = str(route.get("dst-address") or route.get("dst_address") or "").strip()
        if dst in {"0.0.0.0/0", "0.0.0.0"}:
            gateway = str(route.get("gateway") or "").strip()
            if gateway and not re.match(r"\d+\.\d+\.\d+\.\d+", gateway):
                return gateway
    for iface in interfaces:
        name = _rec_name(iface)
        if name.lower() in {"ether1", "wan"} or "wan" in name.lower():
            return name
    return ""


def _extract_subnets(*groups: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for group in groups:
        for item in group:
            for key in ("address", "dst-address", "dst_address", "ranges", "src-address", "src_address"):
                raw = str(item.get(key) or "").strip()
                for token in re.split(r"[, ]+", raw):
                    token = token.strip()
                    if not token:
                        continue
                    if "-" in token:
                        token = token.split("-", 1)[0]
                    try:
                        net = ipaddress.ip_network(token, strict=False)
                    except ValueError:
                        continue
                    if net.version == 4 and str(net) not in found:
                        found.append(str(net))
    return found


def _candidate_overlaps(existing_subnets: list[str], candidate_cidrs: list[str]) -> list[dict[str, str]]:
    overlaps: list[dict[str, str]] = []
    existing = []
    for raw in existing_subnets:
        try:
            existing.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    for raw_candidate in candidate_cidrs:
        try:
            candidate = ipaddress.ip_network(str(raw_candidate), strict=False)
        except ValueError:
            continue
        for existing_net in existing:
            if candidate.overlaps(existing_net):
                overlaps.append({"candidate": str(candidate), "existing": str(existing_net)})
    return overlaps


class RouterInventoryService:
    def __init__(
        self,
        *,
        parser: RouterInventoryParser | None = None,
        analyzer: RouterRiskAnalyzer | None = None,
    ) -> None:
        self.parser = parser or RouterInventoryParser()
        self.analyzer = analyzer or RouterRiskAnalyzer()

    def create_from_pasted_output(
        self,
        *,
        tenant_id: int,
        run_id: int,
        output: str,
        selected_wan_interface: str = "",
    ) -> dict[str, Any]:
        if not str(output or "").strip():
            raise SetupWizardValidationError("inventory output is required")
        parsed = self.parser.parse(output)
        risk = self.analyzer.analyze(
            snapshot=parsed,
            selected_wan_interface=selected_wan_interface,
        )
        return self.store_snapshot(
            tenant_id=tenant_id,
            run_id=run_id,
            source="pasted",
            snapshot={**parsed, "risk_report": risk},
        )

    def store_snapshot(
        self,
        *,
        tenant_id: int,
        run_id: int,
        source: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        clean = sanitize_inventory(snapshot)
        risk = clean.get("risk_report") or self.analyzer.analyze(snapshot=clean)
        now = _now()
        with transaction() as c:
            cur = c.execute(
                """
                INSERT INTO setup_wizard_router_snapshots (
                  wizard_run_id, tenant_id, source, identity_json, interfaces_json,
                  addresses_json, routes_json, pools_json, nat_json, radius_json,
                  hotspot_json, ppp_json, wireguard_json, risk_report_json,
                  raw_summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    int(tenant_id),
                    str(source or "pasted")[:40],
                    _json_dumps(clean.get("identity") or {}),
                    _json_dumps(clean.get("interfaces") or []),
                    _json_dumps(clean.get("addresses") or []),
                    _json_dumps(clean.get("routes") or []),
                    _json_dumps(clean.get("pools") or []),
                    _json_dumps(clean.get("nat") or []),
                    _json_dumps(clean.get("radius") or []),
                    _json_dumps(clean.get("hotspot") or []),
                    _json_dumps(clean.get("ppp") or []),
                    _json_dumps(clean.get("wireguard") or []),
                    _json_dumps(risk),
                    _json_dumps(clean.get("raw_summary") or {}),
                    now,
                ),
            )
            snapshot_id = int(cur.lastrowid)
        row = db().execute(
            "SELECT * FROM setup_wizard_router_snapshots WHERE id=?",
            (snapshot_id,),
        ).fetchone()
        return _row_to_snapshot(row).to_dict()

    def latest_snapshot(self, *, tenant_id: int, run_id: int) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM setup_wizard_router_snapshots
            WHERE tenant_id=? AND wizard_run_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (int(tenant_id), int(run_id)),
        ).fetchone()
        return _row_to_snapshot(row).to_dict() if row else None
