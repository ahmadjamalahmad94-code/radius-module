"""SW4 interface discovery contract (no live adapter in this slice)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InterfaceInfo:
    name: str
    kind: str = "ether"
    running: bool = True


class InterfaceDiscoveryContract(Protocol):
    def list_interfaces(self, *, tenant_id: int, run_id: int) -> list[InterfaceInfo]:
        """Return interface candidates for planning (no mutation)."""


class StaticInterfaceDiscovery:
    """Test-safe static interface source."""

    def __init__(self, interfaces: list[InterfaceInfo] | None = None) -> None:
        self._interfaces = interfaces or []

    def list_interfaces(self, *, tenant_id: int, run_id: int) -> list[InterfaceInfo]:
        _ = tenant_id, run_id
        return list(self._interfaces)
