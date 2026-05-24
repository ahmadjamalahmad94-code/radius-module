# Added Services Automation

The setup wizard exposes added services through a catalog rather than
duplicating existing network-policy logic.

Supported delegates:

- Walled Garden: existing NPC walled-garden planner
- Block Sites: existing NPC web-block planner
- Site Exit: existing site-exit planner where required inputs are available

Anti-sharing/tethering remains visible but returns `not_supported_yet` until a
stable setup-wizard-safe planner exists.

All apply paths must go through the guarded setup wizard operation engine.
