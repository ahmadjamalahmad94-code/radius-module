#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# inventory-current-vps.sh — READ-ONLY VPS discovery + manual-drift finder.
#
# الغرض (بالعربي للمالك): شغّل هذا على الـ VPS الحيّ الحالي. لا يغيّر أي شيء
# إطلاقًا (قراءة فقط). يكتشف كل ما هو مثبَّت ومضبوط، ويقارنه بافتراضات الريبو،
# ثم يخرج ملفّين:
#   • vps-manifest.json     — بصمة كاملة للنظام (يقرأها provision + verify).
#   • vps-drift-report.txt  — «تقرير التعديلات اليدوية»: كل ما يختلف عن الريبو
#                              (أي التغييرات اليدوية التي عملتها بمرور الوقت).
#
# SELF-CONTAINED: انسخ هذا الملف وحده (scp) للـ VPS الحالي وشغّله. لا يحتاج
# بقيّة مجلّد provision/. يعمل على Ubuntu + Docker القياسي (bash/docker/coreutils
# + python3 stdlib). لا pip، لا npm، لا تنزيل حزم.
#
# الاستخدام:
#   sudo bash inventory-current-vps.sh                 # يكتب في ./ (المجلّد الحالي)
#   sudo bash inventory-current-vps.sh -o /root/out    # مجلّد إخراج مخصّص
#   HR_ROOT=/opt/hoberadius sudo -E bash inventory-current-vps.sh
#
# السرّية: قيم الأسرار (secrets/keys/private-keys) لا تُطبع أبدًا خام — يُعرَض
# اسم المفتاح + هل هو مضبوط + بصمة sha256 مختصرة فقط.
# ─────────────────────────────────────────────────────────────────────────────
set -u
umask 077   # الملفّات الناتجة قد تحوي أسماء مفاتيح/بصمات — لا تجعلها عالميّة القراءة.

# ── args ──
OUT_DIR="."
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--out) OUT_DIR="${2:-.}"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "arg مجهول: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUT_DIR" 2>/dev/null || { echo "تعذّر إنشاء $OUT_DIR" >&2; exit 1; }
MANIFEST="$OUT_DIR/vps-manifest.json"
DRIFT="$OUT_DIR/vps-drift-report.txt"

# ── repo root discovery (default /opt/hoberadius) ──
HR_ROOT="${HR_ROOT:-}"
if [ -z "$HR_ROOT" ]; then
  for c in /opt/hoberadius /opt/radius-module /root/hoberadius "$HOME/hoberadius"; do
    [ -f "$c/deploy/docker-compose.yml" ] && { HR_ROOT="$c"; break; }
  done
fi
[ -z "$HR_ROOT" ] && HR_ROOT="/opt/hoberadius"
PROXY_ROOT="${PROXY_ROOT:-/opt/radius-proxy}"

# ── temp facts store (KEY<TAB>base64(value)); python turns it into nested JSON ──
FACTS="$(mktemp)"; DRIFT_TMP="$(mktemp)"
trap 'rm -f "$FACTS" "$DRIFT_TMP"' EXIT

have() { command -v "$1" >/dev/null 2>&1; }
b64()  { printf '%s' "$1" | base64 | tr -d '\n'; }
# add KEY VALUE  → nested by dots (host.os.kernel …). Value stored base64.
add()  { printf '%s\t%s\n' "$1" "$(b64 "${2-}")" >> "$FACTS"; }
# addr KEY  (raw multiline from stdin)
addr() { printf '%s\t%s\n' "$1" "$(base64 | tr -d '\n')" >> "$FACTS"; }
# sha of a value (secret fingerprint, never the value itself)
sha()  { printf '%s' "${1-}" | sha256sum 2>/dev/null | cut -c1-16; }
drift(){ printf '%s\n' "$*" >> "$DRIFT_TMP"; }
dsec() { printf '\n=== %s ===\n' "$*" >> "$DRIFT_TMP"; }

SECRETY='SECRET|PASS|PASSWORD|TOKEN|KEY|PRIVATE|PSK'   # regex for secret-ish env keys

