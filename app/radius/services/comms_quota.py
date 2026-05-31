"""Message quota accounting for SMS and WhatsApp channels."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .comms_providers import HTTP_CHANNELS, _channel, _settings_key

MODE_SELF_API = "self_api"
MODE_ADMIN_QUOTA = "admin_quota"
QUOTA_EXHAUSTED_REASON = "نفدت كوتة الرسائل"

_LEDGER_MAX = 200


def _int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else 0


def _repo():
    from ..db.repos import tenants_repo

    return tenants_repo


def quota_balance(tenant_id: int, channel: str) -> int:
    ch = _channel(channel)
    raw = _repo().get_setting(int(tenant_id or 1), _settings_key(ch, "quota_balance"), "0")
    return _int(raw, 0)


def quota_used(tenant_id: int, channel: str) -> int:
    ch = _channel(channel)
    raw = _repo().get_setting(int(tenant_id or 1), _settings_key(ch, "quota_used"), "0")
    return _int(raw, 0)


def quota_ledger(tenant_id: int, channel: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    ch = _channel(channel)
    raw = _repo().get_setting(int(tenant_id or 1), _settings_key(ch, "quota_ledger"), "")
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        data = []
    entries = [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []
    if limit is not None and limit >= 0:
        return entries[-limit:]
    return entries


def quota_available(tenant_id: int, channel: str) -> bool:
    return quota_balance(tenant_id, channel) > 0


def consume_quota(tenant_id: int, channel: str, n: int = 1) -> int:
    ch = _channel(channel)
    tid = int(tenant_id or 1)
    spend = max(0, _int(n, 0))
    if spend == 0:
        return quota_balance(tid, ch)

    current = quota_balance(tid, ch)
    spent = min(current, spend)
    new_balance = current - spent
    used = quota_used(tid, ch) + spent

    repo = _repo()
    repo.set_setting(tid, _settings_key(ch, "quota_balance"), str(new_balance))
    repo.set_setting(tid, _settings_key(ch, "quota_used"), str(used))
    if spent > 0:
        _append_ledger(tid, ch, delta=-spent, by="system", note="إرسال رسالة", balance_after=new_balance)
    return new_balance


def credit_quota(tenant_id: int, channel: str, n: int, *, by: str = "", note: str = "") -> int:
    ch = _channel(channel)
    tid = int(tenant_id or 1)
    amount = max(0, _int(n, 0))
    current = quota_balance(tid, ch)
    if amount == 0:
        return current

    new_balance = current + amount
    _repo().set_setting(tid, _settings_key(ch, "quota_balance"), str(new_balance))
    _append_ledger(
        tid,
        ch,
        delta=amount,
        by=(str(by or "").strip() or "operator"),
        note=(str(note or "").strip() or "إضافة رصيد"),
        balance_after=new_balance,
    )
    return new_balance


def _append_ledger(tenant_id: int, channel: str, *, delta: int, by: str, note: str, balance_after: int) -> None:
    from ..db.helpers import now_iso

    ch = _channel(channel)
    tid = int(tenant_id or 1)
    entries = quota_ledger(tid, ch)
    entries.append(
        {
            "ts": now_iso(),
            "delta": int(delta),
            "by": str(by or "")[:120],
            "note": str(note or "")[:240],
            "balance_after": int(balance_after),
        }
    )
    if len(entries) > _LEDGER_MAX:
        entries = entries[-_LEDGER_MAX:]
    _repo().set_setting(tid, _settings_key(ch, "quota_ledger"), json.dumps(entries, ensure_ascii=False))


@dataclass(frozen=True)
class QuotaStatus:
    channel: str
    mode: str
    balance: int
    used: int
    is_quota_mode: bool


def quota_status(tenant_id: int, channel: str) -> QuotaStatus:
    from .comms_providers import load_channel_config

    ch = _channel(channel)
    cfg = load_channel_config(tenant_id, ch)
    mode = str(cfg.get("mode") or MODE_SELF_API)
    return QuotaStatus(
        channel=ch,
        mode=mode,
        balance=quota_balance(tenant_id, ch),
        used=quota_used(tenant_id, ch),
        is_quota_mode=(mode == MODE_ADMIN_QUOTA),
    )


def all_channel_status(tenant_id: int) -> dict[str, QuotaStatus]:
    return {ch: quota_status(tenant_id, ch) for ch in HTTP_CHANNELS}
