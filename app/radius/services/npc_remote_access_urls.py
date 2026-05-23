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


def compute_remote_access_urls(
    policy: Mapping,
    public_host: str,
    mappings: list[dict],
) -> list[dict]:
    """Build the "from outside the network (via VPS)" URL list.

    Each enabled service in `policy` is matched with its
    `npc_remote_port_mappings` row to produce a URL the
    operator can use from anywhere on the internet —
    `<public_host>:<public_port>` — that the VPS forwards to
    the router's local port over the WG tunnel.

    Empty list if no mappings or no public host configured.
    """
    if not public_host:
        return []
    by_service: dict[str, dict] = {
        str(m["service"]): m for m in (mappings or [])
        if bool(m.get("enabled"))
    }
    if not by_service:
        return []

    out: list[dict] = []

    def _emit(svc_key: str, label_ar: str, hint_ar: str,
              url_factory):
        m = by_service.get(svc_key)
        if m is None:
            return
        port = int(m["public_port"])
        url, clip = url_factory(public_host, port)
        out.append({
            "service":     svc_key,
            "service_ar":  label_ar,
            "host":        public_host,
            "port":        port,
            "url":         url,
            "clipboard":   clip,
            "hint_ar":     hint_ar,
        })

    if policy.get("allow_winbox"):
        _emit(
            "winbox", "وينبوكس (Winbox) — من خارج الشبكة",
            "افتح Winbox وأدخل عنوان VPS مع الـ port المعطى.",
            lambda h, p: (f"{h}:{p}", f"{h}:{p}"),
        )
    if policy.get("allow_webfig_https"):
        _emit(
            "webfig_https",
            "ويب-فيغ آمن (HTTPS) — من خارج الشبكة",
            "افتح الرابط في المتصفّح من أي مكان.",
            lambda h, p: (
                f"https://{h}:{p}/",
                f"https://{h}:{p}/",
            ),
        )
    if policy.get("allow_webfig_http"):
        _emit(
            "webfig_http",
            "ويب-فيغ (HTTP) — من خارج الشبكة",
            "اتصال غير مشفّر — يُفضَّل HTTPS.",
            lambda h, p: (
                f"http://{h}:{p}/",
                f"http://{h}:{p}/",
            ),
        )
    if policy.get("allow_ssh"):
        _emit(
            "ssh", "SSH — من خارج الشبكة",
            "استخدم اسم المستخدم على الراوتر.",
            lambda h, p: (
                f"{h}:{p}",
                f"ssh -p {p} admin@{h}",
            ),
        )
    if policy.get("allow_api_ssl"):
        _emit(
            "api_ssl",
            "MikroTik API — TLS — من خارج الشبكة",
            "للتطبيقات التي تتصل عبر MikroTik API (TLS).",
            lambda h, p: (f"{h}:{p}", f"{h}:{p}"),
        )
    if policy.get("allow_api"):
        _emit(
            "api", "MikroTik API — من خارج الشبكة",
            "API غير مشفّر — يُفضَّل API-SSL.",
            lambda h, p: (f"{h}:{p}", f"{h}:{p}"),
        )

    return out


__all__ = ["compute_access_urls", "compute_remote_access_urls"]
