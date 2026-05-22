#!/bin/bash
# HobeRadius — host-side WG sync helper.
#
# Triggered by wg-reload.path on any change inside
# /etc/hoberadius/wg-peers.d/ (HobeRadius container's domain).
# Concatenates every *.conf in that directory (each file is
# already a peer-only fragment) and feeds the result straight to
# `wg syncconf`. The wg0 interface keeps its [Interface] block
# from /etc/wireguard/wg0.conf as set by wg-quick@wg0 — we never
# touch that file, never re-create the interface, and handshakes
# survive across reloads.
#
# Earlier draft tried to merge wg0.conf + peers.d via `wg-quick
# strip`, but strip rejects tmpfile names that don't follow the
# `<interface_name>.conf` pattern. The simpler peer-only path is
# both correct and immune to that picky parser.
set -e

INTERFACE=wg0
PEERS_DIR=/etc/hoberadius/wg-peers.d

TMP=$(mktemp -p /run wg-reload-XXXXXX)
chmod 0600 "$TMP"
trap 'rm -f "$TMP"' EXIT

# Concatenate every peer fragment. An empty directory results in
# an empty TMP — `wg syncconf wg0 <empty>` removes every peer,
# which is what we want when the operator deletes all of them.
if [ -d "$PEERS_DIR" ]; then
    shopt -s nullglob
    for f in "$PEERS_DIR"/*.conf; do
        cat "$f" >> "$TMP"
        echo "" >> "$TMP"
    done
fi

/usr/bin/wg syncconf "$INTERFACE" "$TMP"
