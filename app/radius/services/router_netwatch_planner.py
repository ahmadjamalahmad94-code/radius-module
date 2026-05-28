"""Router-side Netwatch planner (Sprint 6).

Installs `/tool netwatch` on the customer's MikroTik so the
router itself polls a device every minute and fires a webhook
back to HobeRadius on every state flip. Useful when the VPS-
side ping monitor (Sprint 2) can't reach the LAN — e.g., when
the customer router's hr-wg → LAN routing isn't configured.

Comment prefix HOBE_NETWATCH:<device_id>: per the plan, so the
remove step can sweep our rows without touching operator-
authored netwatch entries.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from . import mikrotik_admin_client as mac
from . import router_event_token

_LOG = logging.getLogger(__name__)


def _public_url() -> str:
    """Where the router should POST its events. Sourced from
    HOBERADIUS_PUBLIC_URL (e.g. https://radius.example.com).
    Empty string is detected by `install_netwatch` and surfaces
    as a clear error to the operator."""
    return (os.environ.get("HOBERADIUS_PUBLIC_URL") or "").strip().rstrip("/")


def _comment(device_id: int, role: str = "netwatch") -> str:
    return f"HOBE_NETWATCH:{int(device_id)}:{role}"


def install_netwatch(
    *,
    nas: Mapping[str, Any],
    tenant_id: int,
    device: Mapping[str, Any],
    interval_sec: int = 60,
    timeout_sec: int = 3,
) -> tuple[bool, str, dict | None]:
    """Add (or replace) the netwatch entry on the router.

    Returns (ok, error, info-dict). Info dict echoes the webhook
    URL we wired in so the UI can show it back to the operator
    for verification.
    """
    public_url = _public_url()
    if not public_url:
        return (
            False,
            "HOBERADIUS_PUBLIC_URL غير محدد — لا يمكن للراوتر "
            "إرسال الـ webhook. اضبط المتغير في إعدادات الخادم.",
            None,
        )
    ip = (device.get("ip_address") or "").strip()
    if not ip:
        return False, "الجهاز يحتاج IP محفوظ قبل التفعيل.", None

    device_id = int(device["id"])
    token = router_event_token.make_token(int(tenant_id), device_id)
    base = f"{public_url}/api/router-events/netwatch?device_id={device_id}&token={token}"
    up_script = (
        '/tool fetch url="' + base + '&state=up'
        '" mode=' + ("https" if public_url.startswith("https") else "http")
        + ' keep-result=no'
    )
    down_script = (
        '/tool fetch url="' + base + '&state=down'
        '" mode=' + ("https" if public_url.startswith("https") else "http")
        + ' keep-result=no'
    )

    def _work(client):
        # Idempotent: remove any prior HobeRadius netwatch on
        # the same device, then add fresh. Operator-authored
        # netwatch entries (other comments) are untouched.
        prefix = _comment(device_id, "")
        rows = list(client.print_("/tool/netwatch/print"))
        for r in rows:
            if (r.get("comment") or "").startswith(prefix):
                rid = r.get(".id")
                if rid:
                    try:
                        client.run("/tool/netwatch/remove",
                                   attrs={".id": rid})
                    except Exception:  # noqa: BLE001
                        _LOG.exception("netwatch remove failed id=%s", rid)

        client.run(
            "/tool/netwatch/add",
            attrs={
                "host":        ip,
                "interval":    _hms(interval_sec),
                "timeout":     _hms(timeout_sec),
                "up-script":   up_script,
                "down-script": down_script,
                "comment":     _comment(device_id, "netwatch"),
            },
        )
        return {"ok": True}

    result = mac._safe_dial(
        nas=nas, operation=f"netwatch:install:{device_id}", work=_work,
    )
    if not result.ok:
        return False, result.error or "تعذّر تنفيذ السكربت على الراوتر.", None
    return True, "", {
        "webhook_up":   base + "&state=up",
        "webhook_down": base + "&state=down",
    }


def remove_netwatch(
    *,
    nas: Mapping[str, Any],
    device_id: int,
) -> tuple[bool, str]:
    prefix = _comment(int(device_id), "")

    def _work(client):
        removed = 0
        try:
            rows = list(client.print_("/tool/netwatch/print"))
            for r in rows:
                if (r.get("comment") or "").startswith(prefix):
                    rid = r.get(".id")
                    if not rid:
                        continue
                    try:
                        client.run("/tool/netwatch/remove",
                                   attrs={".id": rid})
                        removed += 1
                    except Exception:  # noqa: BLE001
                        _LOG.exception("netwatch remove failed")
        except Exception:  # noqa: BLE001
            _LOG.exception("netwatch print failed")
        return {"removed": removed}

    result = mac._safe_dial(
        nas=nas, operation=f"netwatch:remove:{device_id}", work=_work,
    )
    if not result.ok:
        return False, result.error or "تعذّر الوصول للراوتر."
    return True, ""


def _hms(seconds: int) -> str:
    """RouterOS time format: 00:00:00."""
    s = max(1, int(seconds))
    h, rem = divmod(s, 3600)
    m, s2 = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s2:02d}"
