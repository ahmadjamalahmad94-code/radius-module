#!/bin/bash
# Router 14 ping-failure diagnostic — one-paste runner for VPS.
# Captures every signal needed to confirm or refute the hypothesis in
# ROUTER_14_PING_DIAGNOSIS.md. Read-only; safe to run on production.
#
# USAGE:
#   1. SSH into VPS (187.77.70.18)
#   2. Copy-paste this entire block into the shell
#   3. Copy the entire output back to Claude
#
# What it checks:
#   A. Whether router 14's pubkey is registered on wg0 right now
#   B. Whether wg0's server pubkey matches what router 14 is dialling
#   C. Whether the lab-mode env flags are set (if not → that's why wizard
#      didn't apply the peer)
#   D. Whether a peers.d fragment exists for router 14
#   E. Whether VPS firewall allows UDP/51820 inbound
#   F. Whether handshake packets are arriving from the router right now
#   G. Recent wg-quick / kernel logs for handshake errors

set +e   # don't bail on individual check failures
ROUTER14_PUBKEY="o5ZzsCcC7iW5OzxLM//V5nUfEe6lLw613PQbdbNn1Gk="
ROUTER14_VPN_IP="10.10.0.15"
EXPECTED_SERVER_PUBKEY="FCCFdzxg2qicb1yx8ALX9vGFmYe/Nur1USL68zOTY30="
ROUTER_PUBLIC_IP=""  # we'll learn this from tcpdump

section() { printf "\n══════════════════════════════════════════════════════\n%s\n══════════════════════════════════════════════════════\n" "$1"; }

section "A. router 14 pubkey on wg0?"
sudo wg show wg0 2>/dev/null | grep -B0 -A4 "$ROUTER14_PUBKEY" \
  || echo "❌ NOT FOUND — router pubkey is NOT registered on wg0"

section "B. wg0 server pubkey vs what router is dialling"
ACTUAL=$(sudo wg show wg0 public-key 2>/dev/null)
echo "wg0 public key:        $ACTUAL"
echo "router is dialling:    $EXPECTED_SERVER_PUBKEY"
[ "$ACTUAL" = "$EXPECTED_SERVER_PUBKEY" ] && echo "✓ MATCH" || echo "❌ MISMATCH — router has stale server pubkey"

section "C. lab-mode + apply flags (hoberadius service)"
for unit in hoberadius hoberadius-admin hoberadius.service; do
    if systemctl status "$unit" >/dev/null 2>&1; then
        echo "--- $unit ---"
        systemctl show "$unit" -p Environment 2>/dev/null | tr ' ' '\n' \
            | grep -E 'HOBERADIUS_SETUP_WIZARD_(LAB_MODE|SERVER_WG_APPLY|SERVER_WG_READINESS|SERVER_WG_REAL_ADAPTER)' \
            || echo "  (no relevant env vars found)"
        break
    fi
done
echo "--- docker (if used) ---"
if command -v docker >/dev/null 2>&1; then
    CONTAINER=$(docker ps --format '{{.Names}}' | grep -E 'hoberadius|radius' | head -1)
    if [ -n "$CONTAINER" ]; then
        echo "container: $CONTAINER"
        docker exec "$CONTAINER" env 2>/dev/null | grep HOBERADIUS_SETUP_WIZARD \
            || echo "  (no HOBERADIUS_SETUP_WIZARD vars set)"
    else
        echo "(no hoberadius container running)"
    fi
fi

section "D. peers.d fragment for router 14?"
PEERS_DIR=/etc/hoberadius/wg-peers.d
echo "dir: $PEERS_DIR"
ls -la "$PEERS_DIR/" 2>/dev/null || echo "❌ dir doesn't exist"
echo "--- looking for router 14 specifically ---"
for f in "$PEERS_DIR"/*router*14*.conf "$PEERS_DIR"/router-14.conf "$PEERS_DIR"/*0014*.conf; do
    [ -f "$f" ] && echo "found: $f" && cat "$f"
done 2>/dev/null
echo "--- any fragment containing router 14's pubkey ---"
sudo grep -lR "$ROUTER14_PUBKEY" "$PEERS_DIR" 2>/dev/null || echo "(none)"

section "E. firewall on UDP/51820"
echo "--- iptables INPUT chain ---"
sudo iptables -nvL INPUT 2>/dev/null | grep -E '51820|udp' | head -10
echo "--- ufw (if active) ---"
sudo ufw status 2>/dev/null | head -10
echo "--- listening on 51820? ---"
sudo ss -lunp 2>/dev/null | grep 51820 || echo "❌ NOTHING listening on UDP/51820"

section "F. 5-second packet capture from router"
echo "Capturing UDP/51820 packets for 5 seconds (router should be retrying handshake)..."
sudo timeout 5 tcpdump -ni any 'udp port 51820' -c 20 2>&1 | tail -25 \
  || echo "(tcpdump not installed or no packets)"

section "G. recent kernel WG logs"
sudo dmesg 2>/dev/null | grep -i wireguard | tail -10
echo "--- journalctl wg-quick ---"
sudo journalctl -u wg-quick@wg0 -n 20 --no-pager 2>/dev/null | tail -20

section "H. nas_devices row for router 14"
DB=""
for candidate in /opt/hoberadius/instance/hoberadius.db /var/lib/hoberadius/hoberadius.db /etc/hoberadius/hoberadius.db; do
    [ -f "$candidate" ] && DB="$candidate" && break
done
if [ -n "$DB" ]; then
    echo "db: $DB"
    sqlite3 "$DB" "SELECT id, nas_name, address, vpn_peer_address, vpn_public_key, vpn_assigned_ip, connection_mode FROM nas_devices WHERE id=14 OR vpn_peer_address='$ROUTER14_VPN_IP' OR vpn_public_key='$ROUTER14_PUBKEY';" 2>&1
    echo "--- router_provisioning_registry ---"
    sqlite3 "$DB" "SELECT id, router_vpn_ip, server_vpn_ip, lifecycle_state, status FROM router_provisioning_registry WHERE id=14 OR router_vpn_ip='$ROUTER14_VPN_IP';" 2>&1
    echo "--- prepared_wireguard_peers status for router 14 ---"
    sqlite3 "$DB" "SELECT id, registry_id, status, router_public_key_masked, allowed_ips FROM prepared_wireguard_peers WHERE registry_id=14;" 2>&1
    echo "--- prepared_wireguard_peer_operations latest 5 for router 14 ---"
    sqlite3 "$DB" "SELECT id, operation_type, status, json_extract(error_json,'\$.code') AS err_code, created_at FROM prepared_wireguard_peer_operations WHERE registry_id=14 ORDER BY id DESC LIMIT 5;" 2>&1
else
    echo "(could not locate hoberadius.db)"
fi

section "DONE"
echo "Copy everything above (from section A onwards) and paste back to Claude."
