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
    "temporary_speed.apply": (CAT_SPEED,      "تغيير السرعة (مؤقتة)"),
    "temporary_speed.revert":(CAT_SPEED,      "إرجاع السرعة العادية"),
    "set_speed":             (CAT_SPEED,      "تغيير السرعة"),
    "reset_password":        (CAT_RESET,      "إعادة تعيين كلمة السر"),
    "card.adjust_time":      (CAT_PLAN,       "تعديل وقت البطاقة"),
    "card.reset_usage":      (CAT_PLAN,       "تصفير استهلاك البطاقة"),
    "mt.coa.set_ip":         (CAT_CONFIG,     "تغيير IP الجلسة"),
}


def classify_action(action: str) -> Optional[tuple[str, str]]:
    """Map a raw audit action key to (category, arabic_label), or None when
    the action is NOT a genuine router-facing dispatch.

    SCOPING RULE (owner: «ما بيلزم تغيير الاسم للمايكروتيك»): this feed is the
    log of actions that actually reach the MikroTik. A DB-only edit — a
    subscriber name/label, an offer rename, a remark/email change — writes a
    generic `update`/`create`/`disable`/… and never touches the device, so it
    is EXCLUDED here (its home is the subscriber/manager event logs). We use an
    ALLOWLIST of router-facing action keys/prefixes; anything else → None. This
    also cures the «قيد الانتظار» flood, since those pending rows were DB edits
    with no dispatch result."""
    a = (action or "").strip()
    if not a:
        return None
    if a in _ACTION_OVERRIDES:
        return _ACTION_OVERRIDES[a]
    low = a.lower()

    # ── router-facing allowlist (exact overrides above already matched) ──
    if low == "disconnect" or low.endswith(".disconnect"):
        cat = CAT_DISCONNECT
    elif low == "reset_password" or low.endswith(".reset_password"):
        cat = CAT_RESET
    elif ("set_speed" in low or "rate_limit" in low or "ratelimit" in low
          or low == "bulk_set_speeds"
          or low.startswith("temporary_speed.")
          or low.startswith("speed_control.")):
        cat = CAT_SPEED
    elif low.startswith("mt.coa."):
        # live CoA that isn't speed/disconnect (e.g. set_ip) → config push
        cat = CAT_CONFIG
    elif low.startswith("mt.") and any(t in low for t in (
            "deploy", "config", "programming", "push", "backup",
            "provision", "login_designer")):
        cat = CAT_CONFIG
    elif (low.startswith("mt.profile") or low.startswith("profile_push")
          or low in ("plan_upsert", "plan_push", "reprovision",
                     "subscriber_reprovision")):
        # a plan/profile change ACTUALLY pushed to the router
        cat = CAT_PLAN
    else:
        # generic update/create/disable/enable/delete/extend_time/rename/… →
        # DB-only, not a router action → excluded from this feed.
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
    # RFC 5176 CoA/PoD reply codes stored raw by some paths (temp-speed stores
    # the code_name) → normalize so a delivered change reads نجاح, not pending.
    if "nak" in s or "reject" in s or "unsupported" in s:
        return False
    if "ack" in s:
        return True
    return None


# ═══════════════════ human-readable speed «ميجا» (bidi-safe) ═══════════════════
def _kbps_to_ar(kbps: int) -> str:
    """One rate component (kbps) → Arabic «X ميجا» (owner: «بالميجا أفضل من
    كيلو»). Canonical conversion: Mbps = 1024 kbps (core/units.SPEED_UNITS), so
    40960k → «40 ميجا», 7680k → «7.5 ميجا». One decimal only when it matters;
    below 1 Mbps stays «X كيلو». Zero/unknown → '' (caller shows «غير معروف»)."""
    if kbps <= 0:
        return ""
    if kbps < 1024:
        return f"{kbps} كيلو"
    mbps = kbps / 1024
    txt = f"{mbps:.0f}" if abs(mbps - round(mbps)) < 0.05 else f"{mbps:.1f}"
    return f"{txt} ميجا"


