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

# First-boot helper: the host-side /etc/hoberadius/nginx-streams.d
# is bind-mounted into both the nginx container (here) and the
# hoberadius container. nginx runs as root by default in the
# image; hoberadius runs as a non-root user. So we chmod the
# directory once at boot to make sure hoberadius can write into
# it. World-writable is OK here — the dir lives inside /etc/
# hoberadius/ on the host, only the two containers reach it.
# Skips silently if perms are already permissive.
if [ -w "$STREAM_DIR" ]; then
    chmod 1777 "$STREAM_DIR" 2>/dev/null || true
fi

# ─────────────────────────────────────────────────────────────────────────
# HTTPS on :8443 — self-signed, PURELY ADDITIVE.
#
# Hard rule: the panel keeps serving on :80 no matter what. We enable the 8443
# TLS server block ONLY when a cert is present, so a failed cert-gen (e.g. an
# offline box with no openssl) can NEVER break :80 — nginx just serves http.
# The cert is generated once and persisted in /etc/nginx/tls (host bind), and
# the 8443 block is copied into conf.d only after the cert exists.
# ─────────────────────────────────────────────────────────────────────────
TLS_DIR="/etc/nginx/tls"
TLS_CRT="$TLS_DIR/selfsigned.crt"
TLS_KEY="$TLS_DIR/selfsigned.key"
TLS_TEMPLATE="/etc/nginx/tls-template/8443.conf"
TLS_ENABLED="/etc/nginx/conf.d/8443-ssl.conf"

mkdir -p "$TLS_DIR" 2>/dev/null || true
if [ ! -s "$TLS_CRT" ] || [ ! -s "$TLS_KEY" ]; then
    command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl >/dev/null 2>&1 || true
    if command -v openssl >/dev/null 2>&1; then
        echo "[nginx-entrypoint] generating self-signed cert for :8443"
        openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
            -subj "/CN=hoberadius-panel" \
            -keyout "$TLS_KEY" -out "$TLS_CRT" >/dev/null 2>&1 || true
        chmod 600 "$TLS_KEY" 2>/dev/null || true
    fi
fi
if [ -s "$TLS_CRT" ] && [ -s "$TLS_KEY" ] && [ -f "$TLS_TEMPLATE" ]; then
    cp "$TLS_TEMPLATE" "$TLS_ENABLED" 2>/dev/null || true
    # Trial-validate the FULL config WITH the 8443 block. If anything about it
    # is wrong, roll it back so :80 can never be taken down by the TLS listener.
    if nginx -t >/dev/null 2>&1; then
        echo "[nginx-entrypoint] HTTPS on :8443 enabled (self-signed)"
    else
        rm -f "$TLS_ENABLED" 2>/dev/null || true
        echo "[nginx-entrypoint] :8443 block failed validation — disabled, serving :80 only"
    fi
else
    # No cert → make sure no stale 8443 block lingers, then serve :80 only.
    rm -f "$TLS_ENABLED" 2>/dev/null || true
    echo "[nginx-entrypoint] HTTPS :8443 NOT enabled (no cert) — serving :80 only"
fi

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
