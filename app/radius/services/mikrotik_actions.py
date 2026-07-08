"""
mikrotik_actions — unified, read-only monitoring feed of every action that
travels between the RADIUS/panel and the MikroTik routers.

The truth is scattered across three stores; this service UNIONS them into
one time-ordered feed with a normalized action category, resolved router
name+IP (never a bare id), a resolved subscriber/card identity, a human
from→to detail, and a success/fail status:

  • audit_log   — speed changes, plan/profile updates, «إجراء المايكروتيك»,
                  password resets, config/deploy pushes, and (S-gap) live
                  CoA outcomes recorded at the dispatch site. Carries
                  result_status + error_message + router_id + before/after.
  • radpostauth — network RADIUS auth (Access-Accept/Reject) + panel/portal
                  web logins (via login_events._collect_rows) → the «الدخول»
                  and «الفشل» login rows.
  • sync_queue  — queued disconnect / reset_password / config-push jobs with
                  status (queued/syncing/done/failed) + last_error + router.

Everything is tenant-scoped and read-only. No raw/cryptic values ever reach
the row: raw action keys are Arabic-labelled, enum statuses are Arabic,
router ids resolve to «name / ip», and unknown reason codes are humanized.
"""
from __future__ import annotations

from typing import Any, Optional

from ..db.connection import db
from ..db.helpers import json_load


# ═══════════════════ action → category classification ═══════════════════
# Categories double as the top section-tab keys. «fail» is a cross-cutting
# section (status=فشل across all types), not a category, so it is not here.
CAT_LOGIN = "login"
CAT_DISCONNECT = "disconnect"
CAT_SPEED = "speed"
CAT_PLAN = "plan"
CAT_RESET = "reset_password"
CAT_CONFIG = "config"

# Section tabs across the top of the page (order = display order). Each is a
# category filter except «all» (everything) and «fail» (status filter).
SECTIONS: list[dict[str, str]] = [
    {"key": "all",            "label": "الكل",                    "icon": "layer-group"},
    {"key": CAT_LOGIN,        "label": "الدخول",                  "icon": "right-to-bracket"},
    {"key": CAT_DISCONNECT,   "label": "الفصل",                   "icon": "plug-circle-xmark"},
    {"key": CAT_SPEED,        "label": "تغيير السرعة",            "icon": "gauge-high"},
    {"key": CAT_PLAN,         "label": "تحديث الباقة",            "icon": "box-open"},
    {"key": CAT_RESET,        "label": "إعادة تعيين كلمة السر",   "icon": "key"},
    {"key": CAT_CONFIG,       "label": "دفع الإعداد",             "icon": "cloud-arrow-up"},
    {"key": "fail",           "label": "الفشل",                   "icon": "triangle-exclamation"},
]
_SECTION_KEYS = {s["key"] for s in SECTIONS}

# Specific action-key overrides (exact match wins over the substring rules).
# Value = (category, arabic_label). Labels stay tight and concrete.
_ACTION_OVERRIDES: dict[str, tuple[str, str]] = {
    "disconnect":            (CAT_DISCONNECT, "فصل جلسة (قطع اتصال)"),
    "card.disconnect":       (CAT_DISCONNECT, "فصل جلسة بطاقة"),
    "mt.coa.disconnect":     (CAT_DISCONNECT, "فصل جلسة (CoA)"),
    "mt.coa.set_speed":      (CAT_SPEED,      "تغيير السرعة (تطبيق حيّ)"),
    "card.set_speed":        (CAT_SPEED,      "تغيير سرعة البطاقة"),
    "bulk_set_speeds":       (CAT_SPEED,      "تغيير سرعة كل العروض"),
    "reset_password":        (CAT_RESET,      "إعادة تعيين كلمة السر"),
    "card.adjust_time":      (CAT_PLAN,       "تعديل وقت البطاقة"),
    "card.reset_usage":      (CAT_PLAN,       "تصفير استهلاك البطاقة"),
    "mt.coa.set_ip":         (CAT_CONFIG,     "تغيير IP الجلسة"),
}


