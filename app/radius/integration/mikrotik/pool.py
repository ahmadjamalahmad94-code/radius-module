"""
MikroTik connection pool — connection واحد مُعاد الاستخدام لكل router.

تصميم بسيط مناسب لـ Phase-1 (single-process، 1-2 worker threads):
- Lock global لكل router_id يضمن عدم تشارك الـ connection بين خيطين.
- إعادة فتح تلقائية إن قُطعت أو فشلت آخر عملية.
- timeout قصير لاكتشاف الأعطال.

ليس thread-safe بمعنى parallel use؛ هو **serializing** — كل router يخدم
خيطًا واحدًا في كل لحظة، الباقون ينتظرون.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from .client import MikrotikClient
from .errors import ConnectError, MikrotikError
from ...services.nas_connection import resolve_connection_address

_LOG = logging.getLogger(__name__)


class _Entry:
    __slots__ = ("client", "lock", "cfg_version")

    def __init__(self):
        self.client: MikrotikClient | None = None
        self.lock = threading.Lock()
        self.cfg_version: int = 0  # تتبدّل إذا تغيّرت الإعدادات


_entries: dict[int, _Entry] = {}
_entries_lock = threading.Lock()


def _entry_for(router_id: int) -> _Entry:
    with _entries_lock:
        e = _entries.get(router_id)
        if e is None:
            e = _Entry()
            _entries[router_id] = e
        return e


def invalidate(router_id: int) -> None:
    """تُستدعى عند تعديل/حذف config — تُغلق الـ connection الحالي."""
    e = _entries.get(router_id)
    if e is None:
        return
    with e.lock:
        if e.client is not None:
            try: e.client.close()
            except Exception: pass
            e.client = None
        e.cfg_version += 1


@contextmanager
def acquire(router_cfg: dict) -> Iterator[MikrotikClient]:
    """
    سياق يُسلّم client متّصل بـ MT لإجراء عمليات.
    `router_cfg` صف من mikrotik_configs (dict).

    عند الخروج العادي: لا نُغلق (يبقى للاستخدام التالي).
    عند الاستثناء: نُغلق احتياطيًا (قد يكون الاتصال فاسدًا).
    """
    rid = int(router_cfg["id"])
    e = _entry_for(rid)
    with e.lock:
        # افتح إن لم يكن متّصلًا
        if e.client is None:
            try:
                e.client = MikrotikClient(
                    # VPN-only: the single dial chokepoint. Resolves to the
                    # WireGuard peer for VPN-mode rows; idempotent for callers
                    # that already resolved the host (resolver falls back to it).
                    # Routes the legacy router_sync MT-API disconnect through the
                    # resolver too, instead of a raw public host.
                    host=resolve_connection_address(router_cfg) or router_cfg["host"],
                    port=int(router_cfg["port"]),
                    username=router_cfg["username"], password=router_cfg["password"],
                    use_tls=bool(router_cfg["use_tls"]),
                    verify_tls=bool(router_cfg["verify_tls"]),
                    timeout=int(router_cfg["timeout_sec"] or 10),
                )
                e.client.connect()
            except MikrotikError:
                e.client = None
                raise
        try:
            yield e.client
        except (ConnectError, OSError):
            # connection broken — أغلق وأعد المحاولة في المرة القادمة
            _LOG.warning("mikrotik connection broken for router=%d — closing", rid)
            try: e.client.close()
            except Exception: pass
            e.client = None
            raise


def close_all() -> None:
    """يُستخدم عند الإغلاق التام للتطبيق."""
    with _entries_lock:
        for e in _entries.values():
            with e.lock:
                if e.client is not None:
                    try: e.client.close()
                    except Exception: pass
                    e.client = None
        _entries.clear()
