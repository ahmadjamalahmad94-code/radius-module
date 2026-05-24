# Router Inventory and Risk Analysis

The first production-safe inventory source is pasted RouterOS output. The
parser accepts common `print detail` sections and extracts interfaces,
addresses, routes, pools, NAT rules, RADIUS entries, Hotspot, PPP, and
WireGuard records.

Secrets are sanitized before storage. The risk analyzer detects:

- WAN interface candidates
- VPN interface candidates
- existing default routes
- existing subnets and pools
- existing Hotspot and PPPoE services
- NAT count and service conflicts

Smart Hotspot/Broadband planning should consume the latest snapshot and avoid
the detected WAN/VPN interfaces and existing subnets.
