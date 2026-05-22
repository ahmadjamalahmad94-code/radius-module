#!/bin/bash
# HobeRadius — host-side WG peer sync.
#
# Computes the symmetric difference between:
#   • peers currently attached to wg0  (via `wg show wg0 peers`)
#   • peers desired according to       /etc/hoberadius/wg-peers.d/*.conf
# then applies it via `wg set` (NOT `wg syncconf` — that command
# resets ListenPort + PrivateKey when the input file lacks a
# complete [Interface] section, as we discovered on the live VPS).
#
# The interface itself (private key, listen port, address, MTU)
# is owned by wg-quick@wg0 and /etc/wireguard/wg0.conf — this
# script never touches them. Handshakes survive every reload.
set -e

INTERFACE=wg0
PEERS_DIR=/etc/hoberadius/wg-peers.d
WG=/usr/bin/wg


# Pull `Key = value` style fields out of one *.conf fragment.
# Returns the first match, trailing-whitespace-trimmed (the value
# itself may contain a base64 trailing `=`, so we only strip
# whitespace).
extract() {
    local file="$1" field="$2"
    sed -nE "s/^[[:space:]]*${field}[[:space:]]*=[[:space:]]*(.+)$/\1/p" "$file" \
        | head -1 \
        | sed -E 's/[[:space:]]+$//'
}


# 1) Snapshot the peers currently bound to wg0.
declare -A CURRENT
if /usr/sbin/ip link show "$INTERFACE" >/dev/null 2>&1; then
    while read -r pk; do
        [ -n "$pk" ] && CURRENT["$pk"]=1
    done < <($WG show "$INTERFACE" peers 2>/dev/null || true)
fi


# 2) Walk peers.d and add / update every well-formed fragment.
declare -A DESIRED
shopt -s nullglob
for f in "$PEERS_DIR"/*.conf; do
    pk=$(extract "$f" "PublicKey")
    ips=$(extract "$f" "AllowedIPs")
    ka=$(extract "$f" "PersistentKeepalive")

    if [ -z "$pk" ] || [ -z "$ips" ]; then
        echo "wg-reload: skipping $f (missing PublicKey or AllowedIPs)" >&2
        continue
    fi

    args=(set "$INTERFACE" peer "$pk" allowed-ips "$ips")
    if [ -n "$ka" ]; then
        args+=(persistent-keepalive "$ka")
    fi
    if ! $WG "${args[@]}"; then
        echo "wg-reload: $WG set failed for ${f} (pk=$pk)" >&2
        continue
    fi
    DESIRED["$pk"]=1
done


# 3) Remove peers that are on the wire but no longer in peers.d.
for pk in "${!CURRENT[@]}"; do
    if [ -z "${DESIRED[$pk]:-}" ]; then
        $WG set "$INTERFACE" peer "$pk" remove
    fi
done
