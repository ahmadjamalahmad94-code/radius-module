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

# ── truthful-probe infrastructure (المبدأ الحاكم) ─────────────────────────────
# لا نكتب "none"/فارغ أبدًا لفحص فشل. نميّز صراحةً بين ثلاث حالات:
#   • قيمة حقيقيّة            → تُكتب كما هي.
#   • غياب حقيقيّ مؤكَّد       → "absent" (نعرف يقينًا أنّه غير موجود).
#   • تعذّر الاكتشاف           → "UNKNOWN (probe failed: <السبب>)" + يُسجَّل ليُنبَّه
#     (أمر مفقود / يحتاج sudo / فشل التحليل) عليه في النهاية.
UNK_FILE="$(mktemp)"
mark_unknown() { printf '%s\t%s\n' "$1" "$2" >> "$UNK_FILE"; }   # key  reason
# add_unknown KEY REASON   — يكتب حقلًا مجهولًا صريحًا ويُسجّله.
add_unknown() { add "$1" "UNKNOWN (probe failed: $2)"; mark_unknown "$1" "$2"; }
# add_val KEY REASON_IF_EMPTY VALUE  — قيمة، وإلّا UNKNOWN بالسبب (لا "none").
add_val() { if [ -n "${3-}" ]; then add "$1" "$3"; else add_unknown "$1" "$2"; fi; }
# add_absent KEY  — غياب مؤكَّد (لا يُعدّ مجهولًا).
add_absent() { add "$1" "absent"; }
# require_cmd CMD KEY  — لو الأمر مفقود، يكتب UNKNOWN ويرجع 1 (فيُتخطّى الفحص).
require_cmd() { if have "$1"; then return 0; else add_unknown "$2" "الأمر '$1' غير مثبَّت"; return 1; fi; }
# هل نعمل بصلاحية root؟ بعض الفحوصات (users:() في ss، iptables) تحتاجها.
IS_ROOT=0; [ "$(id -u)" -eq 0 ] && IS_ROOT=1
[ "$IS_ROOT" -eq 1 ] || echo "[inventory] تحذير: لا تعمل بـ sudo — بعض الفحوصات (مالك المنافذ، iptables) قد ترجع UNKNOWN." >&2
trap 'rm -f "$FACTS" "$DRIFT_TMP" "$UNK_FILE"' EXIT

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
if have docker; then
  add_val host.docker_version  "docker --version لم يُرجِع إصدارًا" "$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
  add_val host.compose_version "docker compose غير متاح" "$(docker compose version --short 2>/dev/null || docker-compose version --short 2>/dev/null)"
else
  add_unknown host.docker_version "الأمر 'docker' غير مثبَّت"
fi
if have curl; then
  add_val host.public_ip "خدمة IP الخارجيّة لم تُجب (بلا إنترنت خارج؟)" "$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null)"
else
  add_unknown host.public_ip "الأمر 'curl' غير مثبَّت"
fi
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
      add "containers.$want.status" "absent (لا حاوية بهذا الاسم)"
      drift "[containers] الحاوية '$want' غير موجودة على هذا الـ VPS."
    fi
  done
else
  for want in hoberadius hoberadius-freeradius hoberadius-nginx hoberadius-backup; do
    add_unknown "containers.$want.status" "الأمر 'docker' غير مثبَّت"
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
    cmbs="$(printf '%s\n' "$live_default" | grep -iE 'client_max_body_size' | head -3 | tr -s ' ' | sed 's/^ //' | paste -sd'; ' -)"
    add_val nginx.client_max_body_size "لا سطر client_max_body_size في conf الحيّ" "$cmbs"
    tmp_live="$(mktemp)"; printf '%s\n' "$live_default" > "$tmp_live"
    diff_file "$tmp_live" "$NG_REPO" "nginx"
    rm -f "$tmp_live"
    live_8443="$(docker exec hoberadius-nginx sh -c 'cat /etc/nginx/conf.d/8443-ssl.conf 2>/dev/null || cat /etc/nginx/conf.d/*8443* 2>/dev/null' 2>/dev/null || true)"
    [ -n "$live_8443" ] && add nginx.tls_8443_active "yes" || add nginx.tls_8443_active "no"
  else
    add_unknown nginx.client_max_body_size "docker exec على nginx فشل رغم أنّ الحاوية تعمل"
  fi
