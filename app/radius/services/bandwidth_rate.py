"""Profile-aware Mikrotik-Rate-Limit resolution — single source of truth.

Owner decision (June 2026, Finding-1 → option A): a bandwidth profile referenced
by a plan (``access_plans.bandwidth_id``) must REALLY drive the speed, not just
sit in the UI. Both RADIUS reply paths call :func:`plan_rate_limit` for the plan
"base" tier so they stay consistent:

  * ``services/freeradius_translator.sync_plan`` (radgroupreply / group sync)
  * ``services/policy_engine._build_accept_attrs`` (live authorize path)

Precedence is owned by the caller. Within the plan BASE tier this module's rule
is: **profile (if the plan references one and it exists) overrides the plan's own
speed fields**; otherwise fall back to the plan fields so plans without a profile
are untouched. The full cascade in the live path stays:

    active schedule  >  subscriber override  >  [ profile  or  plan default ]
"""
from __future__ import annotations

from typing import Optional

from ..core import units

# Accept either the unit CODE (units.SPEED_UNITS first col, e.g. "kbps"/"Mbps")
# or the LABEL stored by the profile form ("Kbps"/"Mbps") — case-insensitive.
_SPEED_UNIT_TO_CODE: dict[str, str] = {}
for _code, _label, _ratio in units.SPEED_UNITS:
    _SPEED_UNIT_TO_CODE[_code.lower()] = _code
    _SPEED_UNIT_TO_CODE[_label.lower()] = _code


def _speed_to_kbps(value, unit) -> int:
    code = _SPEED_UNIT_TO_CODE.get((unit or "kbps").strip().lower(), "kbps")
    try:
        return int(units.to_base(value or 0, code, "speed"))
    except Exception:  # noqa: BLE001 — never break a RADIUS reply on a bad unit
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


def profile_rate_kbps(profile) -> tuple[int, int]:
    """Return ``(down_kbps, up_kbps)`` for a BandwidthProfile, honoring its units."""
    down = _speed_to_kbps(getattr(profile, "rate_down", 0), getattr(profile, "rate_down_unit", "kbps"))
    up = _speed_to_kbps(getattr(profile, "rate_up", 0), getattr(profile, "rate_up_unit", "kbps"))
    return down, up


def resolve_plan_profile(plan):
    """Return the BandwidthProfile a plan references, or None.

    Lazy import of the repo keeps this module import-cycle-free (the translator
    and policy_engine both import it very early)."""
    bw_id = getattr(plan, "bandwidth_id", None)
    if not bw_id:
        return None
    try:
        from ..db.repos import bandwidth_repo
        return bandwidth_repo.get(plan.tenant_id, int(bw_id))
    except Exception:  # noqa: BLE001 — missing profile must not break auth
        return None


def plan_rate_limit(plan) -> Optional[str]:
    """Mikrotik-Rate-Limit string for a plan's BASE tier, profile-aware.

    Profile wins over the plan's own fields when the plan references one that
    exists (its raw burst string if set, else ``up_k/down_k`` from its rates).
    Falls back to the plan's ``burst_raw`` / ``speed_*_kbps``. ``None`` when
    neither yields a rate (caller then emits no Mikrotik-Rate-Limit).
    """
    profile = resolve_plan_profile(plan)
    if profile is not None:
        burst = (getattr(profile, "burst", "") or "").strip()
        if burst:
            return burst
        down, up = profile_rate_kbps(profile)
        if down or up:
            return f"{up}k/{down}k"
    if plan.speed_down_kbps or plan.speed_up_kbps:
        return plan.burst_raw or f"{plan.speed_up_kbps}k/{plan.speed_down_kbps}k"
    return None


# «تقسيم السرعة على الأجهزة»: أدنى حصّة للجهاز الواحد بعد التقسيم — لئلّا يهبط
# مشترك لسرعة غير عمليّة حين تتصل أجهزة كثيرة. (256kbps ≈ حدّ أدنى معقول.)
SPLIT_MIN_KBPS = 256


def _split_dirs(sub) -> tuple[bool, bool]:
    """(divide_download, divide_upload) لهذا المشترك — الافتراضيّ (False, False).
    المصدر: علمَا «التوزيع المتساوي» equal_share_download/upload (المعنى: تقاسم
    السرعة الفعّالة بالتساوي بين الأجهزة الحيّة)، يُنفَّذان عبر اللوحة+CoA."""
    return (
        bool(getattr(sub, "equal_share_download", 0)),
        bool(getattr(sub, "equal_share_upload", 0)),
    )


