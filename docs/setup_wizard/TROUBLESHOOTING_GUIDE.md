# Setup Wizard Troubleshooting

Common blocked states:

- `feature_flag_disabled`: live apply is intentionally off.
- `dry_run_required`: generate a dry-run before apply.
- `confirmation_required`: paste the exact confirmation phrase.
- `probe_unavailable`: use pasted output mode or configure read-only probes.
- `route_missing`: inspect `/ip route print detail`.
- `radius_server_unreachable`: confirm VPN reachability and UDP 1812/1813.

Support bundles mask secrets and include the latest diagnostics, operations,
and snapshot summary.