echo "[inventory] repo root = $HR_ROOT  (اضبط HR_ROOT لو مختلف)"
echo "[inventory] جارٍ الاكتشاف — قراءة فقط، لا تغييرات ..."

# ══════════════════════════════════════════════════════════════════════════════
# 1) HOST — os / kernel / packages / docker
# ══════════════════════════════════════════════════════════════════════════════
add meta.tool "inventory-current-vps.sh"
add meta.generated_utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
add meta.hostname "$(hostname 2>/dev/null)"
add meta.repo_root "$HR_ROOT"
add host.kernel "$(uname -r 2>/dev/null)"
add host.arch "$(uname -m 2>/dev/null)"
if [ -r /etc/os-release ]; then . /etc/os-release 2>/dev/null || true; add host.os "${PRETTY_NAME:-unknown}"; fi
add host.docker_version "$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
add host.compose_version "$(docker compose version --short 2>/dev/null || docker-compose version --short 2>/dev/null)"
add host.public_ip "$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || echo '')"
# relevant packages
PKGS="docker.io docker-ce wireguard wireguard-tools accel-ppp nginx certbot sqlite3 openssl iptables python3"
if have dpkg-query; then
  for p in $PKGS; do
    v="$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null || true)"
    [ -n "$v" ] && add "host.packages.$p" "$v"
  done
fi

# ══════════════════════════════════════════════════════════════════════════════
# 2) CONTAINERS — running set + image ids + created
# ══════════════════════════════════════════════════════════════════════════════
if have docker; then
  # newline-joined "name|image|imageid|created|status|ports"
  CJSON="$(docker ps -a --format '{{.Names}}|{{.Image}}|{{.ID}}|{{.CreatedAt}}|{{.Status}}|{{.Ports}}' 2>/dev/null)"
  addr containers.raw <<< "$CJSON"
  for want in hoberadius hoberadius-freeradius hoberadius-nginx hoberadius-backup; do
    line="$(printf '%s\n' "$CJSON" | awk -F'|' -v n="$want" '$1==n{print;exit}')"
    if [ -n "$line" ]; then
      add "containers.$want.image"    "$(printf '%s' "$line" | cut -d'|' -f2)"
      add "containers.$want.image_id" "$(printf '%s' "$line" | cut -d'|' -f3)"
      add "containers.$want.created"  "$(printf '%s' "$line" | cut -d'|' -f4)"
      add "containers.$want.status"   "$(printf '%s' "$line" | cut -d'|' -f5)"
    else
      add "containers.$want.status" "ABSENT"
      drift "[containers] الحاوية '$want' غير موجودة على هذا الـ VPS."
    fi
  done
fi

# ══════════════════════════════════════════════════════════════════════════════
# 3) COMPOSE effective config + .env keys (REDACTED)
# ══════════════════════════════════════════════════════════════════════════════
COMPOSE_FILE="$HR_ROOT/deploy/docker-compose.yml"
if [ -f "$COMPOSE_FILE" ] && have docker; then
  eff="$(cd "$HR_ROOT" && docker compose -f deploy/docker-compose.yml config 2>/dev/null || true)"
  # redact any obvious secret-looking values in the effective config dump
  eff_red="$(printf '%s\n' "$eff" | sed -E 's/(SECRET|TOKEN|PASSWORD|KEY|PSK)([A-Z_]*): .*/\1\2: <redacted>/I')"
  addr compose.effective_config_redacted <<< "$eff_red"
fi
ENV_FILE="$HR_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
  add env.present "yes"
  # per key: name -> {set, sha} ; secret values never printed
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    [ "$k" = "$line" ] && continue
    if printf '%s' "$k" | grep -qiE "$SECRETY"; then
      if [ -n "$v" ]; then add "env.keys.$k" "SET sha256:$(sha "$v")"; else add "env.keys.$k" "empty"; fi
    else
      add "env.keys.$k" "$v"   # non-secret value shown verbatim
    fi
  done < "$ENV_FILE"
else
  add env.present "no"
  drift "[env] لا يوجد ملف .env في $HR_ROOT — سيُنشأ عند التزويد."
fi

