"""npc_remote_tunnel — VPS-public TCP relay for NPC remote-access.

When a remote-access policy applies successfully, this service:

  1. Allocates a public TCP port on the VPS for every enabled
     service (winbox/ssh/api/api_ssl/webfig_http/webfig_https).
  2. Writes an nginx-stream config file mapping those public
     ports → router's reachable address (VPN peer if the router
     uses connection_mode='vpn', otherwise the public `address`).
  3. Touches a reload marker file so the nginx container's
     sidecar loop picks it up and runs `nginx -s reload`.

The operator then sees an additional URL list on the preview
page — "روابط من خارج الشبكة (عبر VPS)" — showing
`<VPS_public_host>:<public_port>` for each service.

This module is invoked from the apply route AFTER the apply
itself succeeded (so we don't allocate ports for failed
attempts). Rollback / failure paths can call
`release_for_router(...)` to mark mappings disabled.

Env vars consulted:

  HOBERADIUS_PUBLIC_HOST   — what the access-URLs section
                             advertises as the VPS public host.
                             Falls back to the request host at
                             render time.
  HOBERADIUS_NGINX_STREAM_DIR  — host-shared volume nginx
                                 watches. Default
                                 `/etc/hoberadius/nginx-streams.d`.
                                 The compose file mounts the
                                 same path read-only into the
                                 nginx container.
  HOBERADIUS_NPC_REMOTE_PORT_BASE / _CEILING — port range
                                 override.

No router contact from this module. Only DB + filesystem.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Mapping, Optional

from ..db.repos import (
    npc_remote_port_mappings_repo as ports_repo,
    nas_repo,
)
from .nas_connection import resolve_connection_address


_LOG = logging.getLogger(__name__)


# Default RouterOS service ports — what nginx forwards *to*
# on the router. The public-facing port comes from the
# allocator.
_UPSTREAM_PORTS: dict[str, int] = {
    ports_repo.SERVICE_WINBOX:       8291,
    ports_repo.SERVICE_WEBFIG_HTTPS: 443,
    ports_repo.SERVICE_WEBFIG_HTTP:  80,
    ports_repo.SERVICE_SSH:          22,   # overridden per nas
    ports_repo.SERVICE_API:          8728,
    ports_repo.SERVICE_API_SSL:      8729,
}


def _stream_dir() -> Path:
    raw = (os.environ.get("HOBERADIUS_NGINX_STREAM_DIR")
           or "/etc/hoberadius/nginx-streams.d")
    return Path(raw)


def _port_range() -> tuple[int, int]:
    base = int(os.environ.get(
        "HOBERADIUS_NPC_REMOTE_PORT_BASE",
        ports_repo.PORT_RANGE_BASE,
    ))
    ceil = int(os.environ.get(
        "HOBERADIUS_NPC_REMOTE_PORT_CEILING",
        ports_repo.PORT_RANGE_CEILING,
    ))
    return base, ceil


def public_host(tenant_id: Optional[int] = None) -> str:
    """The address the operator should see in the URLs. Empty
    string means "fall back to the browser's request host".

    Resolution order (DB first, env as fallback) so end-users can
    configure the VPS IP from the admin UI («إعدادات النظام» →
    «عنوان VPS العام») without SSHing into the host:

      1. tenant_settings row ``infra.public_host`` for the given
         tenant. If ``tenant_id`` is None we read DEFAULT_TENANT_ID.
      2. ``HOBERADIUS_PUBLIC_HOST`` env var (legacy / bootstrap path
         for fresh installs before the operator opens settings).
      3. Empty string — caller decides the next fallback (usually
         the WG tunnel IP from nas_devices, useful only from inside
         the VPN).
    """
    # DB lookup is best-effort — if anything goes wrong (no app
    # context, schema not migrated yet, etc.) we silently fall back
    # to the env var so this function never breaks the apply path.
    try:
        from ..db.repos import tenants_repo
        from ..core.tenant import DEFAULT_TENANT_ID
        tid = int(tenant_id) if tenant_id is not None else DEFAULT_TENANT_ID
        v = (tenants_repo.get_setting(tid, "infra.public_host", "") or "").strip()
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    return (os.environ.get("HOBERADIUS_PUBLIC_HOST") or "").strip()


# ─── Allocation API ────────────────────────────────────────


def _services_from_policy(policy: Mapping) -> list[str]:
    """Map remote-access policy toggles → port service ids.
    Skip services the policy didn't enable."""
    out: list[str] = []
    if policy.get("allow_winbox"):
        out.append(ports_repo.SERVICE_WINBOX)
    if policy.get("allow_webfig_https"):
        out.append(ports_repo.SERVICE_WEBFIG_HTTPS)
    if policy.get("allow_webfig_http"):
        out.append(ports_repo.SERVICE_WEBFIG_HTTP)
    if policy.get("allow_ssh"):
        out.append(ports_repo.SERVICE_SSH)
    if policy.get("allow_api_ssl"):
        out.append(ports_repo.SERVICE_API_SSL)
    if policy.get("allow_api"):
        out.append(ports_repo.SERVICE_API)
    return out


