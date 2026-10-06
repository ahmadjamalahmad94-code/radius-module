"""Strict validation of ONE model proposal (catalog ops-v2).

Order (executor_checklist):
  1. it is a JSON object; NO forbidden key anywhere (deep scan) → the WHOLE
     proposal is rejected, values are never echoed;
  2. JSON-schema (catalog ``output_schema``; level 3 = ``PLAN_SCHEMA`` + each
     step against its action's schema, ``$stepN.field`` refs checked);
  3. every plan_id / offer_id / existing username was ISSUED in a CHOICES
     list (or level-4 event) of THIS conversation, and an existing subscriber
     is inside the admin's scope (distributor / own-subscribers rule);
  4. normalisation + caps: Arabic digits, Asia/Gaza local time → UTC,
     durations → minutes (calendar months), ≤ 1 year per operation, temporary
     speed 1–1440 min and 0|64..1,000,000 kbps, selling ≥ wholesale, card
     batch caps;
  5. the logged-in admin's permission (same decision as the API guard).

``prepare_step`` is also what the executor runs right before each API call
(re-validation at confirm time, after ``$step`` references are resolved).

ops-v2 (SPEC_DATA_v3): every object carries ``message`` (the admin-facing
text; never used for a decision and not part of the hash). A proposal from a
round-1/2 model (ops-v1 contract, no ``message``) is accepted with its
``summary_ar`` as the message. INFO actions (``card_batch_status``,
``subscriber_info``, ``online_sessions``) are read-only: schema + issued ids +
scope + permission, then ``info.run`` answers with a RESULT line.
"""
from __future__ import annotations
from app.i18n_text import _tr

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from . import catalog, store, units
from .schema_check import validate as schema_validate

PERMISSION_CODES = frozenset({"missing_permission", "out_of_scope"})


@dataclass
class Violation:
    code: str
    message: str
    path: str = "$"

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "path": self.path}


class ProposalRejected(Exception):
    def __init__(self, violations: list[Violation]):
        super().__init__(violations[0].message if violations else "rejected")
        self.violations = violations

    @property
    def forbidden(self) -> bool:
        return bool(self.violations) and all(v.code in PERMISSION_CODES for v in self.violations)


@dataclass
class PreparedStep:
    action: str
    fields: dict
    method: str = ""
    path: str = ""
    body: Optional[dict] = None
    idempotent: bool = False
    needs_password: bool = False
    executable: bool = True
    deferred: bool = False           # waits for a $step reference
    display: dict = field(default_factory=dict)
    refs: dict = field(default_factory=dict)


@dataclass
class Validated:
    kind: str                         # control | lookup | info | action | plan
    action: str
    proposal: dict
    proposal_hash: str
    steps: list = field(default_factory=list)


# ─────────────────────────── helpers ────────────────────────────