# ── generic file-vs-repo drift helper ──
# diff_file  LIVE_PATH  REPO_PATH  LABEL
diff_file() {
  live="$1"; repo="$2"; label="$3"
  if [ ! -f "$repo" ]; then return; fi
  if [ ! -f "$live" ]; then drift "[$label] الملف الحيّ غير موجود: $live"; return; fi
  d="$(diff -u "$repo" "$live" 2>/dev/null)"
  if [ -n "$d" ]; then
    dsec "$label — تعديلات يدويّة على: $live (مقابل الريبو $repo)"
    printf '%s\n' "$d" | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' >> "$DRIFT_TMP"
    add "drift.$label.changed" "yes"
  else
    add "drift.$label.changed" "no"
  fi
}

# ══════════════════════════════════════════════════════════════════════════════
# 4) NGINX — live host confs + upload limit + TLS 8443, DIFF vs repo
# ══════════════════════════════════════════════════════════════════════════════
NG_REPO="$HR_ROOT/deploy/nginx.conf"
NG_TLS_REPO="$HR_ROOT/deploy/nginx-tls-8443.conf"
# The live conf mounted read-only is the same host file (bind mount), but capture
# the *container's* effective view too in case of manual in-container edits.
if have docker && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx hoberadius-nginx; then
  live_default="$(docker exec hoberadius-nginx sh -c 'cat /etc/nginx/conf.d/default.conf 2>/dev/null' 2>/dev/null || true)"
  if [ -n "$live_default" ]; then
    cmbs="$(printf '%s\n' "$live_default" | grep -iE 'client_max_body_size' | head -3 | tr -s ' ' | sed 's/^ //')"
    add nginx.client_max_body_size "$cmbs"
    tmp_live="$(mktemp)"; printf '%s\n' "$live_default" > "$tmp_live"
    diff_file "$tmp_live" "$NG_REPO" "nginx"
    rm -f "$tmp_live"
    # in-container 8443?
    live_8443="$(docker exec hoberadius-nginx sh -c 'cat /etc/nginx/conf.d/8443-ssl.conf 2>/dev/null || cat /etc/nginx/conf.d/*8443* 2>/dev/null' 2>/dev/null || true)"
    [ -n "$live_8443" ] && add nginx.tls_8443_active "yes" || add nginx.tls_8443_active "no"
  fi
else
  # fall back to the on-disk host files
  [ -f "$NG_REPO" ] && add nginx.client_max_body_size "$(grep -iE 'client_max_body_size' "$NG_REPO" | tr -s ' ' | sed 's/^ //' | paste -sd';' -)"
fi
# also diff the host-side nginx files if the owner edited them locally (git drift)
if [ -d "$HR_ROOT/.git" ]; then
  gd="$(cd "$HR_ROOT" && git diff --name-only -- deploy/nginx.conf deploy/nginx-tls-8443.conf deploy/nginx-main.conf 2>/dev/null)"
  if [ -n "$gd" ]; then
    dsec "nginx — ملفّات nginx معدّلة محليًّا (git، غير ملتزمة) — هذه تُوقف git pull"
    printf '%s\n' "$gd" >> "$DRIFT_TMP"
    (cd "$HR_ROOT" && git diff -- deploy/nginx.conf deploy/nginx-tls-8443.conf deploy/nginx-main.conf 2>/dev/null | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)') >> "$DRIFT_TMP"
    add nginx.local_git_edits "yes"
  else
    add nginx.local_git_edits "no"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# 5) ACCEL-PPP — /etc/accel-ppp.conf, :443 owner, /dev/ppp, modules