else
  # fall back to the on-disk host files
  if [ -f "$NG_REPO" ]; then
    add_val nginx.client_max_body_size "لا سطر client_max_body_size في $NG_REPO" "$(grep -iE 'client_max_body_size' "$NG_REPO" | tr -s ' ' | sed 's/^ //' | paste -sd'; ' -)"
  elif have docker; then
    add_unknown nginx.client_max_body_size "حاوية hoberadius-nginx ليست قيد التشغيل وملف $NG_REPO مفقود"
  else
    add_unknown nginx.client_max_body_size "الأمر 'docker' غير مثبَّت وملف $NG_REPO مفقود"
  fi
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
# 5) LISTENING PORTS — الجدول الكامل + مالك كل منفذ (لا شيء مخفيّ)
#    الإصلاح: مالك :443 كان يُقرأ خطأً (sed يبتلع الاسم) فيُكتب "none" زورًا.
#    الآن: نلتقط كل LISTEN، ونستخرج NAME+pid من users:(("NAME",pid=N,...)) بدقّة،
#    ونميّز absent (لا شيء يستمع) عن UNKNOWN (يستمع لكن معلومات العمليّة تحتاج root).
# ══════════════════════════════════════════════════════════════════════════════
PORTS_OF_INTEREST="443 1723 80 8443 8000 1812 1813 3799"
if have ss; then
  SS_LISTEN="$(ss -lntupH 2>/dev/null)"     # -l listen -n numeric -t tcp -u udp -p process -H no-header
  if [ -n "$SS_LISTEN" ]; then
    addr ports.listen_table <<< "$SS_LISTEN"   # الجدول الكامل الخام في المانيفست (لا شيء مخفيّ)
    owner_of() {   # PORT → "name (pid=N)" | "absent (...)" | "__UNKNOWN__..."
      _p="$1"
      _l="$(printf '%s\n' "$SS_LISTEN" | grep -E "[^[:space:]]:${_p}[[:space:]]" | head -1)"
      if [ -z "$_l" ]; then echo "absent (لا LISTEN على :${_p})"; return; fi
      _n="$(printf '%s\n' "$_l" | sed -nE 's/.*users:\(\("([^"]+)".*/\1/p' | head -1)"
      _pid="$(printf '%s\n' "$_l" | sed -nE 's/.*pid=([0-9]+).*/\1/p' | head -1)"
      if [ -n "$_n" ]; then echo "$_n (pid=${_pid:-?})"
      elif [ "$IS_ROOT" -eq 0 ]; then echo "__UNKNOWN__المنفذ :${_p} يستمع لكن معلومات العمليّة تحتاج sudo"
      else echo "__UNKNOWN__ss لم يُظهر users:() على :${_p} رغم صلاحية root"; fi
    }
    for P in $PORTS_OF_INTEREST; do
      v="$(owner_of "$P")"
      case "$v" in __UNKNOWN__*) add_unknown "ports.${P}_owner" "${v#__UNKNOWN__}" ;; *) add "ports.${P}_owner" "$v" ;; esac
    done
  else
    add_unknown ports.listen_table "ss -lntup لم يُرجِع أي مخرجات"
    for P in $PORTS_OF_INTEREST; do add_unknown "ports.${P}_owner" "ss لم يُرجِع مخرجات"; done
  fi
else
  # ss غير مثبَّت — لا نترك أي منفذ مهمّ بلا قيمة (خصوصًا :443).
  add_unknown ports.listen_table "الأمر 'ss' (iproute2) غير مثبَّت"
  for P in $PORTS_OF_INTEREST; do add_unknown "ports.${P}_owner" "الأمر 'ss' غير مثبَّت"; done
fi

