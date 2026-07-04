#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install.sh — bootstrap واحد لتثبيت نسخة HobeRadius كاملة على VPS نظيف.
#
# يفعل شيئًا واحدًا: يضمن git، يستنسخ الريبو إلى /opt/hoberadius (أو يجلب آخره إن
# كان موجودًا)، ثم يسلّم التنفيذ لـ provision-fresh-vps.sh — السكربت الشامل الذي
# يثبّت كل شيء (Docker + الحاويات + الهجرات + accel SSTP/PPTP + الجدار + التحقّق).
#
# الاستخدام — أمر واحد على صندوق أوبونتو جديد:
#     curl -fsSL https://raw.githubusercontent.com/ahmadjamalahmad94-code/radius-module/main/deploy/install.sh | sudo bash
#
# تخصيص عبر متغيّرات بيئة (اختياريّة):
#     HR_ROLE=app|proxy      الدور (افتراضي app)
#     HR_SHA=origin/main     الإصدار المثبَّت (افتراضي origin/main)
#     HR_ROOT=/opt/hoberadius  جذر التثبيت
#     HR_MANIFEST=/path.json   مانيفست لإعادة إنتاج تعديلات صندوق قائم
#   مثال:  curl -fsSL …/install.sh | sudo HR_ROLE=app bash
#
# آمن لإعادة التشغيل: كل خطوة محميّة (المزوّد نفسه idempotent).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED=$'\033[1;31m'; GRN=$'\033[1;32m'; YLW=$'\033[1;33m'; NC=$'\033[0m'
log()  { printf '%s[install]%s %s\n' "$GRN" "$NC" "$*"; }
warn() { printf '%s[install WARN]%s %s\n' "$YLW" "$NC" "$*"; }
die()  { printf '%s[install FAIL]%s %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

# ── config (env-overridable) ──
HR_ROOT="${HR_ROOT:-/opt/hoberadius}"
HR_SHA="${HR_SHA:-origin/main}"
HR_ROLE="${HR_ROLE:-app}"
HR_MANIFEST="${HR_MANIFEST:-}"
HR_REMOTE="${HR_REMOTE:-https://github.com/ahmadjamalahmad94-code/radius-module.git}"

[ "$(id -u)" -eq 0 ] || die "شغّله كجذر:  curl -fsSL …/install.sh | sudo bash"

# ── 1) ضمان git (المزوّد يثبّت Docker وبقيّة المتطلّبات في STEP 1) ──
if ! command -v git >/dev/null 2>&1; then
  log "تثبيت git…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y  >/dev/null 2>&1 || warn "apt-get update أرجع تحذيرات."
  apt-get install -y git ca-certificates >/dev/null 2>&1 || die "تعذّر تثبيت git."
fi

# ── 2) استنساخ أو جلب الريبو عند SHA المثبَّت ──
if [ ! -d "$HR_ROOT/.git" ]; then
  log "استنساخ $HR_REMOTE → $HR_ROOT"
  mkdir -p "$(dirname "$HR_ROOT")"
  git clone "$HR_REMOTE" "$HR_ROOT" || die "git clone فشل."
else
  log "الريبو موجود في $HR_ROOT — جلب آخر التحديثات…"
fi
( cd "$HR_ROOT" && git fetch --all --quiet && git checkout -f -B main "$HR_SHA" ) \
  || die "checkout $HR_SHA فشل في $HR_ROOT."
log "الريبو @ $(cd "$HR_ROOT" && git rev-parse --short HEAD)"

# ── 3) تسليم التنفيذ للمزوّد الشامل ──
PROV="$HR_ROOT/deploy/provision/provision-fresh-vps.sh"
[ -f "$PROV" ] || die "لم أجد المزوّد $PROV — بنية الريبو غير متوقَّعة."
set -- --sha "$HR_SHA" --role "$HR_ROLE"
[ -n "$HR_MANIFEST" ] && set -- "$@" --manifest "$HR_MANIFEST"
log "تشغيل المزوّد الشامل:  provision-fresh-vps.sh $*"
echo "──────────────────────────────────────────────────────────────────────"
exec bash "$PROV" "$@"
