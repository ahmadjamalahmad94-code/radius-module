#!/bin/sh
# Hoberadius nginx entrypoint — runs nginx in the foreground +
# a sidecar loop that watches /etc/nginx/streams.d/ and reloads
# nginx when the NPC remote-tunnel config changes.
#
# Why a polling loop and not inotify? The nginx:alpine image
# doesn't ship inotify-tools. A 5s mtime/checksum poll is fine
# for our load (config changes only when the operator clicks
# "تطبيق آمن", which is rarely).
set -eu

STREAM_DIR="/etc/nginx/streams.d"
MARKER="$STREAM_DIR/.reload"
LOOP_INTERVAL=5
LAST_CHECKSUM=""

mkdir -p "$STREAM_DIR"

# Test config once before we start — if it's broken, fail loud
# so the container restarts and shows a clear error.
nginx -t

# Background watcher.
(
    while true; do
        # Combined checksum of every conf file in the stream
        # directory PLUS the reload marker mtime.
        CHECKSUM=$(
            (
                ls -la "$STREAM_DIR" 2>/dev/null || true
                md5sum "$STREAM_DIR"/*.conf 2>/dev/null || true
            ) | md5sum | cut -d' ' -f1
        )
        if [ "$CHECKSUM" != "$LAST_CHECKSUM" ]; then
            if [ -n "$LAST_CHECKSUM" ]; then
                # First iteration is just initialization — only
                # log + reload when we see a real change.
                echo "[nginx-entrypoint] stream config changed " \
                     "— reloading nginx"
                if nginx -t 2>&1; then
                    nginx -s reload || \
                        echo "[nginx-entrypoint] reload failed"
                else
                    echo "[nginx-entrypoint] config invalid " \
                         "— skipping reload"
                fi
            fi
            LAST_CHECKSUM="$CHECKSUM"
        fi
        sleep "$LOOP_INTERVAL"
    done
) &

# Foreground nginx — when it exits, the container exits.
exec nginx -g 'daemon off;'
