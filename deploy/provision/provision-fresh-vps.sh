#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# provision-fresh-vps.sh — idempotent one-shot to stand up a HobeRadius VPS
# identical to production. Runs on a FRESH Ubuntu box. Re-runnable; every step
# is guarded (skips if already done) and aborts-with-reason on failure.
#
# يقرأ vps-manifest.json (من inventory-current-vps.sh) لو مُرّر — فيعيد إنتاج
# تعديلاتك اليدويّة (حدّ رفع nginx، subnet الـ WG، ربط الترخيص…). وإلا يستعمل
# افتراضات الريبو المعقولة.
#
# الاستخدام:
#   sudo bash provision-fresh-vps.sh \
#        --sha <git-sha|origin/main> \
#        --role app|proxy \
#        --manifest /path/vps-manifest.json \
#        [--root /opt/hoberadius] [--nginx-conf FILE] [--nginx-tls FILE]
#
# الأسرار لا تُخبَّأ في الريبو أبدًا — تُكتب في .env (المُتجاهَل بـ gitignore)
# أو تُطلب تفاعليًّا. مُولَّدة عشوائيًّا لو غابت.
# ─────────────────────────────────────────────────────────────────────────────
set -u
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$SELF_DIR/_lib.sh"

# ── defaults ──
GIT_SHA="origin/main"
ROLE="app"
MANIFEST=""
ROOT="/opt/hoberadius"
PROXY_ROOT="/opt/radius-proxy"
NGINX_CONF_OVERRIDE=""
NGINX_TLS_OVERRIDE=""
RM_REMOTE="https://github.com/ahmadjamalahmad94-code/radius-module.git"
PX_REMOTE="https://github.com/ahmadjamalahmad94-code/radius-proxy.git"

while [ $# -gt 0 ]; do
  case "$1" in
    --sha) GIT_SHA="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --root) ROOT="$2"; shift 2 ;;
    --nginx-conf) NGINX_CONF_OVERRIDE="$2"; shift 2 ;;
    --nginx-tls) NGINX_TLS_OVERRIDE="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "arg مجهول: $1" ;;
  esac
done
need_root
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"

# manifest helper wrapper (empty if no manifest)
mget() { [ -n "$MANIFEST" ] && manifest_get "$MANIFEST" "$1" || printf ''; }

log "الدور=$ROLE  SHA=$GIT_SHA  الجذر=$ROOT  المانيفست=${MANIFEST:-<none>}"

# ══ STEP 1: system deps ═══════════════════════════════════════════════════════
step "1) متطلّبات النظام (Docker + أدوات)"
if guard deps; then
  export DEBIAN_FRONTEND=noninteractive
  if ! have docker; then
    apt-get update -y || die "apt update فشل"
    apt-get install -y docker.io docker-compose-plugin || die "تثبيت docker فشل"
    systemctl enable --now docker || warn "تعذّر تفعيل خدمة docker"
  fi
  for p in git openssl wireguard wireguard-tools sqlite3 curl iptables ca-certificates python3; do
    have "${p%%-*}" 2>/dev/null || apt-get install -y "$p" >/dev/null 2>&1 || true
  done
  docker compose version >/dev/null 2>&1 || die "docker compose v2 غير متاح بعد التثبيت"
  guard_done deps; ok "المتطلّبات جاهزة"
else ok "المتطلّبات — مثبّتة سابقًا (تخطّي)"; fi

# ══ STEP 2: clone repos at pinned SHA ═════════════════════════════════════════
step "2) استنساخ الريبو عند SHA مثبَّت"
clone_at() { # remote root sha
  rem="$1"; dst="$2"; sha="$3"
  if [ ! -d "$dst/.git" ]; then
    mkdir -p "$(dirname "$dst")"
    git clone "$rem" "$dst" || die "git clone فشل: $rem"
  fi
  ( cd "$dst" && git fetch --all --quiet && git checkout --quiet "$sha" ) \
    || die "checkout $sha فشل في $dst"
  log "   $dst @ $(cd "$dst" && git rev-parse --short HEAD)"
}
clone_at "$RM_REMOTE" "$ROOT" "$GIT_SHA"
if [ "$ROLE" = "proxy" ]; then clone_at "$PX_REMOTE" "$PROXY_ROOT" "$GIT_SHA"; fi
ok "الريبو جاهز"

