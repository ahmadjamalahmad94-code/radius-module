#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# _lib.sh — shared helpers for provision-fresh-vps.sh + verify-parity.sh.
# Sourced, not executed. POSIX-bash. No external deps beyond coreutils + python3.
# ─────────────────────────────────────────────────────────────────────────────

# ── colored logging (Arabic-friendly output the owner reads) ──
if [ -t 1 ]; then
  _C_R=$(printf '\033[31m'); _C_G=$(printf '\033[32m'); _C_Y=$(printf '\033[33m')
  _C_B=$(printf '\033[34m'); _C_0=$(printf '\033[0m')
else
  _C_R=; _C_G=; _C_Y=; _C_B=; _C_0=
fi
log()  { printf '%s[%s]%s %s\n' "$_C_B" "$(date -u +%H:%M:%SZ)" "$_C_0" "$*"; }
ok()   { printf '%s✓%s %s\n' "$_C_G" "$_C_0" "$*"; }
warn() { printf '%s⚠%s %s\n' "$_C_Y" "$_C_0" "$*" >&2; }
die()  { printf '%s✗ FATAL:%s %s\n' "$_C_R" "$_C_0" "$*" >&2; exit 1; }
step() { printf '\n%s══ %s ══%s\n' "$_C_B" "$*" "$_C_0"; }

have() { command -v "$1" >/dev/null 2>&1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "شغّله بصلاحيات root (sudo)."; }

# ── read a value out of a vps-manifest.json by dotted key (python stdlib) ──
# manifest_get FILE  dotted.key   → prints value or empty
manifest_get() {
  [ -f "$1" ] || return 1
  python3 - "$1" "$2" <<'PY' 2>/dev/null
import sys, json
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
node = d
for p in sys.argv[2].split("."):
    if isinstance(node, dict) and p in node:
        node = node[p]
    else:
        sys.exit(0)
print(node if not isinstance(node, (dict, list)) else json.dumps(node, ensure_ascii=False))
PY
}

# ── idempotency guard: run a step only once, marker under $STATE_DIR ──
# guard NAME  &&  { ...do work...; guard_done NAME; }
STATE_DIR="${STATE_DIR:-/var/lib/hoberadius-provision}"
guard() { [ ! -f "$STATE_DIR/$1.done" ]; }
guard_done() { mkdir -p "$STATE_DIR" 2>/dev/null || true; : > "$STATE_DIR/$1.done"; }

# ── prompt for a secret without echoing; keep empty if non-interactive ──
# prompt_secret VARNAME "prompt text"
prompt_secret() {
  _pv="$1"; _pt="$2"; _cur=""
  eval "_cur=\${$_pv:-}"
  [ -n "$_cur" ] && return 0
  if [ -t 0 ]; then
    printf '%s: ' "$_pt" >&2
    stty -echo 2>/dev/null || true
    IFS= read -r _val
    stty echo 2>/dev/null || true
    printf '\n' >&2
    eval "$_pv=\$_val"
  fi
}

# ── ensure a KEY=VALUE line exists/updated in an env file (idempotent) ──
set_env() { # set_env FILE KEY VALUE
  f="$1"; k="$2"; v="$3"
  touch "$f"
  if grep -qE "^${k}=" "$f" 2>/dev/null; then
    # only overwrite if current is empty (respect existing operator values)
    cur="$(grep -E "^${k}=" "$f" | head -1 | cut -d= -f2-)"
    if [ -z "$cur" ] && [ -n "$v" ]; then
      sed -i "s|^${k}=.*|${k}=${v}|" "$f"
    fi
  else
    printf '%s=%s\n' "$k" "$v" >> "$f"
  fi
}
