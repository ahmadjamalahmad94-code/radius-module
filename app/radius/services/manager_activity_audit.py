"""Request-level manager-activity audit interceptor.

The owner wants EVERY manager movement recorded — page visits, mutating
actions, and attempts that FAILED / were BLOCKED / had no effect — so they can
see exactly which PAGE a manager entered, what they DID, and what they TRIED to
do. This module is the server-enforced interceptor that guarantees it, no
matter what the individual route does.

It hangs off the radius blueprint's request lifecycle:

  • ``before_request`` — snapshot ``MAX(audit_log.id)`` so ``after_request`` can
    tell whether the request's own handler already wrote a rich field-diff row
    (subscriber/offer edit → old→new). If it did, we skip a duplicate *success*
    row: the rich row is strictly richer and already shows in the report.
  • ``after_request`` — classify the outcome from the final HTTP status,
    resolve the target entity + Arabic page/action labels, sanitize params
    (dropping secrets), and write ONE ``audit_log`` row via ``audit_repo.record``
    (which redacts secret-keyed values again as a safety net).

Rows land in the SAME ``audit_log`` the manager-events report already reads,
tagged ``target_type='manager_activity'`` with the promoted columns from
migration 161 (``http_method`` / ``endpoint`` / ``outcome`` / ``status_code`` /
``is_visit``). ``target_type='manager_activity'`` keeps these rows OUT of the
per-entity audit timelines (which query ``target_type IN ('subscriber','card',
…)``) and out of the specialized reports, while still surfacing in
manager-events (human actor). The rich per-action audits are untouched.

Volume control:
  * Plain GET page views (``is_visit=1``) are high-volume and can be disabled
    via ``HOBERADIUS_ACTIVITY_AUDIT_VISITS=0`` (default ON — owner wants
    everything). They prune on a SHORTER retention window than action rows
    (see ``services/log_retention.py``: the ``AUDIT_LOG_VISITS`` rule).
  * Action / attempt / failure / blocked rows are ALWAYS recorded (a blocked
    GET — "tried to open a page he can't" — is logged regardless of the visits
    toggle).
  * The whole interceptor can be disabled with ``HOBERADIUS_ACTIVITY_AUDIT=0``
    (escape hatch; default ON).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from flask import Blueprint, g, request, session

from ..db.connection import db
from ..db.repos import audit_repo
from ..db.repos.jobs_repo import SECRET_KEY_FRAGMENTS

_LOG = logging.getLogger(__name__)


# ── config toggles ─────────────────────────────────────────────────────────
def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _enabled() -> bool:
    return _flag("HOBERADIUS_ACTIVITY_AUDIT", True)


def _visits_enabled() -> bool:
    return _flag("HOBERADIUS_ACTIVITY_AUDIT_VISITS", True)


# Endpoints never worth an activity row — health probes, the locale switcher,
# static assets. (Login/logout carry their own rich auth events; an
# unauthenticated GET to the login page has no admin_id and is skipped anyway.)
_SKIP_ENDPOINTS: frozenset[str] = frozenset({
    "_radius_health", "_health", "healthz", "set_locale", "static",
})

_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Redirect targets that mean "you were bounced off a page you may not enter" —
# a blocked attempt even though the status is a 3xx, not a 403.
_GATE_LOCATION_MARKERS: tuple[str, ...] = (
    "auth/login", "license_activate", "license_expired",
    "provider_blocked", "provider_upgrade",
)

# Params dropped outright before the payload is stored (never even keyed).
# audit_repo._redact is a second net for anything secret-keyed we miss.
_DROP_PARAMS: frozenset[str] = frozenset({
    "_csrf_token", "csrf_token", "csrf",
    "password", "new_password", "confirm_password", "current_password",
    "old_password", "pppoe_password", "wifi_password", "card_password",
    "pin", "secret", "client_secret", "api_key", "apikey", "bot_token",
    "token", "access_token",
})


# ── outcome classification ─────────────────────────────────────────────────
def _classify(method: str, status: int, location: str) -> tuple[str, bool]:
    """Return ``(outcome, is_visit)`` from the finished request.

    outcome ∈ {visit, success, failed, blocked}. no-op is decided afterwards.
    """
    mutating = method in _MUTATING_METHODS
    if status >= 500:
        return "failed", False
    if status in (403, 429):
        return "blocked", False
    if status in (400, 401, 422):
        return "failed", False
    if 300 <= status < 400 and _is_gate_location(location):
        # Bounced to login / license / provider gate → a blocked attempt.
        return "blocked", False
    if not mutating:
        # GET/HEAD success, or a benign redirect (PRG on a GET is rare) → visit.
        return "visit", True
    return "success", False


def _is_gate_location(location: str) -> bool:
    loc = (location or "").lower()
    return bool(loc) and any(m in loc for m in _GATE_LOCATION_MARKERS)


def _detect_noop(response) -> bool:
    """Best-effort: a mutating request whose JSON body says nothing changed.

    Matches the panel's common shapes (``changed: false`` / ``noop: true`` /
    an Arabic "لا تغيير" message). Never reads a streamed/oversized body.
    """
    try:
        if getattr(response, "direct_passthrough", False):
            return False
        if (response.mimetype or "") != "application/json":
            return False
        if response.status_code >= 300:
            return False
        raw = response.get_data(as_text=True)
        if not raw or len(raw) > 4096:
            return False
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(data, dict):
        return False
    if data.get("changed") is False or data.get("noop") is True \
            or data.get("no_change") is True or data.get("unchanged") is True:
        return True
    msg = str(data.get("message") or data.get("message_ar")
              or data.get("info") or "")
    return any(p in msg for p in (
        "لا تغيير", "لم يتغيّر", "لم يتغير", "دون تغيير", "بلا تغيير", "لا جديد",
    ))


def note_noop() -> None:
    """Opt-in signal a handler can call when it detects "nothing changed", so
    the interceptor tags the row ``outcome='noop'`` even for a plain 200/redirect
    with no JSON body. Safe no-op outside a request context."""
    try:
        g._activity_outcome = "noop"
    except Exception:  # noqa: BLE001
        pass


# ── Arabic labels: page (which page) + action (what he did/tried) ───────────
# Explicit page labels for the important pages. Anything not here falls back to
# a section-noun + verb derivation (_fallback_page_label) so nothing leaks raw
# English — the endpoint name is snake_case and section-prefixed.
_PAGE_LABELS: dict[str, str] = {
    "dashboard": "لوحة التحكم",
    "account": "حسابي",
    "account_password": "تغيير كلمة المرور",
    "auth_logout": "تسجيل الخروج",
    "users_list": "المشتركون",
    "users_overview": "المشتركون",
    "user_detail": "بطاقة المشترك",
    "users_create": "إضافة مشترك",
    "users_edit": "تعديل مشترك",
    "users_delete": "حذف مشترك",
    "users_extend": "تمديد اشتراك",
    "users_toggle": "تفعيل/تعطيل مشترك",
    "users_change_status": "تغيير حالة المشترك",
    "cards_overview": "الكروت",
    "cards_batches": "حزم الكروت",
    "cards_generate": "توليد كروت",
    "cards_checker": "فاحص الكروت",
    "cards_print": "طباعة الكروت",
    "card_offers": "عروض الكروت",
    "marketplace": "السوق",
    "admins_list": "المدراء والأدوار",
    "admins_create": "إضافة مدير",
    "admins_update": "تعديل مدير",
    "online_list": "المتصلون الآن",
    "online_disconnect": "فصل مشترك",
    "reports_home": "التقارير",
    "rep_manager_events": "تقرير أحداث المدراء",
    "backups_index": "النسخ الاحتياطية",
    "settings_page": "إعدادات النظام",
    "finance_center": "المركز المالي",
    "credit_dashboard": "شحن الرصيد",
}

# Section-noun map for the fallback: leading token of the endpoint → Arabic.
_SECTION_NOUNS: dict[str, str] = {
    "dashboard": "لوحة التحكم", "users": "المشتركون", "user": "المشتركون",
    "subscribers": "المشتركون", "cards": "الكروت", "card": "الكروت",
    "offers": "العروض", "offer": "العروض", "marketplace": "السوق",
    "batches": "الحزم", "batch": "الحزم", "admins": "المدراء",
    "admin": "الإدارة", "managers": "المدراء", "manager": "المدراء",
    "roles": "الأدوار", "online": "المتصلون الآن", "sessions": "الجلسات",
    "reports": "التقارير", "rep": "التقارير", "report": "التقارير",
    "backups": "النسخ الاحتياطية", "backup": "النسخ الاحتياطية",
    "settings": "الإعدادات", "finance": "المالية", "accounting": "المحاسبة",
    "billing": "الفوترة", "collection": "التحصيل", "plans": "الباقات",
    "plan": "الباقات", "nas": "أجهزة الشبكة", "devices": "المايكروتيك",
    "device": "المايكروتيك", "mt": "المايكروتيك", "router": "الراوتر",
    "routers": "الراوترات", "bandwidth": "السرعة", "speed": "السرعة",
    "distributors": "الموزّعون", "distributor": "الموزّعون",
    "communications": "الاتصالات", "comms": "الاتصالات",
    "notifications": "الإشعارات", "monitoring": "المراقبة",
    "audit": "سجل التدقيق", "credit": "الرصيد", "hotspot": "الهوت سبوت",
    "access": "التحكم بالدخول", "tenants": "المستأجرون", "docs": "الأدلة",
    "wg": "WireGuard", "sstp": "SSTP", "integrations": "التكاملات",
    "data": "البيانات", "migration": "الترحيل", "jobs": "المهام",
}

# Verb tokens → Arabic + a compact machine action key.
_VERB_MAP: dict[str, tuple[str, str]] = {
    "create": ("إضافة", "create"), "add": ("إضافة", "create"),
    "new": ("إضافة", "create"), "generate": ("توليد", "create"),
    "import": ("استيراد", "import"), "issue": ("إصدار", "create"),
    "edit": ("تعديل", "update"), "update": ("تعديل", "update"),
    "save": ("حفظ", "update"), "set": ("ضبط", "update"),
    "change": ("تغيير", "update"), "rename": ("إعادة تسمية", "update"),
    "toggle": ("تبديل", "update"), "apply": ("تطبيق", "update"),
    "assign": ("إسناد", "update"), "extend": ("تمديد", "update"),
    "recharge": ("شحن", "update"), "renew": ("تجديد", "update"),
    "adjust": ("تعديل", "update"), "delete": ("حذف", "delete"),
    "remove": ("حذف", "delete"), "destroy": ("حذف", "delete"),
    "purge": ("تفريغ", "delete"), "wipe": ("مسح", "delete"),
    "revoke": ("سحب", "delete"), "disconnect": ("فصل", "disconnect"),
    "kick": ("فصل", "disconnect"), "reset": ("إعادة ضبط", "reset"),
    "reboot": ("إعادة تشغيل", "reboot"), "restart": ("إعادة تشغيل", "reboot"),
    "sync": ("مزامنة", "sync"), "run": ("تشغيل", "run"),
    "export": ("تصدير", "export"), "send": ("إرسال", "send"),
    "block": ("حظر", "block"), "unblock": ("رفع الحظر", "unblock"),
    "activate": ("تفعيل", "activate"), "deactivate": ("تعطيل", "deactivate"),
    "enable": ("تفعيل", "activate"), "disable": ("تعطيل", "deactivate"),
    "approve": ("اعتماد", "approve"), "reject": ("رفض", "reject"),
    "print": ("طباعة", "print"), "upload": ("رفع", "upload"),
    "download": ("تنزيل", "download"), "connect": ("ربط", "connect"),
    "install": ("تركيب", "install"), "publish": ("نشر", "publish"),
    "verify": ("فحص", "verify"), "login": ("دخول", "login"),
    "logout": ("خروج", "logout"),
}


def page_label(endpoint_name: str) -> str:
    """Arabic label for the PAGE the endpoint belongs to. Never English."""
    name = (endpoint_name or "").strip()
    if not name:
        return "صفحة"
    if name in _PAGE_LABELS:
        return _PAGE_LABELS[name]
    return _fallback_page_label(name)


def _fallback_page_label(name: str) -> str:
    toks = name.split("_")
    noun = ""
    for t in toks:
        if t in _SECTION_NOUNS:
            noun = _SECTION_NOUNS[t]
            break
    verb = ""
    for t in toks:
        if t in _VERB_MAP:
            verb = _VERB_MAP[t][0]
            break
    if noun and verb:
        return f"{noun} — {verb}"
    if noun:
        return noun
    # Last resort: never leak raw English — a generic Arabic placeholder.
    return "صفحة الإدارة"


def _action_key(endpoint_name: str, method: str) -> str:
    """Compact machine action key stored in audit_log.action for a mutation."""
    for t in (endpoint_name or "").split("_"):
        if t in _VERB_MAP:
            return _VERB_MAP[t][1]
    return "action"


def action_label(endpoint_name: str, method: str, outcome: str) -> str:
    """Human Arabic phrase: what he DID (or TRIED to do). Used by the report."""
    name = (endpoint_name or "").strip()
    if outcome == "visit":
        return f"دخل صفحة: {page_label(name)}"
    verb = ""
    for t in name.split("_"):
        if t in _VERB_MAP:
            verb = _VERB_MAP[t][0]
            break
    page = page_label(name)
    if verb:
        return f"{verb} — {page}"
    # Unknown verb: describe by page + the raw method so it stays informative.
    return f"إجراء على: {page}"


# ── target entity resolution ────────────────────────────────────────────────
# Route arg / form field name → entity type. Path args (view_args) are the most
# reliable, checked first; then a few common form ids.
_ARG_TO_TYPE: dict[str, str] = {
    "sub_id": "subscriber", "subscriber_id": "subscriber", "uid": "subscriber",
    "card_id": "card", "cid": "card",
    "batch_id": "batch", "batch": "batch", "bid": "batch",
    "offer_id": "offer", "oid": "offer",
    "admin_id": "admin", "manager": "admin", "manager_id": "admin",
    "mid": "admin", "aid": "admin",
    "plan_id": "plan", "pid": "plan",
    "nas_id": "router", "router_id": "router", "device_id": "router",
    "rid": "router",
    "distributor_id": "distributor",
}

_TYPE_AR: dict[str, str] = {
    "subscriber": "مشترك", "card": "كرت", "batch": "حزمة", "offer": "عرض",
    "admin": "مدير", "plan": "باقة", "router": "راوتر",
    "distributor": "موزّع", "account": "حساب",
}


def resolve_target(tenant_id: int) -> tuple[str, str, str]:
    """Best-effort ``(entity_type, entity_id, entity_name)`` for the request.

    Reads path args then a few form ids; resolves the display name with one
    cheap indexed lookup. Fully defensive — any failure yields ('', '', '')."""
    try:
        va = dict(request.view_args or {})
    except Exception:  # noqa: BLE001
        va = {}
    # 1) a <username> path arg — the many card/subscriber action routes.
    uname = va.get("username")
    if isinstance(uname, str) and uname:
        return _resolve_username(tenant_id, uname)
    # 2) typed id args (path first, then form).
    sources = [va]
    try:
        if request.method in _MUTATING_METHODS and request.form:
            sources.append(request.form)
    except Exception:  # noqa: BLE001
        pass
    for src in sources:
        for key, etype in _ARG_TO_TYPE.items():
            raw = src.get(key)
            if raw in (None, ""):
                continue
            try:
                eid = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            name = _lookup_name(tenant_id, etype, eid)
            if name is not None:
                return etype, str(eid), name
    return "", "", ""


def _resolve_username(tenant_id: int, uname: str) -> tuple[str, str, str]:
    try:
        row = db().execute(
            "SELECT full_name, username FROM subscribers "
            "WHERE tenant_id=? AND username=? "
            "AND (deleted_at IS NULL OR deleted_at='') LIMIT 1",
            (tenant_id, uname)).fetchone()
        if row:
            return "subscriber", uname, (row["full_name"] or row["username"])
        crow = db().execute(
            "SELECT username FROM cards WHERE tenant_id=? AND username=? LIMIT 1",
            (tenant_id, uname)).fetchone()
        if crow:
            return "card", uname, uname
    except Exception:  # noqa: BLE001
        pass
    return "account", uname, uname


def _lookup_name(tenant_id: int, etype: str, eid: int) -> Optional[str]:
    q = {
        "subscriber": ("SELECT COALESCE(NULLIF(full_name,''), username) AS n "
                       "FROM subscribers WHERE tenant_id=? AND id=?"),
        "card": ("SELECT username AS n FROM cards WHERE tenant_id=? AND id=?"),
        "batch": ("SELECT COALESCE(NULLIF(package_name,''), batch_code) AS n "
                  "FROM card_batches WHERE tenant_id=? AND id=?"),
        "offer": ("SELECT name AS n FROM card_offers WHERE tenant_id=? AND id=?"),
        "admin": ("SELECT COALESCE(NULLIF(full_name,''), username) AS n "
                  "FROM admins WHERE id=? AND ?=?"),  # admins is tenant-less
        "plan": ("SELECT name AS n FROM access_plans WHERE tenant_id=? AND id=?"),
        "router": ("SELECT COALESCE(NULLIF(shortname,''), nasname) AS n "
                   "FROM nas WHERE tenant_id=? AND id=?"),
        "distributor": ("SELECT COALESCE(NULLIF(full_name,''), username) AS n "
                        "FROM admins WHERE id=? AND ?=?"),
    }.get(etype)
    if not q:
        return None
    try:
        if etype in ("admin", "distributor"):
            row = db().execute(q, (eid, 1, 1)).fetchone()
        else:
            row = db().execute(q, (tenant_id, eid)).fetchone()
        if row and row["n"]:
            return str(row["n"])
        # Row absent but id was well-formed — still record the id, unnamed.
        return "" if row is None else str(row["n"] or "")
    except Exception:  # noqa: BLE001
        return None


# ── param sanitization ──────────────────────────────────────────────────────
def _sanitize_params() -> dict:
    try:
        md = request.form if request.method in _MUTATING_METHODS else request.args
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    try:
        for key in md.keys():
            lk = key.lower()
            if lk in _DROP_PARAMS or any(f in lk for f in SECRET_KEY_FRAGMENTS):
                continue
            vals = md.getlist(key)
            val = vals[0] if len(vals) == 1 else vals
            if isinstance(val, str) and len(val) > 200:
                val = val[:200] + "…"
            out[key] = val
            if len(out) >= 40:
                break
    except Exception:  # noqa: BLE001
        return out
    return out


def _client_ip() -> str:
    try:
        xff = request.headers.get("X-Forwarded-For", "")
        return (xff.split(",")[0].strip() if xff else (request.remote_addr or ""))
    except Exception:  # noqa: BLE001
        return ""


def _reason(outcome: str, status: int) -> str:
    if outcome == "blocked":
        return "محظور — لا صلاحية" if status == 403 else (
            "محظور — تجاوز الحدّ" if status == 429 else "محظور")
    if outcome == "failed":
        if status >= 500:
            return "خطأ في الخادم"
        return "فشل التحقّق"
    return ""


def _handler_wrote_rich(tenant_id: int) -> bool:
    """Did this request's own handler already write a rich (non-interceptor)
    audit row? If so we skip a duplicate success row — the rich one is better."""
    seen = getattr(g, "_activity_seen_audit_id", None)
    if seen is None:
        return False
    try:
        row = db().execute(
            "SELECT 1 FROM audit_log WHERE id > ? AND tenant_id=? "
            "AND target_type != 'manager_activity' LIMIT 1",
            (int(seen), int(tenant_id))).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


# ── the interceptor itself ──────────────────────────────────────────────────
def _record_request(response) -> None:
    if not _enabled():
        return
    ep = request.endpoint or ""
    if not ep.startswith("radius."):
        return
    name = ep.split(".", 1)[1]
    if name in _SKIP_ENDPOINTS or request.method in ("HEAD", "OPTIONS"):
        return
    admin_id = session.get("admin_id")
    if not admin_id:
        return  # not an authenticated manager request

    # Real login name, never a numeric id (owner's requirement).
    actor = (session.get("admin_user") or session.get("admin_name")
             or str(admin_id))
    try:
        tid = int(getattr(g, "tenant_id", None) or session.get("tenant_id") or 1)
    except (TypeError, ValueError):
        tid = 1

    status = int(getattr(response, "status_code", 0) or 0)
    method = (request.method or "").upper()
    location = response.headers.get("Location", "") if 300 <= status < 400 else ""
    outcome, is_visit = _classify(method, status, location)

    if outcome == "success":
        if getattr(g, "_activity_outcome", "") == "noop" or _detect_noop(response):
            outcome = "noop"

    # A successful mutation the handler already richly audited (old→new diff) —
    # don't duplicate; the rich row already appears in the report.
    if outcome == "success" and _handler_wrote_rich(tid):
        return
    # High-volume plain page views are toggleable; everything else is forced.
    if is_visit and not _visits_enabled():
        return

    etype, eid, ename = resolve_target(tid)
    payload = {
        "page": page_label(name),
        "action_ar": action_label(name, method, outcome),
        "entity_type": etype,
        "entity_id": eid,
        "entity_name": ename,
        "params": _sanitize_params(),
    }
    severity = ("critical" if status >= 500
                else "warning" if outcome in ("failed", "blocked") else "info")

    audit_repo.record(
        tenant_id=tid, actor=actor,
        action=("page_visit" if is_visit else _action_key(name, method)),
        target_type="manager_activity", target_id=str(eid or ""),
        payload=payload, ip_address=_client_ip(),
        user_agent=(request.headers.get("User-Agent") or "")[:200],
        severity=severity, result_status=outcome,
        error_message=_reason(outcome, status),
        http_method=method, endpoint=name, outcome=outcome,
        status_code=status, is_visit=is_visit,
    )


def install_activity_audit(bp: Blueprint) -> None:
    """Wire the interceptor onto the radius blueprint. Registered AFTER the
    login + permission guards so a blocked request's 403 (produced by the
    guard's errorhandler) still flows through ``after_request`` and is logged
    as «محظور»."""

    @bp.before_request
    def _activity_snapshot():  # noqa: ANN202
        # Snapshot the audit high-water mark so after_request can detect a rich
        # row written by the handler. Runs only for authenticated managers; if
        # a guard aborted earlier this never runs (fine — that path isn't a
        # success, so the missing snapshot doesn't matter).
        try:
            if not _enabled() or not session.get("admin_id"):
                return None
            row = db().execute("SELECT MAX(id) AS m FROM audit_log").fetchone()
            g._activity_seen_audit_id = int(row["m"]) if row and row["m"] is not None else 0
        except Exception:  # noqa: BLE001
            g._activity_seen_audit_id = 0
        return None

    @bp.after_request
    def _activity_record(response):  # noqa: ANN202
        try:
            _record_request(response)
        except Exception:  # noqa: BLE001 — auditing must never break a request
            _LOG.warning("manager-activity audit failed", exc_info=True)
        return response


__all__ = ["install_activity_audit", "page_label", "action_label",
           "resolve_target", "note_noop"]