def _deep_forbidden(obj: Any, path: str = "$") -> list[Violation]:
    out: list[Violation] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if catalog.is_forbidden_key(str(k)):
                out.append(Violation("forbidden_key",
                                     _tr("حقل ممنوع في المقترح — الأسرار والحقول الخام لا تمرّ عبر المساعد."),
                                     p))
            out += _deep_forbidden(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _deep_forbidden(v, f"{path}[{i}]")
    return out


def _schema(instance: Any, schema: dict, path: str) -> list[Violation]:
    return [Violation("schema", msg, path + p[1:]) for p, msg in schema_validate(instance, schema)]


def canonical_hash(cid: str, proposal: dict) -> str:
    """Hash of what would EXECUTE (action + fields / steps) bound to the
    conversation — ``summary_ar`` wording does not change it, any field does."""
    core = {"conversation": cid, "action": proposal.get("action")}
    if proposal.get("action") == "plan":
        core["steps"] = proposal.get("steps")
    else:
        core["fields"] = proposal.get("fields")
    raw = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _refs_of(fields: dict, step_no: int, actions_before: list[str],
             path: str) -> tuple[dict, list[Violation]]:
    refs, errs = {}, []
    for key, val in fields.items():
        if not (isinstance(val, str) and val.startswith("$")):
            continue
        m = catalog.REF_RE.match(val)
        if not m or key not in catalog.REF_FIELDS:
            errs.append(Violation("bad_ref", _tr("مرجع خطوة غير صالح."), f"{path}.{key}"))
            continue
        n, out_field = int(m.group(1)), m.group(2)
        if n >= step_no or n < 1:
            errs.append(Violation("bad_ref", _tr("المرجع يجب أن يشير إلى خطوة سابقة."), f"{path}.{key}"))
            continue
        if out_field != key or out_field not in catalog.REF_OUTPUTS.get(actions_before[n - 1], ()):
            errs.append(Violation("bad_ref", _tr("الخطوة المشار إليها لا تُنتج هذا الحقل."), f"{path}.{key}"))
            continue
        refs[key] = (n, out_field)
    return refs, errs


# ─────────────────────────── top level ────────────────────────────

def with_message(proposal: Any) -> Any:
    """ops-v1 compatibility: a round-1/2 model has no ``message`` — its Arabic
    ``summary_ar`` is shown instead (the hash never covers either)."""
    if isinstance(proposal, dict) and "message" not in proposal:
        summ = proposal.get("summary_ar")
        if isinstance(summ, str) and summ.strip():
            return {**proposal, "message": summ.strip()[:400]}
    return proposal


def validate_proposal(conv: dict, proposal: Any, *, mode: str = "execute") -> Validated:
    if not isinstance(proposal, dict):
        raise ProposalRejected([Violation("not_object", _tr("المقترح يجب أن يكون كائن JSON واحدًا."))])
    bad = _deep_forbidden(proposal)
    if bad:
        raise ProposalRejected(bad)
    proposal = with_message(proposal)
    action = proposal.get("action")
    if action == "plan":
        errs = _schema(proposal, catalog.PLAN_SCHEMA, "$")
        if errs:
            raise ProposalRejected(errs)
        return _validate_plan(conv, proposal, mode)
    root = catalog.output_schema()
    errs = _schema(proposal, root, "$")
    if errs:
        raise ProposalRejected(errs)
    phash = canonical_hash(conv["id"], proposal)
    if action in catalog.CONTROL_ACTIONS:
        return Validated("control", action, proposal, phash)
    if action in catalog.LOOKUP_ACTIONS:
        from .context import action_permitted
        if not action_permitted(action):
            raise ProposalRejected([_perm_violation(action, "$")])
        return Validated("lookup", action, proposal, phash)
    if action in catalog.INFO_ACTIONS:
        _check_info(conv, action, dict(proposal.get("fields") or {}))
        return Validated("info", action, proposal, phash)
    step = prepare_step(conv, action, dict(proposal["fields"]), path="$.fields", mode=mode)
    return Validated("action", action, proposal, phash, [step])


def _validate_plan(conv: dict, proposal: dict, mode: str) -> Validated:
    steps_in = proposal["steps"]
    errs: list[Violation] = []
    prepared: list[PreparedStep] = []
    actions = [s["action"] for s in steps_in]
    for i, step in enumerate(steps_in, start=1):
        path = f"$.steps[{i - 1}].fields"
        fields = dict(step["fields"])
        refs, ref_errs = _refs_of(fields, i, actions, path)
        errs += ref_errs
        if ref_errs:
            continue
        probe = {k: (catalog.REF_FIELDS[k] if k in refs else v) for k, v in fields.items()}
        s_errs = _schema(probe, catalog.action_def(step["action"]), path)
        if s_errs:
            errs += s_errs
            continue
        try:
            prepared.append(prepare_step(conv, step["action"], fields, path=path, refs=refs,
                                         mode=mode))
        except ProposalRejected as e:
            errs += e.violations
    if errs:
        raise ProposalRejected(errs)
    return Validated("plan", "plan", proposal, canonical_hash(conv["id"], proposal), prepared)


def _check_info(conv: dict, action: str, fields: dict) -> None:
    """Read-only INFO action: permission + every record id issued in THIS
    conversation (+ the subscriber inside the admin's scope)."""
    from .context import action_permitted
    tid = int(conv["tenant_id"])
    errs: list[Violation] = []
    if not action_permitted(action, fields):
        errs.append(_perm_violation(action, "$.fields"))
    if "batch_id" in fields and not store.is_issued(conv["id"], tid, "batch", fields["batch_id"]):
        errs.append(Violation("invented_id",
                              _tr("رقم الحزمة لم يَرِد في قائمة اختيار من هذه المحادثة."),
                              "$.fields.batch_id"))
    if "username" in fields:
        uname = str(fields["username"])
        if not store.is_issued(conv["id"], tid, "subscriber", uname):
            errs.append(Violation("invented_id",
                                  _tr("اسم المشترك لم يَرِد في نتيجة بحث من هذه المحادثة."),
                                  "$.fields.username"))
        else:
            from ....api.access_control import subscriber_in_scope
            if not subscriber_in_scope(username=uname):
                errs.append(Violation("out_of_scope", _tr("هذا المشترك ليس ضمن نطاقك."),
                                      "$.fields.username"))
    if errs:
        raise ProposalRejected(errs)


def _perm_violation(action: str, path: str) -> Violation:
    key = catalog.ACTION_PERMISSION.get(action, ("", "", ""))[0]
    return Violation("missing_permission",
                     _tr("لا تملك صلاحية هذا الإجراء (%(k)s).", k=key), path)


# ─────────────────────────── per action ────────────────────────────

def prepare_step(conv: dict, action: str, fields: dict, *, path: str = "$.fields",
                 refs: Optional[dict] = None, mode: str = "execute") -> PreparedStep:
    """Validate + normalise one executable action. Fields named in ``refs``
    still hold ``$stepN.x`` (unresolved): their record checks are deferred."""
    from .context import action_permitted
    refs = refs or {}
    errs: list[Violation] = []
    if refs == {}:
        errs += _schema(fields, catalog.action_def(action), path)
        if errs:
            raise ProposalRejected(errs)
    tid = int(conv["tenant_id"])
    if not action_permitted(action, fields):
        errs.append(_perm_violation(action, path))

    # identities: issued in THIS conversation + inside the admin's scope
    for key, kind in (("plan_id", "plan"), ("offer_id", "offer")):
        if key in fields and key not in refs:
            if not store.is_issued(conv["id"], tid, kind, fields[key]):
                errs.append(Violation("invented_id",
                                      _tr("المعرّف لم يَرِد في قائمة اختيار من هذه المحادثة."),
                                      f"{path}.{key}"))
    if action != "create_subscriber" and "username" in fields and "username" not in refs:
        uname = str(fields["username"])
        if not store.is_issued(conv["id"], tid, "subscriber", uname):
            errs.append(Violation("invented_id",
                                  _tr("اسم المشترك لم يَرِد في نتيجة بحث من هذه المحادثة."),
                                  f"{path}.username"))
        else:
            from ....api.access_control import subscriber_in_scope
            if not subscriber_in_scope(username=uname):
                errs.append(Violation("out_of_scope",
                                      _tr("هذا المشترك ليس ضمن نطاقك."), f"{path}.username"))
    if errs:
        raise ProposalRejected(errs)

    deferred = bool(refs)
    builder = _BUILDERS[action]
    try:
        step = builder(conv, fields, refs, path, mode)
    except units.UnitError as e:
        raise ProposalRejected([Violation(e.code, e.message, path)]) from e
    step.deferred = deferred
    step.refs = dict(refs)
    return step


def _v(code: str, message: str, path: str) -> ProposalRejected:
    return ProposalRejected([Violation(code, message, path)])


def _copy(fields: dict, keys: tuple[str, ...]) -> dict:
    return {k: fields[k] for k in keys if k in fields}


_SUB_OPTIONAL = ("full_name", "email", "mac_lock", "device_count", "device_limit_mode",
                 "service_type", "user_type", "login_without_password", "custom_price",
                 "static_ip", "address", "city", "national_id", "remark")


def _b_create_subscriber(conv, f, refs, path, mode):
    tid = int(conv["tenant_id"])
    body = {"username": f["username"], **_copy(f, _SUB_OPTIONAL)}
    body["plan_id"] = f["plan_id"]
    if "mobile" in f:
        body["mobile"] = units.latin_digits(f["mobile"])
    now = units.utcnow()
    display: dict = {}
    if "duration" in f:
        minutes = units.enforce_cap(units.duration_minutes(f["duration"], tenant_id=tid,
                                                           anchor_utc=now), tid)
        exp = now + timedelta(minutes=minutes)
        body["expire_at"] = units.iso_z(exp)
        display["expire_local"] = units.local_text(exp, tid)
    elif "until_local" in f:
        exp = units.local_to_utc(f["until_local"], tid)
        if exp <= now:
            raise _v("past_datetime", _tr("وقت الانتهاء المطلوب مضى."), f"{path}.until_local")
        units.enforce_cap(int((exp - now).total_seconds() // 60), tid)
        body["expire_at"] = units.iso_z(exp)
        display["expire_local"] = units.local_text(exp, tid)
    elif f.get("no_expiry") is True:
        body["expire_at"] = None
        display["expire_local"] = "no_expiry"
    else:
        from ...core.system_config import create_without_expiry_mode
        display["expire_local"] = "server_default:" + str(create_without_expiry_mode(tid))
    lwp = bool(f.get("login_without_password"))
    return PreparedStep("create_subscriber", f, "POST", "/accounts", body,
                        needs_password=not lwp, display=display)


def _anchor(conv, username: str):
    from ...db.repos import subscribers_repo
    sub = subscribers_repo.get_subscriber(int(conv["tenant_id"]), username)
    now = units.utcnow()
    exp = getattr(sub, "expire_at", None) if sub is not None else None
    return (max(exp, now) if exp else now), exp


def _b_renew(conv, f, refs, path, mode):
    tid = int(conv["tenant_id"])
    body: dict = {"charge_mode": f["charge_mode"]}
    if "amount" in f:
        body["amount"] = f["amount"]
    if "notes" in f:
        body["notes"] = f["notes"]
    display: dict = {}
    uname = f["username"]
    if f["mode"] == "duration" and f["duration"]["unit"] != "months":
        minutes = units.enforce_cap(units.duration_minutes(f["duration"], tenant_id=tid), tid)
        body.update(mode="duration", minutes=minutes)
        display["minutes"] = minutes
    elif "username" in refs:
        # months / until need the live expiry of a subscriber created earlier
        # in this plan — computed when the step runs.
        body.update(mode="expire_at")
    else:
        anchor, _cur = _anchor(conv, uname)
        if f["mode"] == "duration":
            end = units.add_calendar_months(anchor, int(f["duration"]["value"]), tid)
        else:
            end = units.local_to_utc(f["until_local"], tid)
            if end <= anchor:
                raise _v("until_not_after_current",
                         _tr("التاريخ المطلوب ليس بعد انتهاء الاشتراك الحاليّ."),
                         f"{path}.until_local")
        minutes = units.enforce_cap(int(round((end - anchor).total_seconds() / 60)), tid)
        body.update(mode="expire_at", expire_at=units.iso_z(end))
        display.update(minutes=minutes, new_expire_local=units.local_text(end, tid))
    return PreparedStep("renew_or_extend_subscriber", f, "POST",
                        f"/accounts/{uname}/extend", body, idempotent=True, display=display)


def _b_change_plan(conv, f, refs, path, mode):
    if "plan_id" not in refs and "username" not in refs:
        from .context import plan_direction
        direction = plan_direction(str(f["username"]), int(f["plan_id"]))
        allowed = {"lower": "lower_", "higher": "higher_", "neutral": "neutral_"}[direction]
        if not str(f["policy"]).startswith(allowed):
            raise _v("policy_direction",
                     _tr("سياسة التغيير لا تناسب اتجاه الباقة الجديدة (%(d)s).", d=direction),
                     f"{path}.policy")
    return PreparedStep("change_subscriber_plan", f, "POST",
                        f"/accounts/{f['username']}/change-plan",
                        {"plan_id": f["plan_id"], "policy": f["policy"]}, idempotent=True)


def _b_temp_speed(conv, f, refs, path, mode):
    tid = int(conv["tenant_id"])
    uname = f["username"]
    if f["operation"] == "cancel":
        return PreparedStep("temporary_speed", f, "POST",
                            f"/accounts/{uname}/temp-speed/cancel", {})
    down = units.temp_speed_kbps(f["down_kbps"])
    up = units.temp_speed_kbps(f["up_kbps"])
    if down == 0 and up == 0:
        raise _v("speed_both_zero", _tr("لا يكون الاتجاهان بلا حدّ معًا."), path)
    if "duration" in f:
        minutes = int(f["duration"]["value"]) * (60 if f["duration"]["unit"] == "hours" else 1)
    else:
        end = units.local_to_utc(f["until_local"], tid)
        secs = (end - units.utcnow()).total_seconds()
        minutes = int(-(-secs // 60)) if secs > 0 else 0
    minutes = units.temp_speed_minutes(minutes)
    body = {"username": uname, "down_kbps": down, "up_kbps": up,
            "duration_minutes": minutes}
    return PreparedStep("temporary_speed", f, "POST", "/sessions/temp-speed", body,
                        display={"down": units.format_speed(down), "up": units.format_speed(up),
                                 "minutes": minutes})


def _b_status(action, endpoint):
    def build(conv, f, refs, path, mode):
        return PreparedStep(action, f, "POST", f"/accounts/{f['username']}/{endpoint}", {})
    return build


_PLAN_COPY = ("name", "service_type", "plan_type", "speed_down_kbps", "speed_up_kbps",
              "speed_unlimited", "price", "validity_days", "quota_total_mb", "quota_daily_mb",
              "quota_monthly_mb", "concurrent_sessions", "allowed_devices_count", "priority",
              "enabled", "description")


def _b_create_plan(conv, f, refs, path, mode):
    body = _copy(f, _PLAN_COPY)
    if not f.get("speed_unlimited") and (int(f.get("speed_down_kbps") or 0) == 0
                                         or int(f.get("speed_up_kbps") or 0) == 0):
        raise _v("speed_zero", _tr("السرعة صفر مرفوضة إلّا مع «بلا حدّ للسرعة»."), path)
    if "duration" in f:
        d = f["duration"]
        # a plan DEFINES a period: a month is the codebase's 30-day plan period
        per = {"minutes": 1, "hours": 60, "days": 1440, "months": 43200}[d["unit"]]
        body["duration_minutes"] = int(d["value"]) * per
    display = {}
    if "speed_down_kbps" in f:
        display = {"down": units.format_speed(f.get("speed_down_kbps", 0)),
                   "up": units.format_speed(f.get("speed_up_kbps", 0))}
    if "quota_total_mb" in f:
        display["quota"] = units.format_quota(f["quota_total_mb"])
    return PreparedStep("create_plan", f, "POST", "/profiles", body, display=display)


def _b_create_offer(conv, f, refs, path, mode):
    if float(f["selling"]) < float(f["wholesale"]):
        raise _v("selling_below_wholesale", _tr("سعر البيع يجب ألا يقلّ عن سعر الجملة."),
                 f"{path}.selling")
    d = f["duration"]
    minutes = int(d["value"]) * {"minutes": 1, "hours": 60, "days": 1440}[d["unit"]]
    body = {"name": f["name"], "plan_id": f["plan_id"], "duration_minutes": minutes,
            "wholesale": f["wholesale"], "selling": f["selling"], "visible_admin_ids": [],
            **_copy(f, ("device_count", "device_limit_mode", "equal_share_download",
                        "equal_share_upload", "notes"))}
    return PreparedStep("create_offer", f, "POST", "/cards/offers", body,
                        display={"minutes": minutes})


_BATCH_COPY = ("count", "package_name", "username_length", "include_batch_number",
               "password_length", "login_without_password", "password_generation_type",
               "time_value", "time_unit", "device_count", "device_limit_mode",
               "price_per_card", "total_price", "total_quota_mb", "notes")


def _b_card_batch(conv, f, refs, path, mode):
    tid = int(conv["tenant_id"])
    from ..cards import hard_max_cards_per_batch, max_cards_per_batch
    cap = int(hard_max_cards_per_batch(tid))
    soft = int(max_cards_per_batch(tid) or 0)
    if soft:
        cap = min(cap, soft)
    if int(f["count"]) > cap:
        raise _v("count_over_cap", _tr("عدد الكروت يتجاوز الحدّ (%(c)s) — قسّمها على دفعات.", c=cap),
                 f"{path}.count")
    prefix = units.latin_digits(f.get("username_prefix", "")).strip()
    suffix = units.latin_digits(f.get("username_suffix", "")).strip()
    if "username_length" in f and len(prefix) + len(suffix) >= int(f["username_length"]):
        raise _v("username_length_too_short",
                 _tr("البادئة واللاحقة لا تتركان خانة عشوائيّة ضمن الطول الكلّيّ."),
                 f"{path}.username_length")
    body = _copy(f, _BATCH_COPY)
    if prefix:
        body["username_prefix"] = prefix
    if suffix:
        body["username_suffix"] = suffix
    if f["source"] == "offer":
        # Q2/Q7: generating FROM AN OFFER (wallet charge + price lock) is web-only
        # in v1 — draft / hand-off only.
        body["offer_id"] = f["offer_id"]
        return PreparedStep("create_card_batch", f, "GET", f"/cards/offers/{f['offer_id']}/use",
                            body, executable=False)
    body["plan_id"] = f["plan_id"]
    return PreparedStep("create_card_batch", f, "POST", "/cards/generate", body, idempotent=True)


_BUILDERS = {
    "create_subscriber": _b_create_subscriber,
    "renew_or_extend_subscriber": _b_renew,
    "change_subscriber_plan": _b_change_plan,
    "temporary_speed": _b_temp_speed,
    "suspend_subscriber": _b_status("suspend_subscriber", "disable"),
    "enable_subscriber": _b_status("enable_subscriber", "enable"),
    "create_plan": _b_create_plan,
    "create_offer": _b_create_offer,
    "create_card_batch": _b_card_batch,
}


def resolve_refs(fields: dict, refs: dict, outputs: dict[int, dict]) -> dict:
    """Replace ``$stepN.x`` with step N's real output (KeyError → missing)."""
    out = dict(fields)
    for key, (n, out_field) in refs.items():
        out[key] = outputs[n][out_field]
    return out


__all__ = ["Violation", "ProposalRejected", "PreparedStep", "Validated", "validate_proposal",
           "with_message",
           "prepare_step", "canonical_hash", "resolve_refs", "PERMISSION_CODES"]