def _fmt_speed_value(raw) -> str:
    """A Mikrotik-Rate-Limit value → human Arabic Mbps. Accepts «40960k/40960k»,
    «40960k 40960k», «40M/10M», «40960». Each rx/tx component becomes «X ميجا»;
    when rx==tx (the common case) the pair collapses to ONE value. Zero/blank →
    '' so the caller can render «غير معروف» (never a misleading «0»). Values are
    LTR-neutral tokens — the template wraps the cell in <bdi> for bidi safety."""
    import re
    s = str(raw or "").strip()
    if not s:
        return ""
    vals: list[str] = []
    for part in re.split(r"[\s/]+", s):
        if not part:
            continue
        m = re.match(r"^(\d+)\s*([kmg]?)$", part.strip().lower())
        if not m:
            vals.append(part)          # unknown token — keep verbatim
            continue
        n, unit = int(m.group(1)), m.group(2)
        kbps = n * (1 if unit in ("", "k") else (1024 if unit == "m" else 1024 * 1024))
        vals.append(_kbps_to_ar(kbps))
    real = [v for v in vals if v]
    if not real:
        return ""
    uniq = list(dict.fromkeys(real))
    return uniq[0] if len(uniq) == 1 else "/".join(real)


def _speed_detail(before: dict, after: dict, payload: dict) -> str:
    """«السرعة: من X إلى Y» for a speed change, human-readable in «ميجا». The
    FROM value is the REAL previous rate; when it is genuinely unknown (or a
    bogus 0) it shows «غير معروف», NEVER a misleading «0» (owner spec)."""
    def _rate(d: dict) -> str:
        for k in ("rate_limit", "rate", "mikrotik_rate_limit"):
            if isinstance(d, dict) and d.get(k) not in (None, ""):
                return str(d.get(k))
        return ""
    to = _fmt_speed_value(_rate(after) or _rate(payload))
    if not to:
        return ""
    frm = _fmt_speed_value(_rate(before)) or "غير معروف"
    return f"السرعة: من {frm} إلى {to}"


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
    # shared-card previous-session eviction (accounting-start)
    "shared_session_kick": "فصل الجلسات السابقة (حساب مشترك)",
    # card-batch flag: close other sessions on this card's disconnect
    "card_batch_close": "إغلاق جلسات الحساب (إعداد الحزمة)",
    # anti-MAC-clone concurrent-device kick
    "mac_clone": "اكتشاف جهاز متزامن مريب (تعدّد MAC)",
    "anti_mac_clone": "اكتشاف جهاز متزامن مريب (تعدّد MAC)",
    "concurrent_device_detected": "اكتشاف جهاز متزامن مريب (تعدّد MAC)",
    # temp-speed disconnect-reauth (kick to force a re-auth that returns the
    # new rate on routers that ignore rate-CoA)
    "temp_speed_reauth": "إعادة مصادقة لتطبيق السرعة",
    # stale-session reconciler (DB-only close) — raw CAUSE_* constants + codes
    "stale_session": "تسوية جلسة معلّقة (بلا نشاط)",
    "Stale-Session-Timeout": "تسوية جلسة معلّقة (بلا نشاط)",
    "nas_lost": "فقدان اتصال الجلسة من الراوتر",
    "NAS-Lost-Session": "فقدان اتصال الجلسة من الراوتر",
    "Reconciliation-Stale": "تسوية جلسة معلّقة (مصالحة يدويّة)",
    "Admin-Force-Close": "إغلاق إجباريّ من المدير",
    "force_close": "إغلاق إجباريّ من المدير",
    # honest label when the drop did NOT originate from us (router/network side)
    "external": "فصل من الشبكة/الراوتر (غير صادر منّا)",
    "router": "فصل من الشبكة/الراوتر (غير صادر منّا)",
    "network": "فصل من الشبكة/الراوتر (غير صادر منّا)",
    # ── RADIUS Acct-Terminate-Cause values (RFC 2866) written to radacct by
    # the NAS/router — the ONLY record for passive/router-terminated ends
    # (card time-budget & subscriber time exhaustion self-terminate here). ──
    "Session-Timeout": "انتهاء الوقت المسموح", "session-timeout": "انتهاء الوقت المسموح",
    "Idle-Timeout": "انقطاع لخمول (لا نشاط)", "idle-timeout": "انقطاع لخمول (لا نشاط)",
    "Device-Limit-Replace": "دخول جهاز آخر (استبدال الأقدم)",
    "Admin-Reset": "إعادة ضبط إداريّة (فصل)",
    "NAS-Request": "طلب الراوتر (فصل)",
    "NAS-Reboot": "إعادة تشغيل الراوتر",
    "NAS-Error": "خطأ في الراوتر",
    "Port-Error": "خطأ منفذ الراوتر",
    "Lost-Carrier": "فقدان الإشارة (Carrier)",
    "Lost-Service": "فقدان الخدمة",
    "Port-Preempted": "استُبق المنفذ",
    "Port-Suspended": "عُلّق المنفذ",
}

