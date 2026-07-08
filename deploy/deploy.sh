#!/usr/bin/env bash
# HobeRadius — VPS deploy automation
#
# الاستخدام (على VPS Ubuntu):
#   sudo bash deploy/deploy.sh init       # أول مرة
#   sudo bash deploy/deploy.sh upgrade    # تحديث
#   sudo bash deploy/deploy.sh tls DOMAIN # إصدار/تجديد TLS
#   sudo bash deploy/deploy.sh status     # حالة + healthcheck

set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
COMPOSE="docker compose -f $PROJECT_ROOT/deploy/docker-compose.yml"

log()   { echo "[$(date -u +%H:%M:%SZ)] $*"; }
die()   { echo "FATAL: $*" >&2; exit 1; }

# Install/refresh the HOST self-update agent (systemd timer + /usr/local/bin +
# /etc/hoberadius/updater.env). Idempotent + self-guarding so it never aborts a
# deploy — existing installs pick it up on the next `init`/`upgrade`.
install_self_update_agent() {
    local inst="$PROJECT_ROOT/deploy/updater/install-updater.sh"
    if [ -f "$inst" ]; then
        HR_ROOT="$PROJECT_ROOT" bash "$inst" \
            || log "⚠ تثبيت وكيل التحديث الذاتيّ أرجع خطأ (تابع الـ deploy)"
    else
        log "⚠ مثبّت وكيل التحديث الذاتيّ غير موجود: $inst (تخطّي)"
    fi
}

cmd_init() {
    log "1) تثبيت المتطلّبات النظامية ..."
    if ! command -v docker >/dev/null; then
        apt update
        apt install -y docker.io docker-compose-plugin curl
        systemctl enable --now docker
    fi
    if ! command -v openssl >/dev/null; then
        apt install -y openssl
    fi

    log "2) إنشاء .env إن لم يوجد ..."
    if [ ! -f "$ENV_FILE" ]; then
        cp "$PROJECT_ROOT/deploy/.env.example" "$ENV_FILE"
        SECRET="$(openssl rand -hex 32)"
        sed -i "s|change-me-to-32-random-bytes-please|$SECRET|" "$ENV_FILE"
        log "   .env أُنشئ مع FLASK_SECRET عشوائي. عدّله إن أردت."
    else
        log "   .env موجود — تخطّي."
    fi

    log "3) تجهيز المجلدات ..."
    mkdir -p instance backups logs
    chmod 0700 instance

    # NPC remote-tunnel — nginx streams shared volume. Must
    # exist before docker compose up, or docker bind-mounts it
    # as an empty directory at the wrong perms (postmortem #1
    # / #5 / #7).
    mkdir -p /etc/hoberadius/nginx-streams.d
    # Ownership: gid=999 matches the hoberadius container's
    # primary group. The setgid+rwx-on-group bits (2775)
    # mean any new file inherits gid=999 too. setgid avoids
    # the chmod 1777 fallback (world-writable) that the
    # earlier postmortems papered over the issue with.
    chgrp 999 /etc/hoberadius/nginx-streams.d 2>/dev/null || true
    chmod 2775 /etc/hoberadius/nginx-streams.d

    # OPT-IN self-update shared dir: container writes update-request.json,
    # host updater writes update-status.json. gid=999 so `hr` can write.
    mkdir -p /var/lib/hoberadius
    chgrp 999 /var/lib/hoberadius 2>/dev/null || true
    chmod 2775 /var/lib/hoberadius

    log "3b) فحص ملفّات nginx المطلوبة ..."
    for f in deploy/nginx-main.conf deploy/nginx-entrypoint.sh \
             deploy/docker-compose.yml; do
        if [ ! -f "$f" ]; then
            die "ملفّ مفقود: $f — اسحب آخر تحديث بـ git pull أولاً."
        fi
    done

    log "4) بناء الصورة وتشغيلها ..."
    $COMPOSE up -d --build

    log "5) انتظار healthcheck ..."
    for i in $(seq 1 30); do
        if curl -fsS http://127.0.0.1:80/admin/radius/_health >/dev/null 2>&1 \
           || curl -fsS http://127.0.0.1:8000/admin/radius/_health >/dev/null 2>&1; then
            log "   ✓ healthy"
            break
        fi
        sleep 2
    done

    log "6) تحقّق readiness:"
    curl -fsS http://127.0.0.1:80/admin/radius/_healthz || \
    curl -fsS http://127.0.0.1:8000/admin/radius/_healthz || true
    echo

    log "7) تثبيت وكيل التحديث الذاتيّ على المضيف (systemd) ..."
    install_self_update_agent

    log "✅ تم. التالي:"
    echo "   - لو لديك domain: sudo bash deploy/deploy.sh tls YOUR_DOMAIN"
    echo "   - login: http://YOUR_VPS_IP/admin/radius/login  (admin / admin — غيّرها فورًا)"
}

