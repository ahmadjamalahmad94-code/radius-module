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

    log "✅ تم. التالي:"
    echo "   - لو لديك domain: sudo bash deploy/deploy.sh tls YOUR_DOMAIN"
    echo "   - login: http://YOUR_VPS_IP/admin/radius/login  (admin / admin — غيّرها فورًا)"
}

cmd_upgrade() {
    log "1) git pull ..."
    cd "$PROJECT_ROOT" && git pull --rebase
    log "2) build + restart ..."
    $COMPOSE up -d --build
    log "3) status:"
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
    # Phase M — wires the host-side systemd path-unit that watches
    # /etc/wireguard/wg0.conf and runs `wg syncconf wg0 <(wg-quick
    # strip wg0)` whenever the container rewrites it. Idempotent.
    log "1) check WireGuard is installed ..."
    if ! command -v wg >/dev/null || ! command -v wg-quick >/dev/null; then
        die "wireguard-tools not installed on the host. Run: apt install -y wireguard"
    fi

    log "2) install systemd units ..."
    install -m 0644 "$PROJECT_ROOT/deploy/wg-reload.service" /etc/systemd/system/wg-reload.service
    install -m 0644 "$PROJECT_ROOT/deploy/wg-reload.path"    /etc/systemd/system/wg-reload.path

    log "3) reload + enable ..."
    systemctl daemon-reload
    systemctl enable --now wg-reload.path

    log "4) status:"
    systemctl --no-pager status wg-reload.path | head -10
    log "✅ wg-reload watcher is active. Any write to wg0.conf will trigger a syncconf."
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