def ensure_tunnels_for_policy(
    *,
    tenant_id: int,
    policy: Mapping,
) -> list[dict]:
    """Allocate public ports for every service the policy
    enables. Returns the list of mapping dicts (existing or
    freshly created). Routers without a reachable address
    (no VPN peer, no public address) are skipped.

    Caller invokes `regenerate_and_reload()` after this to push
    the new ports into nginx — kept separate so a batch apply
    on multiple policies can amortise the reload."""
    router_id = int(policy.get("router_id") or 0)
    if not router_id:
        return []

    nas = nas_repo.get_nas(int(tenant_id), router_id)
    if nas is None:
        _LOG.warning(
            "remote_tunnel: router %d not found for tenant %d",
            router_id, tenant_id,
        )
        return []

    upstream_host = resolve_connection_address({
        "address":           nas.address,
        "connection_mode":   getattr(nas, "connection_mode",
                                       None) or "direct",
        "vpn_peer_address":  getattr(nas, "vpn_peer_address",
                                       None) or "",
    })
    if not upstream_host:
        _LOG.warning(
            "remote_tunnel: router %d has no resolvable upstream "
            "address — skipping mapping allocation.",
            router_id,
        )
        return []

    base, ceil = _port_range()
    out: list[dict] = []
    for svc in _services_from_policy(policy):
        upstream_port = _UPSTREAM_PORTS[svc]
        # SSH honours per-NAS ssh_port column.
        if svc == ports_repo.SERVICE_SSH:
            upstream_port = int(getattr(nas, "ssh_port",
                                        22) or 22)
        mapping = ports_repo.ensure(
            tenant_id=int(tenant_id),
            router_id=router_id,
            service=svc,
            upstream_address=upstream_host,
            upstream_port=upstream_port,
            port_base=base, port_ceiling=ceil,
        )
        out.append(mapping)
    return out


def release_for_router(router_id: int) -> int:
    """On router delete / rollback — disable every mapping for
    the router so nginx stops forwarding to it. Port stays
    reserved so a future re-apply gets the same number."""
    return ports_repo.disable_for_router(int(router_id))


# ─── nginx stream config generator ─────────────────────────


def render_stream_config(
    mappings: Optional[list[dict]] = None,
) -> str:
    """Emit the contents of a `streams.d/*.conf` file.

    Each enabled mapping becomes a `server { listen N; proxy_pass H:P; }`
    block. The wrapping `stream {}` block lives in the outer
    nginx.conf — we only emit the per-router blocks here, so
    multiple files can coexist if a future refactor splits per
    tenant.
    """
    rows = (mappings if mappings is not None
            else ports_repo.list_all_enabled())
    if not rows:
        return (
            "# NPC remote-tunnel: no active mappings.\n"
        )

    lines: list[str] = [
        "# NPC remote-tunnel — auto-generated. Do not edit.\n",
        "# (regenerated on every successful NPC apply.)\n",
        "\n",
    ]
    for m in rows:
        lines.extend([
            f"server {{\n",
            f"    listen {int(m['public_port'])};\n",
            f"    proxy_pass {m['upstream_address']}:"
            f"{int(m['upstream_port'])};\n",
            f"    proxy_connect_timeout 10s;\n",
            f"    proxy_timeout         1h;\n",
            f"    # router_id={int(m['router_id'])} "
            f"service={m['service']}\n",
            f"}}\n",
            f"\n",
        ])
    return "".join(lines)


def write_stream_config(
    out_dir: Optional[Path] = None,
    *,
    file_name: str = "npc_remote.conf",
    contents: Optional[str] = None,
) -> Path:
    """Write the stream config into the nginx shared volume.
    Returns the path written. Creates the dir if missing."""
    dir_path = out_dir if out_dir is not None else _stream_dir()
    dir_path.mkdir(parents=True, exist_ok=True)
    out_path = dir_path / file_name
    body = (contents if contents is not None
            else render_stream_config())
    # Atomic write via temp + rename so nginx never reads a
    # half-written file.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(out_path)
    return out_path


def touch_reload_marker(
    out_dir: Optional[Path] = None,
) -> Path:
    """Update the mtime of a marker file the nginx sidecar
    watches. Cheap signal — the watcher checksum-compares the
    config files themselves, so this is purely cosmetic for
    operators reading logs."""
    dir_path = out_dir if out_dir is not None else _stream_dir()
    dir_path.mkdir(parents=True, exist_ok=True)
    marker = dir_path / ".reload"
    marker.touch()
    return marker


def regenerate_and_reload(
    out_dir: Optional[Path] = None,
) -> dict:
    """Convenience — write config + touch marker. The nginx
    sidecar loop picks up the change on its next 5s tick.
    Returns a small status dict for logging/audit."""
    cfg_path = write_stream_config(out_dir=out_dir)
    marker = touch_reload_marker(out_dir=out_dir)
    return {
        "config_path":  str(cfg_path),
        "marker_path":  str(marker),
        "mapping_count": len(ports_repo.list_all_enabled()),
    }


__all__ = [
    "ensure_tunnels_for_policy",
    "release_for_router",
    "render_stream_config",
    "write_stream_config",
    "touch_reload_marker",
    "regenerate_and_reload",
    "public_host",
]
