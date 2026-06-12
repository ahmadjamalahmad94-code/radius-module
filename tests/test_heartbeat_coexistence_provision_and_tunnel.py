"""Heartbeat coexistence: provision-on-link + radius_tunnel in ONE call.

Context — smart-merge: this file proves the two heartbeat surfaces survive
together after merging `fix/client-trigger-auto-provision` on top of main
(which already had `feat/customer-radius-tunnel-client`):

  • REQUEST: build_payload() ships BOTH the provision fields
    (`radius_auth_ip`, `realm`, `radius_auth_port`, `radius_acct_port`) at
    the top level — needed by the panel's provision_on_link — AND the
    `wg_radius` block (public_key, last_handshake_age_s, ...) needed by
    the customer-RADIUS tunnel design §3.1.

  • RESPONSE: send_heartbeat() processes BOTH the panel-minted
    `shared_secret` (persisted via store_provisioned_secret + surfaced as
    masked `provisioned_secret_masked`) AND the `radius_tunnel` block
    (handed to ProxyTunnelManager.apply_response which writes
    wg-radius.conf + proxy-client.conf).

A regression in either path would drop one of these — that's the whole
point of the smart-merge.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch


def _valid_radius_tunnel(secret: str = "from-panel-rt-secret"):
    return {
        "enabled": True,
        "tunnel_ip": "10.200.5.2",
        "tunnel_cidr": 16,
        "proxy_public_key": "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=",
        "proxy_endpoint":   "proxy.hoberadius.com:51822",
        "proxy_tunnel_ip":  "10.200.0.1",
        "allowed_ips":      ["10.200.0.1/32"],
        "persistent_keepalive": 25,
        "radius_secret":    secret,
        "listen_ports":     {"auth": 1812, "acct": 1813},
    }


def test_build_payload_carries_both_provision_and_wg_radius(tmp_path, monkeypatch):
    """REQUEST side — neither feature dropped the other's keys."""
    monkeypatch.setenv("HOBERADIUS_TUNNEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR", str(tmp_path / "clients"))
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "smart-merge-test-key")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_BASE_URL", "https://panel.local")

    from app.radius.services.license_admin_instance_health import InstanceHealthService

    payload = InstanceHealthService().build_payload(tenant_id=1)

    # provision-on-link fields (Branch 1)
    assert "license_key" in payload
    assert "radius_auth_ip" in payload, "provision_on_link contract dropped"
    assert "realm" in payload, "provision_on_link contract dropped"
    assert "radius_auth_port" in payload, "provision_on_link contract dropped"
    assert "radius_acct_port" in payload, "provision_on_link contract dropped"
    assert payload["radius_auth_port"] in (1812, 0) or isinstance(payload["radius_auth_port"], int)
    assert payload["radius_acct_port"] in (1813, 0) or isinstance(payload["radius_acct_port"], int)

    # CUSTOMER_RADIUS_TUNNEL_DESIGN §3.1 block (already-on-main feature)
    wg = payload.get("wg_radius")
    assert wg is not None, "wg_radius block dropped from heartbeat payload"
    assert wg.get("public_key"), "wg_radius.public_key missing/empty"
    assert re.fullmatch(r"[A-Za-z0-9+/]{43}=", wg["public_key"])
    assert "last_handshake_age_s" in wg
    assert "config_fingerprint" in wg


def test_send_heartbeat_applies_both_provisioning_and_tunnel(tmp_path, monkeypatch):
    """RESPONSE side — the panel returns a response that triggers BOTH
    side-effects. Verify provisioned_secret is persisted AND
    proxy-client.conf is written from the radius_tunnel block."""
    state_dir   = tmp_path / "state"
    clients_dir = tmp_path / "clients"
    monkeypatch.setenv("HOBERADIUS_TUNNEL_STATE_DIR",                  str(state_dir))
    monkeypatch.setenv("HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR",     str(clients_dir))
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "smart-merge-test-key")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_BASE_URL", "https://panel.local")

    from app.radius.services.license_admin_instance_health import InstanceHealthService

    svc = InstanceHealthService()
    monkeypatch.setattr(svc, "record_attempt", lambda attempt: {"status": "sent"})

    # Sentinel for provisioning. store_provisioned_secret persists to a
    # tenant_settings table that isn't migrated in this stub DB; stub it.
    seen: dict[str, str] = {}
    def _fake_store(secret: str, *, tenant_id: int = 1) -> str:
        seen["secret"] = secret
        return secret
    monkeypatch.setattr(
        "app.radius.services.license_admin_instance_health.store_provisioned_secret",
        _fake_store,
    )

    provision_secret_value = "panel-minted-shared-secret-xyz"
    tunnel_secret_value    = "panel-minted-route-secret-abc"
    fake_raw_response = {
        "ok": True,
        "status": "applied",
        "shared_secret": provision_secret_value,
        "radius_tunnel": _valid_radius_tunnel(secret=tunnel_secret_value),
    }

    with patch.object(
        svc.admin_client,
        "post_instance_heartbeat",
        return_value={
            "ok": True,
            "status": "sent",
            "response": fake_raw_response,
            "_raw_response": fake_raw_response,
        },
    ):
        out = svc.send_heartbeat(tenant_id=1, dry_run=False)

    # ── Provision-on-link side ────────────────────────────────────────
    assert out["ok"] is True
    assert seen.get("secret") == provision_secret_value, \
        "provisioned shared_secret was not handed to store_provisioned_secret"
    assert out.get("provisioned") is True
    assert out.get("provisioned_secret_masked"), \
        "masked secret missing — surface lost in the merge"

    # ── Tunnel side ──────────────────────────────────────────────────
    step = out["radius_tunnel_step"]
    assert step["ok"] is True, f"tunnel step did not apply: {step!r}"
    assert "wg.write" in step["actions"]
    assert "freeradius.write" in step["actions"]
    clients_path = clients_dir / "proxy-client.conf"
    wg_path      = state_dir / "wg-radius.conf"
    assert clients_path.exists()
    assert wg_path.exists()
    # The ROUTE secret (tunnel branch) is the one written into clients.conf.
    text = clients_path.read_text(encoding="utf-8")
    assert tunnel_secret_value in text
    # The PROVISION secret (link branch) MUST NOT leak into the FreeRADIUS
    # file — they're different secrets for different purposes.
    assert provision_secret_value not in text, \
        "provision shared_secret leaked into proxy-client.conf — wrong secret"

    # Neither secret should appear in the masked return field (it carries
    # only the provision secret, MASKED).
    masked = out["provisioned_secret_masked"]
    assert provision_secret_value not in masked, "raw provision secret leaked"
    assert tunnel_secret_value    not in masked, "tunnel secret leaked"