# ══════════════════════════════════════════════════════════════════════════════
ACCEL_CONF="/etc/accel-ppp.conf"
if [ -f "$ACCEL_CONF" ]; then
  add accel.conf_present "yes"
  add accel.sstp_port "$(grep -iE '^\s*(port|ssl-port|bind)' "$ACCEL_CONF" | grep -oE '[0-9]{2,5}' | head -1)"
  add accel.has_radius_section "$(grep -qiE '^\[radius\]' "$ACCEL_CONF" && echo yes || echo no)"
  add accel.has_sstp_section "$(grep -qiE '^\[sstp\]' "$ACCEL_CONF" && echo yes || echo no)"
  # redact any secret=/password= lines in the captured copy
  addr accel.conf_redacted < <(sed -E 's/^(\s*(secret|password|chap-secret)\s*=).*/\1 <redacted>/I' "$ACCEL_CONF")
  # duplicate-section sanity (a known past failure)
  dupes="$(grep -oE '^\[[a-z-]+\]' "$ACCEL_CONF" | sort | uniq -d | tr '\n' ' ')"
  [ -n "$dupes" ] && drift "[accel] أقسام مكرّرة في accel-ppp.conf: $dupes (خطأ سابق معروف)."
else
  add accel.conf_present "no"
  drift "[accel] لا يوجد /etc/accel-ppp.conf — نفق SSTP للإدارة غير مثبَّت."
fi
add accel.dev_ppp "$( [ -c /dev/ppp ] && echo present || echo MISSING )"
add accel.ppp_modules "$(lsmod 2>/dev/null | grep -E '^(ppp_generic|ppp_async|pppox|sstp)' | awk '{print $1}' | paste -sd',' -)"
add accel.service_active "$(systemctl is-active accel-ppp 2>/dev/null || echo unknown)"
if have ss; then
  own443="$(ss -ltnpH 'sport = :443' 2>/dev/null | grep -oE 'users:\(\("[^"]+"' | head -1 | sed 's/.*"//')"
  add ports.443_owner "${own443:-none}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# 6) WIREGUARD — wg0 subnet + peers (keys redacted)
# ══════════════════════════════════════════════════════════════════════════════
if have wg; then
  add wg.wg0_up "$(ip link show wg0 >/dev/null 2>&1 && echo yes || echo no)"
  add wg.server_ip "$(ip -o -4 addr show wg0 2>/dev/null | awk '{print $4}' | head -1)"
  add wg.peer_count "$(wg show wg0 peers 2>/dev/null | wc -l | tr -d ' ')"
  add wg.listen_port "$(wg show wg0 listen-port 2>/dev/null)"
  # public key only (private key NEVER captured)
  add wg.server_pubkey "$(wg show wg0 public-key 2>/dev/null)"
fi
if [ -f /etc/wireguard/wg0.conf ]; then
  add wg.conf_present "yes"
  addr wg.conf_redacted < <(sed -E 's/^(\s*(PrivateKey|PresharedKey)\s*=).*/\1 <redacted>/I' /etc/wireguard/wg0.conf)
  add wg.subnet "$(grep -iE '^\s*Address' /etc/wireguard/wg0.conf | grep -oE '[0-9.]+/[0-9]+' | head -1)"
else
  add wg.conf_present "no"
fi

# ══════════════════════════════════════════════════════════════════════════════
# 7) FREERADIUS — mods/sites/clients, $INCLUDE wizard dir, DIFF vs repo
# ══════════════════════════════════════════════════════════════════════════════
FR_REPO="$HR_ROOT/deploy/freeradius"
if have docker && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx hoberadius-freeradius; then
  add freeradius.mods_enabled "$(docker exec hoberadius-freeradius sh -c 'ls /etc/freeradius/3.0/mods-enabled 2>/dev/null || ls /etc/raddb/mods-enabled 2>/dev/null' 2>/dev/null | paste -sd',' -)"
  add freeradius.has_sql "$(docker exec hoberadius-freeradius sh -c 'test -e /etc/freeradius/3.0/mods-enabled/sql -o -e /etc/raddb/mods-enabled/sql && echo yes || echo no' 2>/dev/null)"
  add freeradius.has_mschap "$(docker exec hoberadius-freeradius sh -c 'test -e /etc/freeradius/3.0/mods-enabled/mschap -o -e /etc/raddb/mods-enabled/mschap && echo yes || echo no' 2>/dev/null)"
  # wizard client include dir (host side under instance/)
  WZ="$HR_ROOT/instance/freeradius-clients-wizard"
  add freeradius.wizard_client_count "$( ls "$WZ"/*.conf 2>/dev/null | wc -l | tr -d ' ' )"
fi
diff_file "$HR_ROOT/deploy/freeradius/clients.conf" "$FR_REPO/clients.conf" "freeradius_clients"

# ══════════════════════════════════════════════════════════════════════════════
# 8) FIREWALL / systemd / cron / ip addr
# ══════════════════════════════════════════════════════════════════════════════
if have iptables; then
  addr iptables.filter_rules < <(iptables -S 2>/dev/null)
  add firewall.mgmt_confine_chains "$(iptables -S 2>/dev/null | grep -oE 'HR[-_A-Z]*|MGMT[-_A-Z]*|hoberadius[-_a-z]*' | sort -u | paste -sd',' -)"
fi
add systemd.hobe_units "$(systemctl list-unit-files 2>/dev/null | grep -iE 'hobe|wg-reload|accel' | awk '{print $1}' | paste -sd',' -)"
add cron.root "$(crontab -l 2>/dev/null | grep -vE '^\s*#' | wc -l | tr -d ' ') entries"
add cron.d_files "$(ls /etc/cron.d 2>/dev/null | paste -sd',' -)"
# host manual ip addr add (e.g. 10.x/32 on lo)
add net.lo_extra_addrs "$(ip -o -4 addr show lo 2>/dev/null | awk '{print $4}' | grep -vE '^127\.' | paste -sd',' -)"

# ══════════════════════════════════════════════════════════════════════════════
# 9) DATABASE — path, migration version, tables + row COUNTS (structure only)
# ══════════════════════════════════════════════════════════════════════════════
DB_HOST="$HR_ROOT/instance/hoberadius.db"
add db.path "$DB_HOST"
# choose a sqlite3: host binary, else the backup container (has sqlite), else app container
SQ=""; SQMODE=""
if have sqlite3 && [ -f "$DB_HOST" ]; then SQ="host"; fi
if [ -z "$SQ" ] && have docker && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx hoberadius-backup; then SQ="backup"; fi
run_sql() { # $1 = SQL (read-only). Pass SQL as a positional param so quotes
            # inside it (e.g. "table") never get re-parsed by the container shell.
  case "$SQ" in
    host)   sqlite3 -readonly "$DB_HOST" "$1" 2>/dev/null ;;
    backup) docker exec hoberadius-backup sh -c 'sqlite3 -readonly /data/hoberadius.db "$1"' _ "$1" 2>/dev/null ;;
    *) return 1 ;;
  esac
}
if [ -n "$SQ" ]; then
  add db.access_via "$SQ"
  add db.migration_version "$(run_sql "SELECT name FROM _migrations ORDER BY name DESC LIMIT 1;")"
  add db.migration_count "$(run_sql "SELECT COUNT(*) FROM _migrations;")"
  # table -> rowcount (structure + counts only, NO data)
  tbls="$(run_sql "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")"
  add db.table_count "$(printf '%s\n' "$tbls" | grep -c . )"
  for t in $tbls; do
    c="$(run_sql "SELECT COUNT(*) FROM \"$t\";")"
    add "db.rows.$t" "${c:-?}"
  done
