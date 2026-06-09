-- RouterOS v6 VPN strategy: per-router tunnel profile (SSTP mgmt + L2TP/IPsec traffic).
--
-- Two SEPARATE tunnels (see docs/router_vpn/ROUTEROS_V6_VPN_STRATEGY.md):
--   * management tunnel — v7: WireGuard, v6: SSTP. Management ONLY, never
--     owns the default route.
--   * traffic tunnel — OPTIONAL, v6: L2TP/IPsec. IP-change / selected routing.
--
-- Columns live on nas_devices (the MT-focused table). The FreeRADIUS `nas`
-- table is intentionally NOT touched — tunnel config is a management/
-- provisioning concern, not RADIUS auth/accounting.
--
-- SECURITY: no plaintext tunnel secrets are stored here. Only masked
-- *_secret_ref pointers are persisted; the generated secret is shown once at
-- render time (same pattern as the WireGuard private key) and never logged.

-- ── Management tunnel (always-on; v7 WireGuard / v6 SSTP) ──
ALTER TABLE nas_devices ADD COLUMN management_tunnel_type TEXT NOT NULL DEFAULT 'none';
ALTER TABLE nas_devices ADD COLUMN management_tunnel_status TEXT NOT NULL DEFAULT 'not_configured';
ALTER TABLE nas_devices ADD COLUMN management_tunnel_interface_name TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN management_remote_address TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN management_vpn_subnet TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN management_secret_ref TEXT NOT NULL DEFAULT '';
-- Operator setting (Q3): verify the SSTP server certificate. 0 = no (default,
-- works without a deployed CA + carries a security warning), 1 = yes.
ALTER TABLE nas_devices ADD COLUMN sstp_verify_certificate INTEGER NOT NULL DEFAULT 0;

-- ── Traffic tunnel (optional; v6 L2TP/IPsec) ──
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_type TEXT NOT NULL DEFAULT 'none';
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_status TEXT NOT NULL DEFAULT 'not_configured';
ALTER TABLE nas_devices ADD COLUMN traffic_tunnel_interface_name TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN traffic_remote_address TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN traffic_vpn_subnet TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN traffic_mode TEXT NOT NULL DEFAULT 'disabled';
ALTER TABLE nas_devices ADD COLUMN traffic_routing_mark TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN traffic_source_pool TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN traffic_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nas_devices ADD COLUMN traffic_ipsec_secret_ref TEXT NOT NULL DEFAULT '';

-- ── Bookkeeping ──
ALTER TABLE nas_devices ADD COLUMN tunnel_updated_at INTEGER NOT NULL DEFAULT 0;