# ══════════════════════════════════════════════════════════════════════════════
# 6) ACCEL-PPP / SSTP / PPTP — config + service (active + enabled) + modules
# ══════════════════════════════════════════════════════════════════════════════
ACCEL_CONF="/etc/accel-ppp.conf"
if [ -f "$ACCEL_CONF" ]; then
  add accel.conf_present "yes"
  add accel.has_radius_section "$(grep -qiE '^\[radius\]' "$ACCEL_CONF" && echo yes || echo no)"
  add accel.has_sstp_section   "$(grep -qiE '^\[sstp\]'   "$ACCEL_CONF" && echo yes || echo no)"
  add accel.has_pptp_section   "$(grep -qiE '^\[pptp\]'   "$ACCEL_CONF" && echo yes || echo no)"
  # الوحدات المفعّلة من قسم [modules] (sstp/pptp/pppoe/…)
  mods="$(awk '/^\[modules\]/{f=1;next} /^\[/{f=0} f && $1!~/^[#;]/ && NF{print $1}' "$ACCEL_CONF" | paste -sd',' -)"
  add_val accel.enabled_modules "تعذّر تحليل قسم [modules] في accel-ppp.conf" "$mods"
  add accel.sstp_module_enabled "$(printf '%s' "$mods" | grep -qw sstp && echo yes || echo no)"
  add accel.pptp_module_enabled "$(printf '%s' "$mods" | grep -qw pptp && echo yes || echo no)"
  # منفذ SSTP من [sstp] (الافتراضي 443 لو غير مذكور)
  sstp_port="$(awk '/^\[sstp\]/{f=1;next} /^\[/{f=0} f && $1~/^(port|ssl-port|bind)/{print}' "$ACCEL_CONF" | grep -oE '[0-9]{2,5}' | head -1)"
  add accel.sstp_port "${sstp_port:-443 (accel default)}"
  add accel.pptp_port "1723 (accel default)"
  # redact any secret/password lines in the captured copy
  addr accel.conf_redacted < <(sed -E 's/^(\s*(secret|password|chap-secret)\s*=).*/\1 <redacted>/I' "$ACCEL_CONF")
  dupes="$(grep -oE '^\[[a-z-]+\]' "$ACCEL_CONF" | sort | uniq -d | tr '\n' ' ')"
  [ -n "$dupes" ] && drift "[accel] أقسام مكرّرة في accel-ppp.conf: $dupes (خطأ سابق معروف)."
else
  add_absent accel.conf_present
  drift "[accel] لا يوجد /etc/accel-ppp.conf — نفق SSTP للإدارة غير مثبَّت (أو مسار مختلف)."
fi
# /dev/ppp: غياب مؤكَّد vs موجود
[ -c /dev/ppp ] && add accel.dev_ppp "present" || add accel.dev_ppp "absent (/dev/ppp غير موجود)"
# وحدات PPP في النواة
if require_cmd lsmod accel.ppp_modules; then
  ppmods="$(lsmod 2>/dev/null | awk '$1 ~ /^(ppp_generic|ppp_async|pppox|pptp|sstp)/{print $1}' | paste -sd',' -)"
  [ -n "$ppmods" ] && add accel.ppp_modules "$ppmods" || add accel.ppp_modules "absent (لا وحدات ppp محمّلة في lsmod)"
fi
# حالة الخدمة: تعمل الآن؟ + تبدأ مع الإقلاع؟ (حقول أولى الدرجة)
if require_cmd systemctl accel.service_active; then
  a="$(systemctl is-active  accel-ppp 2>/dev/null)"; add_val accel.service_active  "systemctl is-active أرجع فراغًا"  "$a"
  e="$(systemctl is-enabled accel-ppp 2>/dev/null)"; add_val accel.service_enabled "systemctl is-enabled أرجع فراغًا" "$e"
  [ "$e" = "enabled" ] || drift "[accel] الخدمة accel-ppp ليست enabled على الإقلاع (is-enabled=$e) — لن تعمل بعد إعادة التشغيل."
fi

# ══════════════════════════════════════════════════════════════════════════════
# 6) WIREGUARD — wg0 subnet + peers (keys redacted)
# ══════════════════════════════════════════════════════════════════════════════
if require_cmd wg wg.status; then
  if ip link show wg0 >/dev/null 2>&1; then
    add wg.wg0_up "yes"
    add_val wg.server_ip   "wg0 مرفوع لكن بلا عنوان IPv4 (تحقّق يدويًّا)" "$(ip -o -4 addr show wg0 2>/dev/null | awk '{print $4}' | head -1)"
    # wg show يحتاج root لإظهار المفاتيح/النظائر
    pc="$(wg show wg0 peers 2>/dev/null | grep -c . )"
    if [ "$IS_ROOT" -eq 1 ]; then add wg.peer_count "$pc"; else add_unknown wg.peer_count "wg show يحتاج sudo لعدّ النظائر"; fi
    add_val wg.listen_port   "wg show لم يُرجِع listen-port (يحتاج sudo؟)" "$(wg show wg0 listen-port 2>/dev/null)"
    add_val wg.server_pubkey "wg show لم يُرجِع المفتاح العامّ (يحتاج sudo؟)" "$(wg show wg0 public-key 2>/dev/null)"
  else
    add wg.wg0_up "no"
    add_absent wg.server_pubkey
    drift "[wg] الواجهة wg0 غير مرفوعة — نفق إدارة الراوترات غير نشط."
  fi