else
  drift "[db] تعذّر الوصول لـ sqlite (لا sqlite3 على المضيف ولا حاوية backup) — عدّاد الجداول غير متاح."
fi

# ══════════════════════════════════════════════════════════════════════════════
# 10) LICENSING linkage (tenant_settings, license_key REDACTED)
# ══════════════════════════════════════════════════════════════════════════════
if [ -n "$SQ" ]; then
  bu="$(run_sql "SELECT value FROM tenant_settings WHERE key='license_admin_bridge.base_url' LIMIT 1;")"
  en="$(run_sql "SELECT value FROM tenant_settings WHERE key='license_admin_bridge.enabled' LIMIT 1;")"
  lk="$(run_sql "SELECT value FROM tenant_settings WHERE key='license_admin_bridge.license_key' LIMIT 1;")"
  add licensing.bridge_base_url "${bu:-}"
  add licensing.bridge_enabled  "${en:-}"
  if [ -n "$lk" ]; then add licensing.license_key "SET sha256:$(sha "$lk")"; else add licensing.license_key "unset"; fi
  # registered/enabled services (structure only)
  add licensing.enabled_service_rows "$(run_sql "SELECT COUNT(*) FROM services WHERE COALESCE(enabled,0)=1;" 2>/dev/null || echo '?')"
