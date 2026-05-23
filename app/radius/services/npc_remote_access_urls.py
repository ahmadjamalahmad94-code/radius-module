"""npc_remote_access_urls — compute the access endpoints a
remote-access policy unlocks on a router.

Given a remote_access policy + the target nas_devices row, return
the human-readable URLs the operator needs to connect once the
policy is applied. This is what answers "I clicked apply — now
how do I connect?".

Pure function. No DB. No router contact. Called from the preview
view + the changes view so both surfaces can show the same
endpoint list.
"""
from __future__ import annotations

from typing import Mapping


# Default service ports on MikroTik (changeable on the router via
# /ip/service/set; we surface the standard defaults — operators
# who changed them know to substitute).
_DEFAULTS = {
    "winbox":       8291,
    "api":          8728,
    "api_ssl":      8729,
    "webfig_http":  80,
    "webfig_https": 443,
}


def compute_access_urls(
    policy: Mapping,
    nas: Mapping,
) -> list[dict]:
    """Return a list of access-endpoint dicts.

    Each entry has stable keys:
      * `service`      machine label, e.g. "winbox"
      * `service_ar`   short Arabic label, e.g. "وينبوكس"
      * `host`         hostname or IP (from nas_devices.address)
      * `port`         integer port
      * `url`          best-effort URL (e.g. "https://...", or
                       just "host:port" for non-HTTP services)
      * `clipboard`    short string ideal for "نسخ" buttons
      * `hint_ar`      brief operator note
    """
    host = str(nas.get("address") or "").strip()
    if not host:
        return []
    ssh_port = int(nas.get("ssh_port") or 22)

    out: list[dict] = []

    if policy.get("allow_winbox"):
        port = _DEFAULTS["winbox"]
        out.append({
            "service":     "winbox",
            "service_ar":  "وينبوكس (Winbox)",
            "host":        host,
            "port":        port,
            "url":         f"{host}:{port}",
            "clipboard":   f"{host}:{port}",
            "hint_ar":     (
                "افتح برنامج Winbox ثم أدخل العنوان "
                "والمنفذ كما هما."
            ),
        })

    if policy.get("allow_webfig_https"):
        port = _DEFAULTS["webfig_https"]
        url = f"https://{host}/"
        out.append({
            "service":     "webfig_https",
            "service_ar":  "ويب-فيغ آمن (HTTPS)",
            "host":        host,
            "port":        port,
            "url":         url,
            "clipboard":   url,
            "hint_ar":     (
                "افتح الرابط في المتصفّح. إذا حذّر المتصفّح من "
                "الشهادة، الراوتر يستخدم شهادة موقّعة ذاتياً."
            ),
        })

    if policy.get("allow_webfig_http"):
        port = _DEFAULTS["webfig_http"]
        url = f"http://{host}/"
        out.append({
            "service":     "webfig_http",
            "service_ar":  "ويب-فيغ (HTTP)",
            "host":        host,
            "port":        port,
            "url":         url,
            "clipboard":   url,
            "hint_ar":     (
                "اتصال غير مشفّر — يُفضَّل HTTPS إذا كانت "
                "الشبكة عامّة."
            ),
        })

    if policy.get("allow_ssh"):
        out.append({
            "service":     "ssh",
            "service_ar":  "SSH",
            "host":        host,
            "port":        ssh_port,
            "url":         f"{host}:{ssh_port}",
            "clipboard":   (
                f"ssh -p {ssh_port} admin@{host}"
                if ssh_port != 22
                else f"ssh admin@{host}"
            ),
            "hint_ar":     (
                "استخدم اسم المستخدم الخاصّ بك على الراوتر "
                "(غالباً admin)."
            ),
        })

    if policy.get("allow_api_ssl"):
        port = _DEFAULTS["api_ssl"]
        out.append({
            "service":     "api_ssl",
            "service_ar":  "MikroTik API — TLS",
            "host":        host,
            "port":        port,
            "url":         f"{host}:{port}",
            "clipboard":   f"{host}:{port}",
            "hint_ar":     (
                "للتطبيقات التي تتصل عبر MikroTik API "
                "(منفذ TLS)."
            ),
        })

    if policy.get("allow_api"):
        port = _DEFAULTS["api"]
        out.append({
            "service":     "api",
            "service_ar":  "MikroTik API",
            "host":        host,
            "port":        port,
            "url":         f"{host}:{port}",
            "clipboard":   f"{host}:{port}",
            "hint_ar":     (
                "اتصال API غير مشفّر — يُفضَّل API-SSL على "
                "الشبكات العامّة."
            ),
        })

    return out


__all__ = ["compute_access_urls"]
