"""Subscriber form fields the owner retired (decision page 2026-10-06).

They were saved but nothing ever read them (no RADIUS attribute, no job, no
policy). The owner chose «حذف» for each, so:

* every input is gone from the web form and the app;
* the save paths stop writing them — the web form keeps the stored column
  (``_WEB_FORM_UNMANAGED``), the API drops the keys silently (old app builds
  still send them: no 422) and pins the retired metadata keys to their stored
  values;
* the DB columns and the stored values stay as they are (nothing is dropped
  or wiped) — reading an old value is harmless.

PPPoE login: a PPPoE subscriber authenticates with its ONE login (``username``
+ ``password``) — FreeRADIUS → ``/api/v1/internal/auth`` → policy_engine looks
the account up by User-Name. The separate «اسم/كلمة مرور البرودباند» columns
were never read by anything, so they are retired as duplicates of the login.
The PPPoE address (``pppoe_ip``) was merged into «IP ثابت» (``static_ip``,
owner follow-up 2026-10-06; migration 198 copied the stored values): no form
writes it any more and the API maps the old key onto ``static_ip``. See
``framed_ip``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

# Subscriber columns no form edits any more.
RETIRED_COLUMNS: tuple[str, ...] = (
    "pool",                    # «مجموعة العناوين (Pool)»
    "vlan_id",                 # «VLAN»
    "device_connection_file",  # «ملف اتصال الجهاز»
    "pppoe_username",          # duplicate of the login (username)
    "pppoe_password",          # duplicate of the login (password)
)

# metadata group → retired keys.
RETIRED_META: dict[str, tuple[str, ...]] = {
    # app «إعدادات الراوتر» + web «مجموعة إدارة الراوتر (WinBox)»
    "mikrotik": ("profile", "rate_limit", "ip_pool", "comment",
                 "mikrotik_winbox_group"),
    # app session/idle timeout + called-station; web NAS IP / port / service
    "radius": ("session_timeout", "idle_timeout", "called_station_id",
               "nas_ip_address", "nas_port_id", "service_name"),
    "advanced": ("disable_on_first_use",),
    "notifications": ("on_login", "email", "mobile"),
    "subscription": ("type", "days"),
}


def _load(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def pin_retired_metadata(incoming: str, stored: Optional[str]) -> str:
    """``incoming`` metadata JSON with every retired key reset to its STORED
    value (or removed when nothing is stored). An old app build that still
    sends «مهلة الجلسة» or «تنبيه عند الدخول» therefore changes nothing, and
    a value saved before the removal is never wiped."""
    data = _load(incoming)
    if not isinstance(data, dict):
        return incoming
    old = _load(stored)
    if not isinstance(old, dict):
        old = {}
    changed = False
    for grp, keys in RETIRED_META.items():
        new_grp = data.get(grp)
        old_grp = old.get(grp) if isinstance(old.get(grp), dict) else {}
        if new_grp is None and any(k in old_grp for k in keys):
            new_grp = data[grp] = {}
        if not isinstance(new_grp, dict):
            continue
        for k in keys:
            if k in old_grp:
                if new_grp.get(k, object()) != old_grp[k]:
                    new_grp[k] = old_grp[k]
                    changed = True
            elif k in new_grp:
                del new_grp[k]
                changed = True
    if not changed:
        return incoming
    return json.dumps(data, ensure_ascii=False)


def framed_ip(sub) -> str:
    """The subscriber's fixed address for the Access-Accept
    (``Framed-IP-Address``): the ONE field «IP ثابت» (``static_ip``).

    ``Framed-IP-Address`` is an IPv4 attribute: a legacy IPv6 / malformed value
    stored before the IPv4-only rule is kept in the DB (the form shows a hint to
    fix it) but is never sent. The old ``pppoe_ip`` column is no longer read —
    migration 198 copied it into ``static_ip``."""
    import ipaddress
    v = str(getattr(sub, "static_ip", "") or "").strip()
    if not v:
        return ""
    try:
        addr = ipaddress.ip_address(v)
    except ValueError:
        return ""
    return v if isinstance(addr, ipaddress.IPv4Address) else ""
