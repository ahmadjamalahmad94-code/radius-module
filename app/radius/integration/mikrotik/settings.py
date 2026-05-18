"""
MikrotikConfigStore — اتصالات الـ MTs المُدارة.

تخزين in-memory الآن، sqlite في P2. الواجهة لن تتغيّر.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from itertools import count
from threading import Lock
from typing import Optional


@dataclass(frozen=True)
class MikrotikConfig:
    id: Optional[int]
    name: str               # display name (مرتبط بـ NasDevice.name لاحقًا)
    host: str
    port: int = 8728
    username: str = "admin"
    password: str = ""
    use_tls: bool = False
    verify_tls: bool = True
    timeout_sec: int = 10
    enabled: bool = True


class MikrotikConfigStore:
    _inst: Optional["MikrotikConfigStore"] = None
    _inst_lock = Lock()

    def __init__(self) -> None:
        self._by_id: dict[int, MikrotikConfig] = {}
        self._seq = count(1)
        self._lock = Lock()
        self._load_from_env()

    @classmethod
    def instance(cls) -> "MikrotikConfigStore":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def _load_from_env(self) -> None:
        """يقرأ اتصالًا واحدًا من env (للبدء السريع)."""
        host = os.environ.get("MIKROTIK_HOST")
        if not host:
            return
        cfg = MikrotikConfig(
            id=next(self._seq),
            name=os.environ.get("MIKROTIK_NAME", "default"),
            host=host,
            port=int(os.environ.get("MIKROTIK_PORT", "0") or 0)
                  or (8729 if os.environ.get("MIKROTIK_TLS") in {"1","true","yes","on"} else 8728),
            username=os.environ.get("MIKROTIK_USER", "admin"),
            password=os.environ.get("MIKROTIK_PASSWORD", ""),
            use_tls=os.environ.get("MIKROTIK_TLS", "").lower() in {"1","true","yes","on"},
            verify_tls=os.environ.get("MIKROTIK_TLS_VERIFY", "1").lower() in {"1","true","yes","on"},
            timeout_sec=int(os.environ.get("MIKROTIK_TIMEOUT", "10")),
        )
        self._by_id[cfg.id] = cfg

    def list(self) -> list[MikrotikConfig]:
        with self._lock:
            return sorted(self._by_id.values(), key=lambda c: c.id or 0)

    def get(self, cfg_id: int) -> Optional[MikrotikConfig]:
        with self._lock:
            return self._by_id.get(cfg_id)

    def add(self, cfg: MikrotikConfig) -> MikrotikConfig:
        with self._lock:
            new_id = next(self._seq)
            saved = replace(cfg, id=new_id)
            self._by_id[new_id] = saved
            return saved

    def update(self, cfg_id: int, **changes) -> Optional[MikrotikConfig]:
        with self._lock:
            cur = self._by_id.get(cfg_id)
            if not cur:
                return None
            saved = replace(cur, **{k: v for k, v in changes.items() if hasattr(cur, k)})
            self._by_id[cfg_id] = saved
            return saved

    def delete(self, cfg_id: int) -> None:
        with self._lock:
            self._by_id.pop(cfg_id, None)

    def primary(self) -> Optional[MikrotikConfig]:
        """يُرجع أول اتصال مفعَّل."""
        for c in self.list():
            if c.enabled:
                return c
        return None