cmd_upgrade() {
    log "1) git pull ..."
    # --autostash: على السيرفر توجد تعديلات محليّة دائمة على ملفات متتبَّعة
    # (أبرزها deploy/nginx.conf بعد أمر tls الذي يستبدل YOUR_DOMAIN بالدومين
    # عبر sed -i) — بدونها يفشل السحب بـ"You have unstaged changes" ويموت
    # السكربت قبل البناء والتنظيف. autostash يخبّئها ويعيد تطبيقها تلقائيًا.
    cd "$PROJECT_ROOT" && git pull --rebase --autostash
    log "2) فحص ملفّات nginx + تجهيز streams.d ..."
    for f in deploy/nginx-main.conf deploy/nginx-entrypoint.sh \
             deploy/docker-compose.yml; do
        if [ ! -f "$f" ]; then
            die "ملفّ مفقود بعد git pull: $f"
        fi
    done
    mkdir -p /etc/hoberadius/nginx-streams.d
    # Ownership: gid=999 matches the hoberadius container's
    # primary group. The setgid+rwx-on-group bits (2775)
    # mean any new file inherits gid=999 too. setgid avoids
    # the chmod 1777 fallback (world-writable) that the
    # earlier postmortems papered over the issue with.
    chgrp 999 /etc/hoberadius/nginx-streams.d 2>/dev/null || true
    chmod 2775 /etc/hoberadius/nginx-streams.d
    # OPT-IN self-update shared dir (see cmd_init).
    mkdir -p /var/lib/hoberadius
    chgrp 999 /var/lib/hoberadius 2>/dev/null || true
    chmod 2775 /var/lib/hoberadius
    log "3) build + restart ..."
    $COMPOSE up -d --build
    # تنظيف آمن بعد كل ترقية: الصورة السابقة تصبح dangling (<none>) بعد
    # البناء الجديد وتتراكم صور قديمة + build cache مع كل deploy (هذا ما
    # ضخّم docker system df إلى عشرات الغيغا). prune هنا يحذف فقط
    # الطبقات/الصور غير المرجعيّة — لا يمسّ الصور المستعملة ولا الـvolumes
    # ولا أي بيانات إنتاج (instance/ و backups/ هي bind mounts خارج docker).
    log "4) تنظيف الصور القديمة وbuild cache ..."
    docker image prune -f
    docker builder prune -f --keep-storage 2GB 2>/dev/null || true
    log "5) تثبيت/تحديث وكيل التحديث الذاتيّ على المضيف (systemd) ..."
    install_self_update_agent
    log "6) status:"
    $COMPOSE ps
}

cmd_tls() {
    local DOMAIN="${1:-}"
    [ -z "$DOMAIN" ] && die "usage: deploy.sh tls <domain>"
    if ! command -v certbot >/dev/null; then
        apt install -y certbot
    fi
    log "1) إيقاف nginx مؤقتًا لإصدار الشهادة ..."
    $COMPOSE stop nginx || true
    certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos \
        -m "admin@${DOMAIN}" --keep-until-expiring

    log "2) تعديل nginx.conf لاستخدام الدومين ..."
    sed -i "s/YOUR_DOMAIN/$DOMAIN/g" deploy/nginx.conf
    log "3) إعادة التشغيل ..."
    $COMPOSE up -d nginx

    log "4) إعداد التجديد التلقائي ..."
    CRON_LINE="0 3 * * * certbot renew --quiet && cd $PROJECT_ROOT && $COMPOSE exec nginx nginx -s reload"
    (crontab -l 2>/dev/null | grep -v 'certbot renew' ; echo "$CRON_LINE") | crontab -

    log "✅ TLS مفعَّل. تحقّق: https://$DOMAIN/admin/radius/_health"
}

cmd_status() {
    log "Containers:"
    $COMPOSE ps
    log ""
    log "Health:"
    curl -fsS http://127.0.0.1:80/admin/radius/_healthz 2>/dev/null || \
    curl -fsS http://127.0.0.1:8000/admin/radius/_healthz || echo "(غير متاح)"
    echo
    log "Disk:"
    du -sh instance/ backups/ logs/ 2>/dev/null || true
}

cmd_backup() {
    log "تشغيل backup يدوي ..."
    $COMPOSE exec backup /usr/local/bin/backup.sh
    ls -lh "$PROJECT_ROOT/backups/" | tail -5
}

cmd_logs() {
    $COMPOSE logs --tail=200 -f app
}