fi

# ══════════════════════════════════════════════════════════════════════════════
# 11) GIT HEADs of the repos checked out on the VPS
# ══════════════════════════════════════════════════════════════════════════════
[ -d "$HR_ROOT/.git" ]    && { add git.radius_module_head "$(cd "$HR_ROOT" && git rev-parse HEAD 2>/dev/null)"; add git.radius_module_dirty "$(cd "$HR_ROOT" && [ -n "$(git status --porcelain 2>/dev/null)" ] && echo yes || echo no)"; }
[ -d "$PROXY_ROOT/.git" ] && add git.radius_proxy_head "$(cd "$PROXY_ROOT" && git rev-parse HEAD 2>/dev/null)"

# ══════════════════════════════════════════════════════════════════════════════
# EMIT — nested JSON manifest via python3 stdlib (present on stock Ubuntu)
# ══════════════════════════════════════════════════════════════════════════════
if have python3; then
  FACTS="$FACTS" python3 - "$MANIFEST" <<'PY'
import os, sys, json, base64
facts = os.environ["FACTS"]
root = {}
with open(facts, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line or "\t" not in line:
            continue
        key, b = line.split("\t", 1)
        try:
            val = base64.b64decode(b).decode("utf-8", "replace")
        except Exception:
            val = ""
        node = root
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):  # collision guard
                break
        if isinstance(node, dict):
            node[parts[-1]] = val
with open(sys.argv[1], "w", encoding="utf-8") as out:
    json.dump(root, out, ensure_ascii=False, indent=2, sort_keys=True)
print("[inventory] manifest written:", sys.argv[1])
PY
else
  echo "[inventory] python3 غير موجود — إخراج المانيفست كنص خام (key=value)." >&2
  sort "$FACTS" | while IFS="$(printf '\t')" read -r k b; do printf '%s=%s\n' "$k" "$(printf '%s' "$b" | base64 -d 2>/dev/null)"; done > "${MANIFEST%.json}.kv.txt"
  MANIFEST="${MANIFEST%.json}.kv.txt"
fi

# ══════════════════════════════════════════════════════════════════════════════
# DRIFT REPORT (human)
# ══════════════════════════════════════════════════════════════════════════════
{
  echo "════════════════════════════════════════════════════════════════"
  echo " HobeRadius — تقرير التعديلات اليدويّة (MANUAL DRIFT REPORT)"
  echo " VPS: $(hostname 2>/dev/null)   |   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo " repo root: $HR_ROOT"
  echo "════════════════════════════════════════════════════════════════"
  echo
  echo "هذا كل ما يختلف عن افتراضات الريبو — أي التغييرات اليدويّة التي عملتها"
  echo "بمرور الوقت. أعِد تطبيقها على الـ VPS الجديد (provision يقرأها آليًّا)."
  echo
  if [ -s "$DRIFT_TMP" ]; then
    cat "$DRIFT_TMP"
  else
    echo "(لا انحرافات مكتشَفة — النظام مطابق لافتراضات الريبو، أو تعذّر بعض الفحص.)"
  fi
  echo
  echo "── ملخّص سريع (من المانيفست) ──"
  echo "• حدّ رفع nginx (client_max_body_size): راجع nginx.client_max_body_size في المانيفست."
  echo "• نفق SSTP :443 مالكه: راجع ports.443_owner."
  echo "• إصدار الهجرات (schema): راجع db.migration_version."
  echo "• ربط الترخيص: راجع licensing.* (المفتاح مُخفى)."
  echo "• git HEAD: راجع git.radius_module_head."
} > "$DRIFT"

echo "[inventory] drift report written: $DRIFT"
echo "[inventory] ✅ تمّ. راجع:"
echo "    $MANIFEST"
echo "    $DRIFT   ← اقرأ هذا لتشوف كل تعديلاتك اليدويّة"