fi
if [ -f /etc/wireguard/wg0.conf ]; then
  add wg.conf_present "yes"
  addr wg.conf_redacted < <(sed -E 's/^(\s*(PrivateKey|PresharedKey)\s*=).*/\1 <redacted>/I' /etc/wireguard/wg0.conf)
  add_val wg.subnet "تعذّر استخراج Address من wg0.conf" "$(grep -iE '^\s*Address' /etc/wireguard/wg0.conf | grep -oE '[0-9.]+/[0-9]+' | head -1)"
elif [ ! -r /etc/wireguard/wg0.conf ] && [ "$IS_ROOT" -eq 0 ]; then
  add_unknown wg.conf_present "‎/etc/wireguard/wg0.conf‎ يحتاج sudo للقراءة"
else
  add_absent wg.conf_present
fi

# ══════════════════════════════════════════════════════════════════════════════
# 7) FREERADIUS — mods/sites/clients, $INCLUDE wizard dir, DIFF vs repo
# ══════════════════════════════════════════════════════════════════════════════
FR_REPO="$HR_ROOT/deploy/freeradius"
if ! have docker; then
  add_unknown freeradius.mods_enabled "الأمر 'docker' غير مثبَّت"
elif docker ps --format '{{.Names}}' 2>/dev/null | grep -qx hoberadius-freeradius; then
  add_val freeradius.mods_enabled "docker exec لم يُرجِع mods-enabled" \
    "$(docker exec hoberadius-freeradius sh -c 'ls /etc/freeradius/3.0/mods-enabled 2>/dev/null || ls /etc/raddb/mods-enabled 2>/dev/null' 2>/dev/null | paste -sd',' -)"
  add freeradius.has_sql "$(docker exec hoberadius-freeradius sh -c 'test -e /etc/freeradius/3.0/mods-enabled/sql -o -e /etc/raddb/mods-enabled/sql && echo yes || echo no' 2>/dev/null)"
  add freeradius.has_mschap "$(docker exec hoberadius-freeradius sh -c 'test -e /etc/freeradius/3.0/mods-enabled/mschap -o -e /etc/raddb/mods-enabled/mschap && echo yes || echo no' 2>/dev/null)"
  WZ="$HR_ROOT/instance/freeradius-clients-wizard"
  add freeradius.wizard_client_count "$( ls "$WZ"/*.conf 2>/dev/null | grep -c . )"
else
  # الحاوية غير قيد التشغيل — مجهول لا "غياب" (قد تكون متوقّفة مؤقّتًا).
  add_unknown freeradius.mods_enabled "حاوية hoberadius-freeradius ليست قيد التشغيل"
  add_unknown freeradius.has_sql "حاوية hoberadius-freeradius ليست قيد التشغيل"
  add_unknown freeradius.has_mschap "حاوية hoberadius-freeradius ليست قيد التشغيل"
fi
diff_file "$HR_ROOT/deploy/freeradius/clients.conf" "$FR_REPO/clients.conf" "freeradius_clients"

# ══════════════════════════════════════════════════════════════════════════════
# 8) FIREWALL / systemd / cron / ip addr
# ══════════════════════════════════════════════════════════════════════════════
if require_cmd iptables firewall.filter_rules; then
  ipt="$(iptables -S 2>/dev/null)"
  if [ -n "$ipt" ]; then
    addr iptables.filter_rules <<< "$ipt"
    chains="$(printf '%s\n' "$ipt" | grep -oE 'HR[-_A-Z]*|MGMT[-_A-Z]*|hoberadius[-_a-z]*' | sort -u | paste -sd',' -)"
    [ -n "$chains" ] && add firewall.mgmt_confine_chains "$chains" || add firewall.mgmt_confine_chains "absent (لا سلاسل mgmt-confinement)"
  elif [ "$IS_ROOT" -eq 0 ]; then
    add_unknown firewall.filter_rules "iptables -S يحتاج sudo"
    add_unknown firewall.mgmt_confine_chains "iptables -S يحتاج sudo"
  else
    add firewall.mgmt_confine_chains "absent (iptables فارغة)"
  fi