def classify_action(action: str) -> Optional[tuple[str, str]]:
    """Map a raw audit action key to (category, arabic_label), or None when
    the action is not a router-facing action we surface. Exact overrides win;
    otherwise substring rules (ordered — disconnect before the rest). Labels
    fall back to the shared audit_format.action_label so no English leaks."""
    a = (action or "").strip()
    if not a:
        return None
    if a in _ACTION_OVERRIDES:
        return _ACTION_OVERRIDES[a]
    low = a.lower()

    if "auth_login" in low or low in ("login", "logout"):
        cat = CAT_LOGIN
    elif "disconnect" in low:
        cat = CAT_DISCONNECT
    elif "reset_password" in low or "reset-password" in low:
        cat = CAT_RESET
    elif "speed" in low or "rate_limit" in low or "ratelimit" in low:
        cat = CAT_SPEED
    elif ("profile" in low or "plan" in low
          or low in ("update", "extend_time") or "adjust_time" in low):
        cat = CAT_PLAN
    elif ("deploy" in low or "config" in low or "programming" in low
          or "push" in low or low.startswith("mt.coa.")):
        cat = CAT_CONFIG
    else:
        return None

    try:
        from .audit_format import action_label
        label = action_label(a) or a
    except Exception:  # noqa: BLE001
        label = a
    return cat, label


# ═══════════════════ status normalization ═══════════════════
_STATUS_OK = {"ok", "success", "succeeded", "done", "completed", "applied",
              "verified", "sent"}
_STATUS_FAIL = {"failed", "error", "fail", "aborted", "nak", "timeout"}
_STATUS_PENDING = {"queued", "syncing", "retrying", "pending", "planned"}

_STATUS_LABEL = {True: "نجاح", False: "فشل", None: "قيد الانتظار"}