# honest fallback when a disconnect row carries no reason at all (legacy rows
# or a path we don't instrument): NOT «—», NOT a fabricated cause.
_DISCONNECT_REASON_UNKNOWN = "سبب غير مُسجَّل"


_DISCONNECT_REASON_AR_LC = {k.lower(): v for k, v in DISCONNECT_REASON_AR.items()}


def disconnect_reason_label(code: str | None) -> str:
    """Disconnect reason code / Acct-Terminate-Cause → Arabic. Case-insensitive;
    handles the `access_block:<type>` prefix; any unknown code is humanized (no
    snake_case / Title-Case-Hyphen leaks)."""
    raw = (code or "").strip()
    if not raw:
        return ""
    if raw in DISCONNECT_REASON_AR:
        return DISCONNECT_REASON_AR[raw]
    low = raw.lower()
    if low in _DISCONNECT_REASON_AR_LC:
        return _DISCONNECT_REASON_AR_LC[low]
    base = raw.split(":", 1)[0]
    if base in DISCONNECT_REASON_AR:
        return DISCONNECT_REASON_AR[base]
    if base.lower() in _DISCONNECT_REASON_AR_LC:
        return _DISCONNECT_REASON_AR_LC[base.lower()]
    return raw.replace("_", " ").replace("-", " ").replace(":", " — ").strip()


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
        if cat == CAT_SPEED:
            # Human-readable «من X إلى Y»; the FROM is the real previous rate,
            # never a misleading 0 (→ «غير معروف» when unknown).
            _sd = _speed_detail(
                json_load(r.get("before_json"), default={}) or {},
                json_load(r.get("after_json"), default={}) or {}, payload)
            if _sd:
                detail = _sd
        if cat == CAT_DISCONNECT:
            # The reason is the star of the «التفاصيل» column for disconnects.
            # Always show SOMETHING honest — the mapped reason, or «سبب غير
            # مُسجَّل» when a (legacy/uninstrumented) row carries none — never «—».
            reason_lbl = disconnect_reason_label(payload.get("reason"))
            detail = reason_lbl or _DISCONNECT_REASON_UNKNOWN
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
            # «شو اندفع» — the router attributes carried by this push.
            "detail": _config_push_detail(json_load(r.get("payload_json"),
                                                     default={}) or {}),
            "ok": _norm_status(str(r.get("status") or "")),
            "error": (r.get("last_error") or "")
                     if _norm_status(str(r.get("status") or "")) is False else "",
            "source": "queue",
        })
    return out


# Router-facing subscriber/config payload keys → Arabic (owner: «بدي أعرف شو
# نوع البيانات الي اندفعت»). Non-router metadata (username/email/remark) is
# deliberately absent so a name/email edit never becomes the headline.
_CONFIG_FIELD_AR: dict[str, str] = {
    "profile_name": "الباقة/البروفايل", "plan_id": "الباقة/البروفايل",
    "rate_limit": "السرعة (Rate-Limit)", "speed_down_kbps": "السرعة (Rate-Limit)",
    "speed_up_kbps": "السرعة (Rate-Limit)",
    "static_ip": "عنوان IP", "framed_ip": "عنوان IP",
    "mac_lock": "قفل MAC", "password": "كلمة السر",
    "status": "حالة التفعيل", "schedule": "جدول الاتصال",
    "session_timeout_sec": "مهلة الجلسة", "address_pool": "مجمّع العناوين",
}


def _config_push_detail(payload: dict) -> str:
    """Summarize WHAT a subscriber/config push actually sent to the router —
    the router-affecting fields present in the job payload, in Arabic. Returns
    e.g. «دفع: الباقة/البروفايل، قفل MAC، عنوان IP، حالة التفعيل، كلمة السر».
    Empty payload → '' (template shows «—»)."""
    if not isinstance(payload, dict) or not payload:
        return ""
    seen: list[str] = []
    for k, lbl in _CONFIG_FIELD_AR.items():
        if payload.get(k) not in (None, "", 0) and lbl not in seen:
            # profile → name it inline when we have it
            if k == "profile_name":
                seen.append(f"{lbl} ({payload.get(k)})")
            else:
                seen.append(lbl)
    if not seen:
        return ""
    # subscriber_upsert carries the whole snapshot → frame it as a full push
    head = "دفع بيانات المشترك" if "username" in payload else "دفع إعداد"
    return f"{head}: " + "، ".join(seen)


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