fi
if require_cmd systemctl systemd.hobe_units; then
  hu="$(systemctl list-unit-files 2>/dev/null | grep -iE 'hobe|wg-reload|accel' | awk '{print $1}' | paste -sd',' -)"
  [ -n "$hu" ] && add systemd.hobe_units "$hu" || add systemd.hobe_units "absent (لا وحدات hobe/wg-reload/accel)"
fi
add cron.root "$(crontab -l 2>/dev/null | grep -vcE '^\s*#')  entries"
cd_files="$(ls /etc/cron.d 2>/dev/null | paste -sd',' -)"; [ -n "$cd_files" ] && add cron.d_files "$cd_files" || add cron.d_files "absent"
# host manual ip addr add (e.g. 10.x/32 on lo)
lo_x="$(ip -o -4 addr show lo 2>/dev/null | awk '{print $4}' | grep -vE '^127\.' | paste -sd',' -)"
[ -n "$lo_x" ] && add net.lo_extra_addrs "$lo_x" || add net.lo_extra_addrs "absent (لا عناوين إضافيّة على lo)"

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
if [ ! -f "$DB_HOST" ] && [ "$SQ" != "backup" ]; then
  # DB file غير موجود على المضيف والحاوية غير متاحة — قد تكون قاعدة جديدة لم تُنشأ.
  add_val db.access_via "قاعدة البيانات غير موجودة بعد ولا حاوية backup للقراءة" ""
  add_unknown db.migration_version "لا وصول لـ sqlite (لا ملف DB ولا حاوية backup)"
  add_unknown db.table_count "لا وصول لـ sqlite"
elif [ -n "$SQ" ]; then
  add db.access_via "$SQ"
  add_val db.migration_version "استعلام _migrations أرجع فراغًا (قاعدة جديدة قبل الهجرات؟)" "$(run_sql "SELECT name FROM _migrations ORDER BY name DESC LIMIT 1;")"
  add_val db.migration_count "استعلام COUNT(_migrations) فشل" "$(run_sql "SELECT COUNT(*) FROM _migrations;")"
  tbls="$(run_sql "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")"
  if [ -n "$tbls" ]; then
    add db.table_count "$(printf '%s\n' "$tbls" | grep -c . )"
    for t in $tbls; do add "db.rows.$t" "$(run_sql "SELECT COUNT(*) FROM \"$t\";")"; done
  else
    add_unknown db.table_count "sqlite_master أرجع فراغًا (تعذّر الاستعلام)"
  fi
else
  add_unknown db.access_via "لا sqlite3 على المضيف ولا حاوية hoberadius-backup — عدّاد الجداول غير متاح"
  add_unknown db.migration_version "لا وصول لـ sqlite"
  drift "[db] تعذّر الوصول لـ sqlite — ثبّت sqlite3 أو شغّل حاوية backup لالتقاط إصدار الهجرات والجداول."
fi

# ══════════════════════════════════════════════════════════════════════════════
# 10) LICENSING linkage (tenant_settings, license_key REDACTED)
# ══════════════════════════════════════════════════════════════════════════════
if [ -n "$SQ" ] && [ -f "$DB_HOST" -o "$SQ" = "backup" ]; then
  bu="$(run_sql "SELECT value FROM tenant_settings WHERE key='license_admin_bridge.base_url' LIMIT 1;")"
  en="$(run_sql "SELECT value FROM tenant_settings WHERE key='license_admin_bridge.enabled' LIMIT 1;")"
  lk="$(run_sql "SELECT value FROM tenant_settings WHERE key='license_admin_bridge.license_key' LIMIT 1;")"
  # قيمة فارغة هنا = غير مضبوط فعلًا (الإعداد غائب من الجدول) → "unset" لا فراغ.
  add licensing.bridge_base_url "${bu:-unset (لم يُضبط الجسر بعد)}"
  add licensing.bridge_enabled  "${en:-unset}"
  if [ -n "$lk" ]; then add licensing.license_key "SET sha256:$(sha "$lk")"; else add licensing.license_key "unset"; fi
  add_val licensing.enabled_service_rows "استعلام services فشل (جدول مفقود؟)" "$(run_sql "SELECT COUNT(*) FROM services WHERE COALESCE(enabled,0)=1;")"
else
  add_unknown licensing.bridge_enabled "لا وصول لـ sqlite لقراءة ربط الترخيص"