def _live_device_count(tenant_id: int, username: str) -> int:
    """عدد جلسات المشترك المفتوحة الآن (≥1 دائمًا للقسمة الآمنة).

    المصدر = radacct المفتوحة (acctstoptime IS NULL) — **نفس المصدر الذي يستهدفه
    CoA** (find_all_nas_for_sessions في مسار السرعة المؤقتة الذي يعمل فعليًّا)،
    فيتطابق «عدد الأجهزة المقسوم عليه» مع «الجلسات التي سيُدفع لها المعدّل».

    (البق السابق: count_real_sessions([username]) يعدّ كم اسمًا من القائمة
    المُمرَّرة حقيقيّ — أي 1 دائمًا لاسم واحد → القسمة لم تحدث قطّ في الإنفاذ.)
    """
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS n FROM radacct "
            "WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL",
            (int(tenant_id), str(username)),
        ).fetchone()
        return max(1, int(row["n"] if row else 1))
    except Exception:  # noqa: BLE001 — never break a rate computation
        return 1


def _apply_device_split(tenant_id, username, sub, down_k: int, up_k: int) -> tuple[int, int]:
    """اقسم (down_k, up_k) على عدد الأجهزة الحيّة حسب علمَي التقسيم. عند جهاز
    واحد (أو تعطيل التقسيم) تُعاد القيم كما هي — فالسلوك الافتراضيّ سليم."""
    down_split, up_split = _split_dirs(sub)
    if not (down_split or up_split):
        return down_k, up_k
    n = _live_device_count(tenant_id, username)
    if n <= 1:
        return down_k, up_k
    if down_split and down_k:
        down_k = max(SPLIT_MIN_KBPS, down_k // n)
    if up_split and up_k:
        up_k = max(SPLIT_MIN_KBPS, up_k // n)
    return down_k, up_k


def _base_rate_kbps(tenant_id, username, *, at=None):
    """(down_kbps, up_kbps, sub) عبر الكاسكيد قبل تقسيم الأجهزة. sub=None لو مجهول."""
    try:
        from ..db.repos import operations_repo, plans_repo, subscribers_repo
    except Exception:  # noqa: BLE001
        return (0, 0, None)
    sub = subscribers_repo.get_subscriber(tenant_id, username)
    if not sub:
        return (0, 0, None)
    plan = None
    if getattr(sub, "plan_id", None):
        try:
            plan = plans_repo.get_plan(tenant_id, sub.plan_id)
        except Exception:  # noqa: BLE001
            plan = None
    rule = operations_repo.resolve_effective_bandwidth_schedule(
        tenant_id,
        subscriber_username=username,
        card_batch_id=getattr(sub, "card_batch_id", None),
        plan_id=(plan.id if plan else getattr(sub, "plan_id", None)),
        at=at,
    )
    if rule:
        return (int(rule.get("speed_down_kbps") or 0),
                int(rule.get("speed_up_kbps") or 0), sub)
    if getattr(sub, "bandwidth_control_enabled", False) and (
        getattr(sub, "download_speed_kbps", 0) or getattr(sub, "upload_speed_kbps", 0)
    ):
        return (int(sub.download_speed_kbps or 0),
                int(sub.upload_speed_kbps or 0), sub)
    if plan:
        profile = resolve_plan_profile(plan)
        if profile is not None:
            down, up = profile_rate_kbps(profile)
            if down or up:
                return (down, up, sub)
        return (int(getattr(plan, "speed_down_kbps", 0) or 0),
                int(getattr(plan, "speed_up_kbps", 0) or 0), sub)
    return (0, 0, sub)


def effective_rate_kbps(tenant_id: int, username: str, *, at=None) -> tuple[int, int]:
    """(down_kbps, up_kbps) الفعّالان الآن بعد كامل الكاسكيد **وتقسيم الأجهزة**.
    (0, 0) لو مجهول. مصدر رقميّ موحّد للتقسيم/CoA."""
    down_k, up_k, sub = _base_rate_kbps(tenant_id, username, at=at)
    if sub is None:
        return (0, 0)
    return _apply_device_split(tenant_id, username, sub, down_k, up_k)


def _active_speed_factors(tenant_id: int) -> Optional[dict]:
    """المعامل النشِط للتحكّم بالسرعة (من سياسة طُبِّقت حيًّا عبر مركز السرعة) —
    {multiplier, overrides:{plan_id:{down,up}}, profile_ids}، أو None حين لا سياسة
    نشطة (= السلوك الطبيعيّ 100%). محصّن: أيّ خطأ → None (لا تعديل)."""
    try:
        from ..db.repos import tenants_repo
        raw = tenants_repo.get_setting(int(tenant_id), "speed.active_factors", "")
        if not raw:
            return None
        import json as _json
        d = _json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _apply_active_speed_factor(tenant_id: int, plan_id, rate_str: str) -> str:
    """يضرب معدّل «Uk/Dk» بمعامل السياسة النشطة لباقة المشترك (عامّ أو لكلّ باقة).
    يُبقي ذيل الـburst كما هو. محصّن: يعيد المعدّل بلا تغيير عند غياب سياسة/خطأ."""
    d = _active_speed_factors(tenant_id)
    if not d or not rate_str:
        return rate_str
    try:
        mult = float(d.get("multiplier") or 1.0)
        profile_ids = d.get("profile_ids") or []
        if profile_ids and plan_id is not None:
            if int(plan_id) not in {int(x) for x in profile_ids}:
                return rate_str          # الباقة خارج نطاق السياسة
        ov = (d.get("overrides") or {}).get(str(plan_id)) if plan_id is not None else None
        down_f = float(ov.get("down", mult)) if isinstance(ov, dict) else mult
        up_f = float(ov.get("up", mult)) if isinstance(ov, dict) else mult
        down_f = max(0.0, min(5.0, down_f))
        up_f = max(0.0, min(5.0, up_f))
        if abs(down_f - 1.0) < 1e-9 and abs(up_f - 1.0) < 1e-9:
            return rate_str              # 100% = لا تغيير
        parts = rate_str.strip().split(" ", 1)
        main = parts[0]
        tail = (" " + parts[1]) if len(parts) > 1 else ""
        if "/" not in main:
            return rate_str
        up_s, down_s = main.split("/", 1)
        if not (up_s.endswith("k") and down_s.endswith("k")):
            return rate_str
        up_k = int(int(up_s[:-1]) * up_f)
        down_k = int(int(down_s[:-1]) * down_f)
        return f"{up_k}k/{down_k}k{tail}"
    except Exception:  # noqa: BLE001 — fail-open: لا نكسر تخصيص السرعة
        return rate_str


def _card_effective_rate_limit(tenant_id: int, card, *, at=None) -> str:
    """Effective Mikrotik-Rate-Limit for a live CARD session, same cascade the
    auth path uses for cards (policy_engine._card_to_subscriber + _build_accept_
    attrs): active schedule (by card batch/plan) > per-card speed override >
    plan/profile base. Then the tenant's active speed factor. '' when unknown.

    Cards are resolved authoritatively from the ``cards`` table because a card's
    subscriber-mirror row can be absent, or carry a stale/empty rate — which is
    exactly why the scheduler/CoA computed '' and applied to 0 card sessions.
    """
    try:
        from ..db.repos import operations_repo, plans_repo
    except Exception:  # noqa: BLE001
        return ""
    plan_id = getattr(card, "plan_id", None)
    plan = None
    if plan_id:
        try:
            plan = plans_repo.get_plan(tenant_id, plan_id)
        except Exception:  # noqa: BLE001
            plan = None
    rule = operations_repo.resolve_effective_bandwidth_schedule(
        tenant_id,
        subscriber_username=getattr(card, "username", "") or "",
        card_batch_id=getattr(card, "batch_id", None),
        plan_id=(plan.id if plan else plan_id),
        at=at,
    )
    result = ""
    if rule:
        result = (f"{int(rule.get('speed_up_kbps') or 0)}k/"
                  f"{int(rule.get('speed_down_kbps') or 0)}k")
    else:
        down = int(getattr(card, "card_speed_down_kbps", 0) or 0)
        up = int(getattr(card, "card_speed_up_kbps", 0) or 0)
        if down and up:                       # per-card override wins over plan
            result = f"{up}k/{down}k"
        elif plan:
            result = plan_rate_limit(plan) or ""
    return _apply_active_speed_factor(tenant_id, plan_id, result) if result else result


def effective_rate_limit(tenant_id: int, username: str, *, at=None) -> str:
    """The Mikrotik-Rate-Limit a live session SHOULD have right now, by the full
    cascade — identical ordering to policy_engine._build_accept_attrs:

        active schedule (time window)  >  subscriber override  >  plan/profile base

    **مصدر الحقيقة الوحيد** ويطبّق «تقسيم السرعة على الأجهزة» كخطوة أخيرة، فيتّسق
    مسار authorize وعامل الجدولة وCoA على نفس القيمة المقسَّمة (لا يُلغي أحدهم
    الآخر). حين التقسيم معطّل (الافتراضيّ) يعود السلوك كما كان تمامًا (يشمل سلاسل
    burst للباقات). Returns "" if unknown.

    Card sessions (بطايق) resolve through the card cascade above so the speed
    change (manual + scheduled) reaches connected CARD users exactly like
    subscribers — not just subscriber accounts.
    """
    try:
        from ..db.repos import operations_repo, plans_repo, subscribers_repo
    except Exception:  # noqa: BLE001
        return ""
    # Card-first: a card is authoritative in the cards table (disjoint username
    # space); the subscriber mirror may lack its real plan/override.
    try:
        from ..db.repos import cards_repo
        _card = cards_repo.get_card_by_username(tenant_id, username)
    except Exception:  # noqa: BLE001
        _card = None
    if _card is not None:
        return _card_effective_rate_limit(tenant_id, _card, at=at)
    sub = subscribers_repo.get_subscriber(tenant_id, username)
    if not sub:
        return ""
    plan_id = getattr(sub, "plan_id", None)
    result = ""
    # التقسيم مفعَّل → معدّل رقميّ مقسَّم (له الأولويّة على سلاسل burst).
    if any(_split_dirs(sub)):
        down_k, up_k = effective_rate_kbps(tenant_id, username, at=at)
        if down_k or up_k:
            result = f"{up_k}k/{down_k}k"
    if not result:
        plan = None
        if plan_id:
            try:
                plan = plans_repo.get_plan(tenant_id, sub.plan_id)
            except Exception:  # noqa: BLE001
                plan = None
        rule = operations_repo.resolve_effective_bandwidth_schedule(
            tenant_id,
            subscriber_username=username,
            card_batch_id=getattr(sub, "card_batch_id", None),
            plan_id=(plan.id if plan else plan_id),
            at=at,
        )
        if rule:
            result = (f"{int(rule.get('speed_up_kbps') or 0)}k/"
                      f"{int(rule.get('speed_down_kbps') or 0)}k")
        elif getattr(sub, "bandwidth_control_enabled", False) and (
            getattr(sub, "download_speed_kbps", 0) or getattr(sub, "upload_speed_kbps", 0)
        ):
            result = f"{sub.upload_speed_kbps}k/{sub.download_speed_kbps}k"
        elif plan:
            result = plan_rate_limit(plan) or ""
    # الخطوة الأخيرة: معامل التحكّم بالسرعة النشِط (وضع عامّ/لكلّ باقة) — يُطبَّق على
    # كامل السلسلة فيتّسق authorize + العامل + CoA على القيمة المخفَّضة نفسها.
    return _apply_active_speed_factor(tenant_id, plan_id, result) if result else result


def live_apply_enabled() -> bool:
    """Whether live CoA application is the operative path.

    Owner decision (June 2026): schedules/profiles must actually take effect on
    live sessions, so this defaults **ON**. Set HOBERADIUS_ENABLE_LIVE_SPEED_APPLY
    to 0/false/no/off to force dry-run only (kill-switch for the whole live path:
    manual «apply now», profile apply, and the auto-schedule worker)."""
    try:
        from ..core import env_settings
        raw = env_settings.env("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", "1")
    except Exception:  # noqa: BLE001
        raw = "1"
    return str("1" if raw is None else raw).strip().lower() not in (
        "0", "false", "no", "off")


__all__ = ["plan_rate_limit", "profile_rate_kbps", "resolve_plan_profile",
           "effective_rate_limit", "live_apply_enabled"]