# ══ STEP 3: .env (secrets prompted/generated, never committed) ════════════════
step "3) توليد .env (الأسرار)"
ENV_FILE="$ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then cp "$ROOT/deploy/.env.example" "$ENV_FILE"; fi
# generate the two hard secrets if empty
if ! grep -qE '^FLASK_SECRET=..' "$ENV_FILE" || grep -qE '^FLASK_SECRET=change-me' "$ENV_FILE"; then
  sed -i "s|^FLASK_SECRET=.*|FLASK_SECRET=$(openssl rand -hex 32)|" "$ENV_FILE"
fi
if [ -z "$(grep -E '^HOBERADIUS_INTERNAL_SECRET=' "$ENV_FILE" | cut -d= -f2-)" ]; then
  sed -i "s|^HOBERADIUS_INTERNAL_SECRET=.*|HOBERADIUS_INTERNAL_SECRET=$(openssl rand -hex 32)|" "$ENV_FILE"
fi
# carry non-secret WG/public-host settings from the manifest (owner's captured values)
for pair in \
    "HOBERADIUS_WG_SUBNET=wg.subnet" \
    "HOBERADIUS_PUBLIC_HOST=host.public_ip" ; do
  ekey="${pair%%=*}"; mkey="${pair#*=}"; mval="$(mget "env.keys.$ekey")"; [ -z "$mval" ] && mval="$(mget "$mkey")"
  [ -n "$mval" ] && set_env "$ENV_FILE" "$ekey" "$mval"
done
chmod 600 "$ENV_FILE"
ok ".env جاهز (الأسرار مُولَّدة/محفوظة محليًّا فقط)"

# ══ STEP 4: host dirs the compose bind-mounts expect ══════════════════════════
step "4) تجهيز المجلّدات على المضيف"
mkdir -p "$ROOT/instance" "$ROOT/backups" "$ROOT/logs" "$ROOT/logs/freeradius"
chmod 700 "$ROOT/instance"
for d in /etc/hoberadius/nginx-streams.d /etc/hoberadius/wg-peers.d /etc/hoberadius/nginx-tls; do
  mkdir -p "$d"; chgrp 999 "$d" 2>/dev/null || true; chmod 2775 "$d"
done
ok "المجلّدات جاهزة"

# ══ STEP 5: re-apply nginx drift (upload limit + TLS) ═════════════════════════
step "5) إعادة تطبيق تعديلات nginx (حدّ الرفع + TLS)"
# repo defaults already include client_max_body_size 1024m on /admin/radius/migrate/.
# If the owner captured a customised nginx.conf / TLS conf, drop it in verbatim.
[ -n "$NGINX_CONF_OVERRIDE" ] && { cp "$NGINX_CONF_OVERRIDE" "$ROOT/deploy/nginx.conf"; ok "طُبِّق nginx.conf المُخصَّص"; }
[ -n "$NGINX_TLS_OVERRIDE" ]  && { cp "$NGINX_TLS_OVERRIDE"  "$ROOT/deploy/nginx-tls-8443.conf"; ok "طُبِّق nginx-tls-8443.conf المُخصَّص"; }
cmbs="$(mget nginx.client_max_body_size)"
[ -n "$cmbs" ] && log "   حدّ الرفع المُلتقَط من المانيفست: $cmbs (تأكّد أنه مطبَّق في deploy/nginx.conf)"
if [ "$(mget nginx.local_git_edits)" = "yes" ] && [ -z "$NGINX_CONF_OVERRIDE" ]; then
  warn "المانيفست يقول إنّ لديك تعديلات nginx يدويّة على الـ VPS القديم — مرّرها بـ --nginx-conf/--nginx-tls، وإلا سيستعمل الجديد افتراضات الريبو."