fi

# ══════════════════════════════════════════════════════════════════════════════
# 11) GIT HEADs of the repos checked out on the VPS
# ══════════════════════════════════════════════════════════════════════════════
if ! have git; then
  add_unknown git.radius_module_head "الأمر 'git' غير مثبَّت"
elif [ -d "$HR_ROOT/.git" ]; then
  add_val git.radius_module_head "git rev-parse فشل في $HR_ROOT" "$(cd "$HR_ROOT" && git rev-parse HEAD 2>/dev/null)"
  add git.radius_module_dirty "$(cd "$HR_ROOT" && [ -n "$(git status --porcelain 2>/dev/null)" ] && echo yes || echo no)"
else
  add_absent git.radius_module_head    # الجذر ليس مستودع git (نسخة يدويّة؟)
fi
if [ -d "$PROXY_ROOT/.git" ]; then add_val git.radius_proxy_head "git rev-parse فشل في $PROXY_ROOT" "$(cd "$PROXY_ROOT" && git rev-parse HEAD 2>/dev/null)"; else add git.radius_proxy_head "absent (radius-proxy غير مثبَّت على هذا الـ VPS)"; fi

# ══════════════════════════════════════════════════════════════════════════════
# UNKNOWN roll-up — كل حقل تعذّر اكتشافه يُسجَّل في المانيفست + يُنبَّه عليه بصوت.
# ══════════════════════════════════════════════════════════════════════════════
UNK_N="$(grep -c . "$UNK_FILE" 2>/dev/null || echo 0)"
add meta.unknown_count "$UNK_N"
i=0
while IFS="$(printf '\t')" read -r uk ur; do
  [ -z "$uk" ] && continue
  i=$((i+1)); add "meta.unknowns.$i" "$uk — $ur"
done < "$UNK_FILE"

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
  echo "── حقول تعذّر اكتشافها (UNKNOWN) — راجعها يدويًّا، لم نُخفِها ولم نزوّرها ──"
  if [ "$UNK_N" -gt 0 ]; then
    while IFS="$(printf '\t')" read -r uk ur; do [ -n "$uk" ] && echo "  • $uk — السبب: $ur"; done < "$UNK_FILE"
    echo
    echo "  (عادةً: شغّل بـ sudo، أو ثبّت الأمر الناقص، أو شغّل الحاوية المطلوبة، ثم أعِد الجرد.)"
  else
    echo "  (لا شيء — كل الحقول تحدّدت بيقين.)"
  fi
  echo
  echo "── ملخّص سريع (من المانيفست) ──"
  echo "• حدّ رفع nginx (client_max_body_size): راجع nginx.client_max_body_size."
  echo "• مالك :443 (يجب أن يكون accel-pppd): راجع ports.443_owner."
  echo "• accel-ppp: service_active + service_enabled + sstp_module_enabled."
  echo "• كل المنافذ المستمِعة: راجع ports.listen_table."
  echo "• إصدار الهجرات (schema): راجع db.migration_version."
  echo "• ربط الترخيص: راجع licensing.* (المفتاح مُخفى)."
  echo "• git HEAD: راجع git.radius_module_head."
} > "$DRIFT"

echo "[inventory] drift report written: $DRIFT"
# تحذير صاخب لو أي حقل مجهول — كي يعرف المالك ما يجب فحصه يدويًّا.
if [ "$UNK_N" -gt 0 ]; then
  printf '\033[1;33m[inventory] ⚠ %s حقلًا رجع UNKNOWN (تعذّر اكتشافه) — مذكورة في تقرير الانحراف وفي meta.unknowns بالمانيفست:\033[0m\n' "$UNK_N" >&2
  while IFS="$(printf '\t')" read -r uk ur; do [ -n "$uk" ] && printf '    - %s (%s)\n' "$uk" "$ur" >&2; done < "$UNK_FILE"
  [ "$IS_ROOT" -eq 0 ] && printf '\033[1;33m    أرجِّح إعادة التشغيل بـ sudo لتقليل UNKNOWN.\033[0m\n' >&2
else
  echo "[inventory] ✓ لا حقول UNKNOWN — كل شيء تحدّد بيقين."
fi
echo "[inventory] ✅ تمّ. راجع:"
echo "    $MANIFEST"
echo "    $DRIFT   ← اقرأ هذا لتشوف كل تعديلاتك اليدويّة + حقول UNKNOWN"