# ═══════════════════ radacct Acct-Stop disconnect source ═══════════════════
# Every terminated session lands in radacct with an Acct-Terminate-Cause + the
# NAS ip — the ONLY record for router-terminated ends (card time-budget &
# subscriber time exhaustion self-terminate via Session-Timeout; the auth-time
# replace kick writes Device-Limit-Replace). Our OWN initiated disconnects
# (manual / policy CoA) already appear from audit_log with a richer reason, so
# we de-dup radacct against them by session id.
#
# We EXCLUDE 'User-Request' and empty cause: those are voluntary user log-offs
# (the user's own device left) — not an enforcement/system disconnect. This
# keeps the «الفصل» tab meaningful (the owner's card-expiry / time-out / replace
# events) instead of burying it under normal log-offs.
_RADACCT_EXCLUDE_CAUSES = ("", "User-Request")


def _audit_disconnect_session_ids(tid: int, date_from: str, date_to: str) -> set:
    """Session ids we ALREADY show from audit_log disconnect rows (manual /
    policy CoA), so the radacct union doesn't duplicate them."""
    where = ["tenant_id = ?",
             "action IN ('disconnect','card.disconnect','mt.coa.disconnect')"]
    params: list[Any] = [tid]
    if date_from:
        where.append("created_at >= ?"); params.append(f"{date_from} 00:00:00")
    if date_to:
        where.append("created_at <= ?"); params.append(f"{date_to} 23:59:59")
    try:
        rows = db().execute(
            f"SELECT payload_json FROM audit_log WHERE {' AND '.join(where)} "
            "ORDER BY id DESC LIMIT 4000", params).fetchall()
    except Exception:  # noqa: BLE001
        return set()
    out: set = set()
    for r in rows:
        p = json_load(r["payload_json"], default={}) or {}
        # `sid` is the redaction-safe dedup key ("session_id" is masked to "***"
        # by the audit repo since it contains the fragment "session").
        for key in ("sid", "session_id"):
            v = str(p.get(key) or "").strip()
            if v and v != "***":
                out.add(v)
    return out


def _radacct_disconnect_rows(tid: int, date_from: str, date_to: str,
                             rip: dict, seen_sids: set) -> list[dict]:
    """Every enforcement/system session termination from radacct — the source
    that finally surfaces the router-terminated disconnects (card/sub time
    expiry, replace kicks) that write NO audit record. cause→reason,
    nasipaddress→router, de-duped against our audit-logged disconnects."""
    where = ["tenant_id = ?", "acctstoptime IS NOT NULL", "acctstoptime != ''",
             "acctterminatecause NOT IN (?, ?)"]
    params: list[Any] = [tid, *_RADACCT_EXCLUDE_CAUSES]
    if date_from:
        where.append("acctstoptime >= ?"); params.append(f"{date_from} 00:00:00")
    if date_to:
        where.append("acctstoptime <= ?"); params.append(f"{date_to} 23:59:59")
    try:
        raw = db().execute(
            "SELECT username, nasipaddress, acctsessionid, acctstoptime, "
            "acctterminatecause FROM radacct "
            f"WHERE {' AND '.join(where)} ORDER BY acctstoptime DESC LIMIT 4000",
            params).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for r in raw:
        r = dict(r)
        sid = str(r.get("acctsessionid") or "").strip()
        if sid and sid in seen_sids:
            continue                    # already shown from audit_log (our CoA)
        cause = str(r.get("acctterminatecause") or "")
        nas = str(r.get("nasipaddress") or "")
        hit = rip.get(nas)
        out.append({
            "when": r.get("acctstoptime") or "",
            "category": CAT_DISCONNECT,
            "action_label": "فصل جلسة (قطع اتصال)",
            "router_name": hit["name"] if hit else "",
            "router_ip": hit["address"] if hit else (nas if _looks_ip(nas) else ""),
            "subject": str(r.get("username") or "—"),
            "detail": disconnect_reason_label(cause) or _DISCONNECT_REASON_UNKNOWN,
            "ok": True,                 # a recorded Acct-Stop = the session ended
            "error": "",
            "source": "radacct",
        })
    return out


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
    # radacct Acct-Stop = the comprehensive disconnect source (router-terminated
    # expiry/time-out + replace kicks that write no audit row), de-duped against
    # our audit-logged CoA disconnects by session id.
    _seen_sids = _audit_disconnect_session_ids(tenant_id, date_from, date_to)
    rows += _radacct_disconnect_rows(tenant_id, date_from, date_to, rip, _seen_sids)

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