cmd_init_wg_reloader() {
    # Phase M — sets up the split WireGuard control plane:
    #   • /etc/wireguard/wg0.conf       stays root-only (server priv key)
    #   • /etc/hoberadius/wg-peers.d/   container-writable peer files
    #   • /usr/local/bin/wg-reload.sh   merges + wg syncconf
    #   • wg-reload.path + .service     watcher → reloader
    # Idempotent — safe to re-run after upgrades.
    local PEERS_DIR=/etc/hoberadius/wg-peers.d
    local HR_GID=999    # matches the `hr` user inside the container

    log "1) check WireGuard tools ..."
    if ! command -v wg >/dev/null || ! command -v wg-quick >/dev/null; then
        die "wireguard-tools not installed on the host. Run: apt install -y wireguard"
    fi

    log "2) prepare ${PEERS_DIR} (gid=${HR_GID} for container write access) ..."
    mkdir -p "$PEERS_DIR"
    # GID 999 is the `hr` group inside the container image. We
    # don't need a matching host-side group name; numeric GID is
    # enough for the bind-mount.
    chgrp "$HR_GID" "$PEERS_DIR"
    chmod 0775 "$PEERS_DIR"

    log "3) migrate any existing [Peer] blocks out of wg0.conf ..."
    if grep -q '^\[Peer\]' /etc/wireguard/wg0.conf 2>/dev/null; then
        cp /etc/wireguard/wg0.conf /etc/wireguard/wg0.conf.pre-migrate.$(date +%s)
        local mig_count
        mig_count=$(awk '
            BEGIN { in_peer = 0; idx = 0 }
            /^\[Peer\]/ { idx++; in_peer = 1
                          fname = "/etc/hoberadius/wg-peers.d/migrated-" idx ".conf"
                          print "[Peer]" > fname
                          next }
            /^\[/ { in_peer = 0 }
            { if (in_peer) print >> fname }
            END { print idx }
        ' /etc/wireguard/wg0.conf)
        # Strip [Peer] sections from wg0.conf (keeps [Interface] only).
        awk '
            /^\[Peer\]/ { in_peer = 1; next }
            /^\[/ { in_peer = 0 }
            { if (!in_peer) print }
        ' /etc/wireguard/wg0.conf > /etc/wireguard/wg0.conf.new
        mv /etc/wireguard/wg0.conf.new /etc/wireguard/wg0.conf
        chmod 0600 /etc/wireguard/wg0.conf
        # Make the migrated files readable by container too.
        chgrp "$HR_GID" "$PEERS_DIR"/migrated-*.conf 2>/dev/null || true
        chmod 0660 "$PEERS_DIR"/migrated-*.conf 2>/dev/null || true
        log "   migrated $mig_count peer(s) to $PEERS_DIR/"
    else
        log "   no [Peer] blocks in wg0.conf — nothing to migrate."
    fi

    log "4) install reload script ..."
    install -m 0755 "$PROJECT_ROOT/deploy/wg-reload.sh" /usr/local/bin/wg-reload.sh

    log "5) install systemd units ..."
    install -m 0644 "$PROJECT_ROOT/deploy/wg-reload.service" /etc/systemd/system/wg-reload.service
    install -m 0644 "$PROJECT_ROOT/deploy/wg-reload.path"    /etc/systemd/system/wg-reload.path

    log "6) reload + enable ..."
    systemctl daemon-reload
    systemctl enable --now wg-reload.path

    log "7) reset wg0 to a clean state (in case a prior bad reload"
    log "   wiped its private key or listen port) ..."
    ip link delete wg0 2>/dev/null || true
    wg-quick up wg0

    log "8) apply peers.d via the reload script ..."
    /usr/local/bin/wg-reload.sh

    log "9) verify:"
    systemctl --no-pager status wg-reload.path | head -8
    echo
    ls -la "$PEERS_DIR" 2>&1 | head -10
    echo
    wg show wg0 2>&1 | head -20
    log "✅ split WG control plane active. Container writes peer files; host reloads on every change."
}

main() {
    local cmd="${1:-help}"; shift || true
    case "$cmd" in
        init)    cmd_init ;;
        upgrade) cmd_upgrade ;;
        tls)     cmd_tls "$@" ;;
        status)  cmd_status ;;
        backup)  cmd_backup ;;
        logs)    cmd_logs ;;
        init-wg-reloader) cmd_init_wg_reloader ;;
        *)
            cat <<EOF
HobeRadius deploy.sh — أوامر:
  init               أول تثبيت كامل
  upgrade            git pull + إعادة بناء
  tls DOMAIN         إصدار شهادة Let's Encrypt + auto-renew
  status             حالة containers + healthcheck + قرص
  backup             backup يدوي
  logs               متابعة logs الـ app
  init-wg-reloader   تنصيب systemd path-unit يراقب wg0.conf
                     (Phase M — auto-sync بعد كل إضافة peer)
EOF
            ;;
    esac
}

main "$@"