def _norm_status(raw: str) -> Optional[bool]:
    """enum/free status → True(نجاح) / False(فشل) / None(قيد الانتظار)."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s in _STATUS_OK:
        return True
    if s in _STATUS_FAIL:
        return False
    if s in _STATUS_PENDING:
        return None
    return None


# ═══════════════════ disconnect reason → Arabic ═══════════════════
# Reason codes are written by the disconnect trigger sites (manual button,
# card disconnect, and policy_reconciler's compliance `why`). Every code maps
# to a clear Arabic label — no raw code ever reaches the «التفاصيل» column.
DISCONNECT_REASON_AR: dict[str, str] = {
    # manual
    "manual": "فصل يدوي من المدير", "admin": "فصل يدوي من المدير",
    # another device / concurrency (owner: «دخول جهاز آخر»)
    "device_limit_exceeded": "دخول جهاز آخر (تجاوز حدّ الأجهزة)",
    "concurrent_limit": "دخول جهاز آخر (تجاوز حدّ الأجهزة)",
    "concurrent": "دخول جهاز آخر (تجاوز حدّ الأجهزة)",
    "another_device": "دخول جهاز آخر", "replace": "دخول جهاز آخر",
    "shared_session": "دخول جهاز آخر (حساب مشترك)",
    # time / quota
    "session_timeout": "انتهاء وقت الجلسة", "conn_time": "انتهاء وقت الاتصال",
    "card_time": "انتهاء وقت البطاقة", "time_expired": "انتهاء الوقت",
    "quota_exhausted": "انتهاء الكوتا", "quota_exceeded": "انتهاء الكوتا",
    "quota": "انتهاء الكوتا",
    # expiry / status
    "expired": "انتهاء صلاحية البطاقة/الاشتراك",
    "expiry": "انتهاء صلاحية البطاقة/الاشتراك",
    "disabled": "تعطيل الحساب", "status": "تعطيل الحساب",
    "user_deleted": "حُذف المستخدم",
    # schedule
    "outside_hours": "خارج ساعات الدوام", "outside_days": "خارج أيام الدوام",
    "schedule": "خارج وقت الدوام", "out_of_schedule": "خارج وقت الدوام",
    # mac / policy
    "mac_mismatch": "عنوان الجهاز (MAC) غير مطابق",
    "mac": "عنوان الجهاز (MAC) غير مطابق",
    "random_mac_blocked": "عنوان MAC عشوائي ممنوع",
    "blocks": "حظر الوصول", "access_block": "حظر الوصول",
    "policy": "مخالفة سياسة الشبكة",
    # save-driven reconcile
    "plan_update": "تغيير الباقة", "batch_update": "تعديل الحزمة",
    "save": "إعادة مطابقة بعد حفظ",
}


def disconnect_reason_label(code: str | None) -> str:
    """Disconnect reason code → Arabic. Handles the `access_block:<type>`
    prefix; any unknown code is humanized (no snake_case leaks)."""
    raw = (code or "").strip()
    if not raw:
        return ""
    if raw in DISCONNECT_REASON_AR:
        return DISCONNECT_REASON_AR[raw]
    base = raw.split(":", 1)[0]
    if base in DISCONNECT_REASON_AR:
        return DISCONNECT_REASON_AR[base]
    return raw.replace("_", " ").replace(":", " — ").strip()


# ═══════════════════ router + subject resolution ═══════════════════
def _router_map(tid: int) -> dict[int, dict[str, str]]:
    try:
        rows = db().execute(
            "SELECT id, name, address FROM nas_devices WHERE tenant_id = ?",
            (tid,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {int(r["id"]): {"name": r["name"] or "", "address": r["address"] or ""}
            for r in rows}


def _router_by_ip(tid: int) -> dict[str, dict[str, str]]:
    """address → {id,name,address} so a bare nas ip/name from radpostauth or
    sync_queue can still resolve to a real router card."""
    out: dict[str, dict[str, str]] = {}
    for rid, r in _router_map(tid).items():
        r2 = {"id": rid, "name": r["name"], "address": r["address"]}
        if r["address"]:
            out[r["address"]] = r2
        if r["name"]:
            out.setdefault(r["name"], r2)
    return out


def _card_username_set(tid: int) -> set[str]:
    try:
        rows = db().execute(
            "SELECT username FROM cards WHERE tenant_id = ?", (tid,)).fetchall()
        return {str(r["username"]) for r in rows}
    except Exception:  # noqa: BLE001
        return set()


# ═══════════════════ from→to detail ═══════════════════
_DIFF_IGNORE = {"id", "tenant_id", "updated_at", "created_at", "deleted_at",
                "password_hash", "csrf_token", "_csrf_token", "actor",
                "actor_id", "event_id"}
_MASK_KEYS = {"password", "pppoe_password", "pin", "secret"}


def _detail_from_change(before: dict, after: dict, *, limit: int = 4) -> str:
    """«الحقل: من X إلى Y» from before/after snapshots — Arabic keys/values via
    audit_format, sensitive values masked. Empty when there is no real diff."""
    if not isinstance(before, dict) or not isinstance(after, dict) or not after:
        return ""
    try:
        from .audit_format import _key_ar, _val_ar
    except Exception:  # noqa: BLE001
        _key_ar = None
        _val_ar = None
    bits: list[str] = []
    for k in after:
        if k in _DIFF_IGNORE or k.endswith(("_at", "_json", "_hash")):
            continue
        ov, nv = before.get(k), after.get(k)
        if str(ov) == str(nv):
            continue
        label = _key_ar(k) if callable(_key_ar) else k
        if k in _MASK_KEYS:
            o = n = "••••"
        else:
            try:
                o = _val_ar(k, ov) if callable(_val_ar) else str(ov)
                n = _val_ar(k, nv) if callable(_val_ar) else str(nv)
            except Exception:  # noqa: BLE001
                o, n = str(ov), str(nv)
        bits.append(f"{label}: من {o or '—'} إلى {n or '—'}")
        if len(bits) >= limit:
            break
    return "، ".join(bits)


def _detail_from_payload(payload: dict) -> str:
    """Best-effort from→to when there is no before/after snapshot but the
    payload carries explicit old/new pairs (e.g. rate / expire pushes)."""
    if not isinstance(payload, dict):
        return ""
    pairs = [
        ("rate_old", "rate_new", "السرعة"),
        ("old_rate", "new_rate", "السرعة"),
        ("expire_at_old", "expire_at_new", "الانتهاء"),
        ("old", "new", "القيمة"),
    ]
    for ko, kn, lbl in pairs:
        if payload.get(ko) not in (None, "") or payload.get(kn) not in (None, ""):
            return f"{lbl}: من {payload.get(ko) or '—'} إلى {payload.get(kn) or '—'}"
    # single-value pushes (no "from"): show the pushed value plainly
    for k, lbl in (("rate", "السرعة"), ("new_framed_ip", "IP الجديد"),
                   ("session_timeout", "مهلة الجلسة"), ("count", "عدد الجلسات")):
        v = payload.get(k)
        if v not in (None, ""):
            return f"{lbl}: {v}"
    return ""


# ═══════════════════ row builders ═══════════════════
def _audit_router_actions(tid: int, date_from: str, date_to: str,
                          rmap: dict, rip: dict) -> list[dict]:
    """audit_log rows that map to a router category (login excluded here — it
    is sourced from login_events so network + web are both covered)."""
    where = ["tenant_id = ?"]
    params: list[Any] = [tid]
    if date_from:
        where.append("created_at >= ?"); params.append(f"{date_from} 00:00:00")
    if date_to:
        where.append("created_at <= ?"); params.append(f"{date_to} 23:59:59")
    sql = (f"SELECT * FROM audit_log WHERE {' AND '.join(where)} "
           "ORDER BY id DESC LIMIT 3000")
    try:
        raw = db().execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001
        return []

    out: list[dict] = []
    for r in raw:
        r = dict(r)
        hit = classify_action(str(r.get("action") or ""))
        if hit is None or hit[0] == CAT_LOGIN:
            continue
        cat, label = hit
        rid = r.get("router_id")
        payload = json_load(r.get("payload_json"), default={}) or {}
        router = _resolve_router(rid, payload.get("nas_ip"), rmap, rip)
        detail = _detail_from_change(
            json_load(r.get("before_json"), default={}) or {},
            json_load(r.get("after_json"), default={}) or {},
        ) or _detail_from_payload(payload)
        raw_status = str(r.get("result_status") or "")
        ok = _norm_status(raw_status)
        if cat == CAT_DISCONNECT:
            # The reason is the star of the «التفاصيل» column for disconnects.
            reason_lbl = disconnect_reason_label(payload.get("reason"))
            if reason_lbl:
                detail = reason_lbl
            # A disconnect audit row is written only AFTER the dispatch call
            # returned (every trigger site audits post-dispatch — a failed
            # dispatch raises before auditing), so an empty result_status means
            # it completed, NOT «قيد الانتظار». Rows with an explicit
            # success/failed keep it.
            if not raw_status:
                ok = True
        out.append({
            "when": r.get("created_at") or "",
            "category": cat,
            "action_label": label,
            "router_name": router["name"], "router_ip": router["ip"],
            "subject": _subject_label(r.get("target_type"), r.get("target_id"),
                                      r.get("actor")),
            "detail": detail,
            "ok": ok,
            "error": (r.get("error_message") or "") if ok is False else "",
            "source": "audit",
        })
    return out


def _login_rows(tid: int, date_from: str, date_to: str,
                rip: dict) -> list[dict]:
    """Login/logout rows (network RADIUS + panel/portal web) via the shared
    login_events collector, mapped to the unified shape."""
    try:
        from .login_events import _collect_rows
        raw = _collect_rows(tid, date_from=date_from, date_to=date_to)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for r in raw:
        nas = str(r.get("nas") or "")
        hit = rip.get(nas)
        router_name = hit["name"] if hit else (nas if r.get("source") == "network" else "")
        router_ip = (hit["address"] if hit else (nas if _looks_ip(nas) else ""))
        out.append({
            "when": r.get("when") or "",
            "category": CAT_LOGIN,
            "action_label": "تسجيل الدخول" if r.get("success") else "محاولة دخول فاشلة",
            "router_name": router_name, "router_ip": router_ip,
            "subject": str(r.get("username") or "—"),
            "detail": r.get("reason") or "",
            "ok": bool(r.get("success")),
            "error": r.get("reason") or "" if not r.get("success") else "",
            "source": r.get("source") or "network",
        })
    return out


def _queue_rows(tid: int, date_from: str, date_to: str,
                rmap: dict, rip: dict) -> list[dict]:
    """Config-push sync jobs still tracked in the legacy queue.

    NOTE — 'disconnect' is intentionally EXCLUDED: `enqueue_disconnect` has no
    callers (dead since R11.16 — the live «قطع» routes through CoA/UDP-3799,
    audit-logged with a real result), and no worker resolves such rows, so any
    sync_queue disconnect row is a stale ghost that would sit at «قيد الانتظار»
    forever. The authoritative disconnect record is the audit_log 'disconnect'
    row (real router + نجاح/فشل). De-duplicating here = one disconnect, one row.
    'reset_password' is likewise dropped — it executes via the live adapter and
    would otherwise duplicate as a perpetual-queued ghost."""
    where = ["tenant_id = ?",
             "kind IN ('subscriber_upsert','config_push')"]
    params: list[Any] = [tid]
    if date_from:
        where.append("created_at >= ?"); params.append(f"{date_from} 00:00:00")
    if date_to:
        where.append("created_at <= ?"); params.append(f"{date_to} 23:59:59")
    sql = (f"SELECT * FROM sync_queue WHERE {' AND '.join(where)} "
           "ORDER BY id DESC LIMIT 1000")
    try:
        raw = db().execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001
        return []
    kind_meta = {
        "subscriber_upsert": (CAT_CONFIG,     "دفع بيانات المشترك"),
        "config_push":       (CAT_CONFIG,     "دفع إعداد"),
    }
    out: list[dict] = []
    for r in raw:
        r = dict(r)
        cat, label = kind_meta.get(str(r.get("kind") or ""), (CAT_CONFIG, "دفع إعداد"))
        rid = r.get("router_id") or r.get("last_router_id")
        router = _resolve_router(rid, "", rmap, rip)
        out.append({
            "when": r.get("created_at") or "",
            "category": cat,
            "action_label": label,
            "router_name": router["name"], "router_ip": router["ip"],
            "subject": str(r.get("entity_key") or "—"),
            "detail": "",
            "ok": _norm_status(str(r.get("status") or "")),
            "error": (r.get("last_error") or "")
                     if _norm_status(str(r.get("status") or "")) is False else "",
            "source": "queue",
        })
    return out


# ═══════════════════ small helpers ═══════════════════
def _looks_ip(s: str) -> bool:
    parts = (s or "").split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def _resolve_router(router_id, nas_ip, rmap: dict, rip: dict) -> dict:
    """→ {"name","ip"} — never a bare id. Resolves by router_id first, then by
    a nas_ip carried in the payload; falls back to the raw ip (a real value)."""
    if router_id not in (None, "", 0):
        try:
            r = rmap.get(int(router_id))
            if r:
                return {"name": r["name"], "ip": r["address"]}
        except (TypeError, ValueError):
            pass
    ip = str(nas_ip or "")
    if ip:
        hit = rip.get(ip)
        if hit:
            return {"name": hit["name"], "ip": hit["address"]}
        return {"name": "", "ip": ip}
    return {"name": "", "ip": ""}


_TARGET_AR = {"user": "مشترك", "subscriber": "مشترك", "card": "كرت",
              "plan": "باقة", "session": "جلسة", "admin": "مدير"}


def _subject_label(target_type, target_id, actor) -> str:
    """Prefer a real username/name; never a bare id with no context."""
    tid_val = str(target_id or "").strip()
    tt = str(target_type or "").strip().lower()
    # target_id is usually the username itself for session/card/user targets
    if tid_val and not tid_val.isdigit():
        return tid_val
    if tt in ("session", "card", "user", "subscriber") and tid_val:
        # numeric id with a known entity type — label it, don't show a bare id
        return f"{_TARGET_AR.get(tt, 'كيان')} #{tid_val}"
    a = str(actor or "").strip()
    if a and not a.isdigit() and a not in ("system", "ui"):
        return a
    return "—"


# ═══════════════════ public API ═══════════════════
def fetch_mikrotik_actions(tenant_id: int, *, section: str = "all",
                           q: str = "", date_from: str = "", date_to: str = "",
                           limit: int = 500) -> dict:
    """Unified, time-ordered MikroTik-actions feed + per-section KPIs.

    Returns {rows, stats:{total,ok,fail,pending}, sections, active, shown,
    matched}. `section` filters by category, except «all» (everything) and
    «fail» (status=فشل across all categories)."""
    section = section if section in _SECTION_KEYS else "all"
    rmap = _router_map(tenant_id)
    rip = _router_by_ip(tenant_id)

    rows: list[dict] = []
    rows += _audit_router_actions(tenant_id, date_from, date_to, rmap, rip)
    rows += _login_rows(tenant_id, date_from, date_to, rip)
    rows += _queue_rows(tenant_id, date_from, date_to, rmap, rip)

    # section filter
    def _in_section(r: dict) -> bool:
        if section == "all":
            return True
        if section == "fail":
            return r["ok"] is False
        return r["category"] == section

    rows = [r for r in rows if _in_section(r)]

    # free-text search over router + subject + action label
    ql = (q or "").strip().lower()
    if ql:
        rows = [r for r in rows if ql in
                f"{r['router_name']} {r['router_ip']} {r['subject']} "
                f"{r['action_label']}".lower()]

    rows.sort(key=lambda r: r["when"], reverse=True)
    for r in rows:
        r["status_label"] = _STATUS_LABEL[r["ok"]]

    matched = len(rows)
    stats = {
        "total": matched,
        "ok": sum(1 for r in rows if r["ok"] is True),
        "fail": sum(1 for r in rows if r["ok"] is False),
        "pending": sum(1 for r in rows if r["ok"] is None),
    }
    rows = rows[:limit]
    return {"rows": rows, "stats": stats, "sections": SECTIONS,
            "active": section, "shown": len(rows), "matched": matched}
