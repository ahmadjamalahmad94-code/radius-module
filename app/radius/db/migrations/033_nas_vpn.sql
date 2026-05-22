-- K1.1: VPN connection layer for NAS / MikroTik routers.
--
-- The admin app dials each router by IP today. For routers behind
-- NAT (common in ISP / hotel setups) we add an alternative path via
-- a WireGuard tunnel: the router stays at its WG peer address inside
-- a private subnet (e.g. 10.10.0.5), and the admin app uses that
-- address when `connection_mode = 'vpn'`.
--
-- See docs/MIKROTIK_CONTROL_PLAN.md K1 + docs/WIREGUARD_SETUP.md
-- (added in phase K12) for the operator setup.

-- ── nas_devices (MT-focused table) ─────────────────────────────────
ALTER TABLE nas_devices ADD COLUMN connection_mode TEXT NOT NULL DEFAULT 'direct';
ALTER TABLE nas_devices ADD COLUMN vpn_peer_address TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN vpn_interface TEXT NOT NULL DEFAULT 'wg0';
ALTER TABLE nas_devices ADD COLUMN vpn_public_key TEXT NOT NULL DEFAULT '';
ALTER TABLE nas_devices ADD COLUMN vpn_last_handshake_ts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nas_devices ADD COLUMN vpn_assigned_ip TEXT NOT NULL DEFAULT '';

-- ── nas (FreeRADIUS table) — same columns so a vendor lookup works
-- on either row shape; the resolver code in K1.2 accepts whichever
-- table the caller passes in.
ALTER TABLE nas ADD COLUMN connection_mode TEXT NOT NULL DEFAULT 'direct';
ALTER TABLE nas ADD COLUMN vpn_peer_address TEXT NOT NULL DEFAULT '';
ALTER TABLE nas ADD COLUMN vpn_interface TEXT NOT NULL DEFAULT 'wg0';
ALTER TABLE nas ADD COLUMN vpn_public_key TEXT NOT NULL DEFAULT '';
ALTER TABLE nas ADD COLUMN vpn_last_handshake_ts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nas ADD COLUMN vpn_assigned_ip TEXT NOT NULL DEFAULT '';