fi

# ══ STEP 6: WireGuard mgmt tunnel wg0 ═════════════════════════════════════════
step "6) نفق الإدارة WireGuard (wg0)"
WG_SUBNET="$(mget env.keys.HOBERADIUS_WG_SUBNET)"; [ -z "$WG_SUBNET" ] && WG_SUBNET="$(mget wg.subnet)"; [ -z "$WG_SUBNET" ] && WG_SUBNET="10.10.0.0/24"
WG_SRV_IP="${WG_SUBNET%/*}"; WG_SRV_IP="${WG_SRV_IP%.*}.1"   # x.x.x.1
WG_PORT="$(mget wg.listen_port)"; [ -z "$WG_PORT" ] && WG_PORT="51820"
if guard wg0; then
  if [ ! -f /etc/wireguard/wg0.conf ]; then
    umask 077
    priv="$(wg genkey)"; pub="$(printf '%s' "$priv" | wg pubkey)"
    printf '%s' "$priv" > /etc/wireguard/server_private.key
    printf '%s' "$pub"  > /etc/wireguard/server_public.key
    cat > /etc/wireguard/wg0.conf <<EOF
# HobeRadius mgmt tunnel — generated by provision-fresh-vps.sh
# Peers are (re)provisioned per-router by the panel (Setup Wizard) — start empty.
[Interface]
Address = ${WG_SRV_IP}/${WG_SUBNET#*/}
ListenPort = ${WG_PORT}
PrivateKey = ${priv}
SaveConfig = false
EOF
    chmod 600 /etc/wireguard/wg0.conf
    log "   wg0 pubkey (ضعه في .env HOBERADIUS_WG_SERVER_PUBKEY وفي الراوترات): $pub"
    set_env "$ENV_FILE" "HOBERADIUS_WG_SERVER_PUBKEY" "$pub"
  fi
  systemctl enable --now "wg-quick@wg0" 2>/dev/null || { wg-quick up wg0 2>/dev/null || warn "تعذّر رفع wg0 (راجع /etc/wireguard/wg0.conf)"; }
  guard_done wg0
fi
ip link show wg0 >/dev/null 2>&1 && ok "wg0 يعمل (${WG_SRV_IP})" || warn "wg0 غير نشط بعد"

# ══ STEP 7: build + up the docker stack (DB + migrations auto on boot) ════════
step "7) بناء وتشغيل الحاويات (DB + migrations تلقائيًّا عند الإقلاع)"
# --no-cache: تجاوز طبقة COPY المخبّأة التي كانت تُبقي الكود قديمًا (gotcha معروف).
( cd "$ROOT" && $COMPOSE build --no-cache ) || die "docker build فشل"
( cd "$ROOT" && $COMPOSE up -d ) || die "docker up فشل"
log "   انتظار صحّة اللوحة ..."
for i in $(seq 1 40); do
  curl -fsS --max-time 3 http://127.0.0.1/admin/radius/_health >/dev/null 2>&1 && { ok "اللوحة تستجيب"; break; }
  sleep 3
done

# ══ STEP 8: accel-ppp (assel / SSTP :443 + PPTP :1723 mgmt link) ══════════════
step "8) accel-ppp — نفق الإدارة SSTP (:443) + PPTP (:1723)"
ACCEL_INSTALLER="$ROOT/deploy/accel-ppp/install-accel-selfsigned.sh"
if [ -f "$ACCEL_INSTALLER" ]; then
  if guard accel; then
    HOBERADIUS_REPO="$ROOT" bash "$ACCEL_INSTALLER" && guard_done accel || warn "مثبّت accel أرجع خطأ — راجع سجلّه أعلاه"
  else ok "accel مثبّت سابقًا (تخطّي)"; fi
else warn "مثبّت accel غير موجود في $ACCEL_INSTALLER"; fi
# فعّل accel-ppp وشغّله واجعله يبدأ مع الإقلاع (نفق الإدارة جزء أساسيّ من التشغيل).
if have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^accel-ppp'; then
  systemctl enable accel-ppp >/dev/null 2>&1 || warn "تعذّر enable لـ accel-ppp"
  systemctl restart accel-ppp 2>/dev/null || systemctl start accel-ppp 2>/dev/null || warn "تعذّر تشغيل accel-ppp"
  sleep 2
  a="$(systemctl is-active accel-ppp 2>/dev/null)"; e="$(systemctl is-enabled accel-ppp 2>/dev/null)"
  [ "$a" = "active" ] && ok "accel-ppp يعمل" || warn "accel-ppp ليس active (=$a) — راجع journalctl -u accel-ppp"
  [ "$e" = "enabled" ] && ok "accel-ppp يبدأ مع الإقلاع" || warn "accel-ppp ليس enabled (=$e)"
  # تأكيد أنّ :443 صار مملوكًا لـ accel-pppd (لا nginx/غيره).
  if have ss; then
    o443="$(ss -lntupH 2>/dev/null | grep -E '[^[:space:]]:443[[:space:]]' | sed -nE 's/.*users:\(\("([^"]+)".*/\1/p' | head -1)"
    [ "$o443" = "accel-pppd" ] && ok ":443 مملوك لـ accel-pppd" || warn ":443 مالكه='${o443:-غير معروف/يحتاج sudo}' (متوقَّع accel-pppd)"
  fi
else
  warn "وحدة systemd 'accel-ppp' غير موجودة — تأكّد أنّ المثبّت أنشأها."
fi

# ══ STEP 9: mgmt-confinement firewall ═════════════════════════════════════════
step "9) جدار حماية mgmt-confinement"
MGMT_INSTALLER="$ROOT/deploy/mgmt-confinement/install-mgmt-confinement.sh"
if [ -f "$MGMT_INSTALLER" ]; then
  if guard mgmt; then bash "$MGMT_INSTALLER" && guard_done mgmt || warn "مثبّت mgmt-confinement أرجع خطأ"; else ok "mgmt-confinement مطبَّق سابقًا"; fi
else warn "مثبّت mgmt-confinement غير موجود"; fi

# ══ STEP 10: licensing linkage ════════════════════════════════════════════════
step "10) ربط الترخيص (لوحة المزوّد)"
BRIDGE_URL="$(mget licensing.bridge_base_url)"
if [ -n "$BRIDGE_URL" ]; then
  log "   عنوان جسر الترخيص المُلتقَط: $BRIDGE_URL"
fi
cat <<EOF
   ▸ أكمِل التفعيل من اللوحة (مفتاح الترخيص سرّ لكل عميل — لا يُخبَّأ في السكربت):
     افتح: http://<VPS_IP>/admin/radius/ ← الترخيص/الجسر
     أدخِل base_url=${BRIDGE_URL:-<licensing-url>} + مفتاح الترخيص + فعّل الجسر.
     اللوحة المركزيّة هي مَن يحدّد الخدمات المفعّلة لهذا العميل.
EOF

# ══ STEP 11: self-verify ══════════════════════════════════════════════════════
step "11) التحقّق الذاتي"
if [ -f "$SELF_DIR/verify-parity.sh" ]; then
  bash "$SELF_DIR/verify-parity.sh" ${MANIFEST:+--manifest "$MANIFEST"} --root "$ROOT" || warn "verify-parity وجد فروقًا — راجع أعلاه"
elif [ -f "$ROOT/deploy/fresh-install-check.sh" ]; then
  bash "$ROOT/deploy/fresh-install-check.sh" || true
fi

step "تم"
ok "التزويد اكتمل. الخطوات المتبقّية اليدويّة: (1) أكمِل ربط الترخيص من اللوحة،"
echo "   (2) غيّر كلمة مرور admin الافتراضيّة، (3) شغّل verify-parity.sh للتأكيد."
