#!/bin/bash
# HobeRadius — host-side WG sync helper.
#
# Triggered by wg-reload.path on any change to
# /etc/hoberadius/wg-peers.d/*.conf (HobeRadius container writes
# there) OR /etc/wireguard/wg0.conf (root edits).
#
# Merges the static interface block in wg0.conf with every
# per-peer file under wg-peers.d/, then pushes the union into the
# running wg0 interface via `wg syncconf`. The interface itself is
# never re-created — handshakes survive across reloads.
set -e

INTERFACE=wg0
MAIN_CONF=/etc/wireguard/wg0.conf
PEERS_DIR=/etc/hoberadius/wg-peers.d

if [ ! -f "$MAIN_CONF" ]; then
    echo "wg-reload: $MAIN_CONF missing — nothing to sync" >&2
    exit 0
fi

TMP=$(mktemp -p /run wg-reload-XXXXXX.conf)
chmod 0600 "$TMP"
trap 'rm -f "$TMP"' EXIT

# 1) main interface block (server private key, address, listen-port).
cat "$MAIN_CONF" > "$TMP"

# 2) every per-peer file, separated by a blank line so [Peer]
#    sections never run into each other.
if [ -d "$PEERS_DIR" ]; then
    shopt -s nullglob
    for f in "$PEERS_DIR"/*.conf; do
        echo "" >> "$TMP"
        cat "$f" >> "$TMP"
    done
fi

# 3) syncconf accepts only the peer set; wg-quick strip filters the
#    [Interface] section out.
/usr/bin/wg syncconf "$INTERFACE" <(/usr/bin/wg-quick strip "$TMP")
