"""Levels 1–3 of the operations assistant (deterministic, owner-approved).

* Level 1 — ``draft``: the validated proposal becomes a prefilled API payload
  for the form; nothing is written (the admin presses Save himself).
* Level 2 — one action after an explicit confirmation carrying the proposal
  hash; executed through the REAL /api/v1 handler (``dispatch.call``) with an
  Idempotency-Key.
* Level 3 — a plan: steps run in order with ONE confirmation; ``$stepN.x``
  references are resolved from the real results; the first error stops the
  plan; every step is reported done / failed / not_run. No rollback "magic":
  what was done stays done and is reported precisely.

Secrets: a subscriber password the API requires is generated HERE, sent to the
API, returned ONCE in the confirm response (``show_once``) and never stored,
logged, audited or put in the model-facing result. Card codes from
/cards/generate are dropped before anything else sees the response.
"""
from __future__ import annotations
from app.i18n_text import _tr

import hmac
import json
import secrets
from typing import Any, Optional

from . import audit, store
from .dispatch import call
from .validator import PreparedStep, ProposalRejected, prepare_step, resolve_refs, validate_proposal

# owner 2026-10-07: «خليها أرقام عشوائي وخلص» — subscribers type it on phones/routers, it is not a bank account
_PW_ALPHABET = "0123456789"
PASSWORD_LENGTH = 6


def generate_password() -> str:
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(PASSWORD_LENGTH))


# ─────────────────────────── level 1 ────────────────────────────

def draft_payload(steps: list[PreparedStep]) -> list[dict]:
    out = []
    for i, st in enumerate(steps, start=1):
        payload = dict(st.body or {})
        out.append({
            "n": i, "action": st.action,
            "api": {"method": st.method, "path": "/api/v1" + st.path},
            "payload": payload,
            "password": "admin_types_or_generated_on_save" if st.needs_password else None,
            "executable_by_assistant": st.executable,
            "pending_refs": {k: f"$step{n}.{f}" for k, (n, f) in st.refs.items()},
            "display": st.display,
        })
    return out


# ─────────────────────────── results ────────────────────────────

def _pick(d: Any, keys: tuple[str, ...]) -> dict:
    d = d if isinstance(d, dict) else {}
    return {k: d.get(k) for k in keys if k in d}


def _redact_result(action: str, data: Any) -> tuple[dict, dict]:
    """(model-safe result, outputs for $step refs) — a WHITELIST per action, so
    passwords / card codes / balances can never leak by accident."""
    data = data if isinstance(data, dict) else {}
    if action == "create_subscriber":
        res = _pick(data, ("username", "plan_id", "status", "expire_at"))
        return res, {"username": data.get("username"), "plan_id": data.get("plan_id")}
    if action == "renew_or_extend_subscriber":
        res = _pick(data, ("username", "mode", "new_expire_at", "charged_amount", "charge_mode"))
        return res, {"username": data.get("username")}
    if action == "change_subscriber_plan":
        res = _pick(data, ("username", "plan_id", "policy", "direction", "new_expire_at",
                           "debt_amount", "minute_delta"))
        return res, {"username": data.get("username"), "plan_id": data.get("plan_id")}
    if action == "temporary_speed":
        ts = data.get("temporary_speed") if isinstance(data.get("temporary_speed"), dict) else {}
        res = {"username": data.get("username"), "ends_at": ts.get("ends_at")}
        return res, {"username": data.get("username")}
    if action in ("suspend_subscriber", "enable_subscriber"):
        res = _pick(data, ("username", "status"))
        return res, {"username": data.get("username")}
    if action == "create_plan":
        res = {"plan_id": data.get("id"), "name": data.get("name")}
        return res, {"plan_id": data.get("id")}
    if action == "create_offer":
        res = {"offer_id": data.get("id"), "name": data.get("name"), "plan_id": data.get("plan_id")}
        return res, {"offer_id": data.get("id"), "plan_id": data.get("plan_id")}
    if action == "create_card_batch":
        batch = data.get("batch") if isinstance(data.get("batch"), dict) else {}
        res = {"batch_id": batch.get("id"), "count": batch.get("count"),
               "package_name": batch.get("package_name"), "plan_id": batch.get("plan_id")}
        return res, {"batch_id": batch.get("id"), "plan_id": batch.get("plan_id")}
    return {}, {}


_OUTPUT_KIND = {"username": "subscriber", "plan_id": "plan", "offer_id": "offer"}


def _issue_outputs(conv: dict, outputs: dict) -> None:
    for key, kind in _OUTPUT_KIND.items():
        if outputs.get(key) not in (None, ""):
            store.issue(conv["id"], conv["tenant_id"], kind, [outputs[key]], "step_result")


# ─────────────────────────── one step ────────────────────────────

def _online_session(username: str) -> Optional[str]:
    res = call("GET", "/sessions/online", query={"q": username, "limit": 50})
    if not res.ok:
        return None
    for row in (res.data or {}).get("items", []) or []:
        if str(row.get("username") or "").lower() == username.lower() and row.get("session_id"):
            return str(row["session_id"])
    return None


def run_step(conv: dict, st: PreparedStep, idem_key: str) -> dict:
    """Execute ONE prepared (fully resolved) step through the API."""
    out: dict[str, Any] = {"action": st.action}
    secret: Optional[dict] = None
    if not st.executable:
        out.update(status="failed", error={
            "code": "not_executable_v1",
            "message": _tr("التوليد من عرض متاح من صفحة الويب فقط في هذه النسخة.")})
        return out
    body = dict(st.body or {})
    if st.action == "temporary_speed" and st.path == "/sessions/temp-speed":
        sid = _online_session(str(st.fields["username"]))
        if not sid:
            out.update(status="failed", error={
                "code": "not_online",
                "message": _tr("المشترك غير متّصل الآن — السرعة المؤقتة للمتّصلين فقط.")})
            return out
        body["session_id"] = sid
    if st.needs_password:
        pw = generate_password()
        body["password"] = pw
        secret = {"username": st.fields.get("username"), "password": pw}
    res = call(st.method, st.path, body=body, idempotency_key=idem_key if st.idempotent else "")
    body.pop("password", None)
    if res.ok:
        result, outputs = _redact_result(st.action, res.data)
        out.update(status="done", result=result, outputs=outputs,
                   replayed=res.headers.get("Idempotent-Replay") == "true"
                   or bool((res.data or {}).get("idempotent_replay")))
        if secret:
            out["_secret"] = secret
    else:
        err = res.error
        out.update(status="failed", http_status=res.status,
                   error={"code": err.get("code") or "api_error",
                          "message": err.get("message") or ""})
    return out


# ─────────────────────────── confirm (levels 2/3) ────────────────────────────

class ConfirmError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def _public(report: dict) -> dict:
    """Strip internal keys (secrets/outputs) from a stored/returned report."""
    steps = []
    for s in report.get("steps", []):
        steps.append({k: v for k, v in s.items() if k not in {"_secret", "outputs"}})
    return {**{k: v for k, v in report.items() if k != "steps"}, "steps": steps}


def model_result(report: dict) -> str:
    """The redacted RESULT line given back to the model (no secrets)."""
    slim = {"source": "execution", "status": report.get("status"),
            "steps": [{"n": s.get("n"), "action": s.get("action"), "status": s.get("status"),
                       "result": s.get("result"), "error": (s.get("error") or {}).get("code")}
                      for s in report.get("steps", [])]}
    return "RESULT " + json.dumps(slim, ensure_ascii=False)


def confirm(conv: dict, proposal_id: str, proposal_hash: str, header_key: str = "") -> dict:
    tid, aid = int(conv["tenant_id"]), int(conv["admin_id"])
    row = store.get_proposal(proposal_id, conv["id"], tid, aid)
    if row is None:
        raise ConfirmError(404, "not_found", _tr("المقترح غير موجود."))
    if row["mode"] == "draft":
        raise ConfirmError(409, "draft_only",
                           _tr("هذا مقترح مسودّة (المستوى 1) — يُحفظ من النموذج لا بالتأكيد."))
    if not isinstance(proposal_hash, str) or not hmac.compare_digest(
            proposal_hash.encode(), str(row["proposal_hash"]).encode()):
        audit.record("confirm", conversation_id=conv["id"], outcome="mismatch",
                     proposal_id=proposal_id, proposal_hash=str(row["proposal_hash"]),
                     action=row["action"], error="proposal hash mismatch")
        raise ConfirmError(409, "confirmation_mismatch",
                           _tr("التأكيد لا يطابق المقترح المعروض — اعرض المقترح الجديد وأكّده من جديد."))
    key = (header_key or "").strip()[:200]
    if key and key != row["idempotency_key"]:
        other = store.proposal_by_key(tid, key)
        if other and other["id"] != proposal_id:
            raise ConfirmError(422, "idempotency_key_reused",
                               _tr("مفتاح التكرار استُخدم لمقترح آخر."))
        if row["status"] == "pending":
            store.set_key(proposal_id, tid, key)
            row["idempotency_key"] = key
    if row["status"] in {"executed", "failed", "partial"}:
        report = dict(row["result"] or {})
        report["replayed"] = True
        audit.record("replay", conversation_id=conv["id"], outcome=row["status"],
                     proposal_id=proposal_id, proposal_hash=row["proposal_hash"],
                     action=row["action"], confirmed_at=row.get("confirmed_at"))
        return {"report": _public(report), "show_once": None}
    if row["status"] == "executing":
        raise ConfirmError(409, "in_progress", _tr("هذا المقترح قيد التنفيذ الآن."))
    if row["status"] != "pending":
        raise ConfirmError(409, "not_pending", _tr("لا يمكن تأكيد هذا المقترح."))

    # re-validate NOW (permissions / scope / caps may have changed)
    try:
        validated = validate_proposal(conv, row["proposal"], mode="execute")
    except ProposalRejected as e:
        audit.record("confirm", conversation_id=conv["id"], outcome="rejected",
                     proposal_id=proposal_id, proposal_hash=row["proposal_hash"],
                     action=row["action"],
                     details={"violations": [v.as_dict() for v in e.violations[:20]]})
        raise ConfirmError(403 if e.forbidden else 422,
                           "proposal_forbidden" if e.forbidden else "proposal_rejected",
                           e.violations[0].message,
                           {"violations": [v.as_dict() for v in e.violations[:20]]}) from e
    if validated.proposal_hash != row["proposal_hash"]:
        raise ConfirmError(409, "confirmation_mismatch", _tr("المقترح تغيّر — أكّده من جديد."))
    if not store.claim(proposal_id, tid):
        fresh = store.get_proposal(proposal_id, conv["id"], tid, aid) or {}
        if fresh.get("status") in {"executed", "failed", "partial"}:
            return {"report": _public({**(fresh.get("result") or {}), "replayed": True}),
                    "show_once": None}
        raise ConfirmError(409, "in_progress", _tr("هذا المقترح قيد التنفيذ الآن."))
    confirmed_at = (store.get_proposal(proposal_id, conv["id"], tid, aid) or {}).get("confirmed_at")
    audit.record("confirm", conversation_id=conv["id"], outcome="confirmed",
                 proposal_id=proposal_id, proposal_hash=row["proposal_hash"],
                 action=row["action"], confirmed_at=confirmed_at)

    try:
        report = execute_steps(conv, validated.steps, row["idempotency_key"])
    except Exception:  # noqa: BLE001 — never leave the proposal stuck in "executing"
        import logging
        logging.getLogger(__name__).exception("ops executor: execution crashed")
        report = {"status": "failed", "steps": [
            {"n": i, "action": st.action, "status": "failed" if i == 1 else "not_run",
             **({"error": {"code": "internal_error", "message": ""}} if i == 1 else {})}
            for i, st in enumerate(validated.steps, start=1)]}
    report.update(proposal_id=proposal_id, proposal_hash=row["proposal_hash"],
                  confirmed_at=confirmed_at, replayed=False)
    secrets_once = [s.pop("_secret") for s in report["steps"] if s.get("_secret")]
    stored = _public(report)
    store.finish(proposal_id, tid, report["status"], stored)
    audit.record("execute", conversation_id=conv["id"], outcome=report["status"],
                 proposal_id=proposal_id, proposal_hash=row["proposal_hash"],
                 action=row["action"], confirmed_at=confirmed_at,
                 details={"steps": [{"n": s["n"], "action": s["action"],
                                     "status": s["status"], "result": s.get("result"),
                                     "error": s.get("error")} for s in stored["steps"]]})
    show_once = None
    if secrets_once:
        show_once = {"subscriber_passwords": secrets_once,
                     "note": "shown once — never stored, logged or sent to the model"}
    return {"report": stored, "show_once": show_once}


def execute_steps(conv: dict, steps: list[PreparedStep], idem_key: str) -> dict:
    results: list[dict] = []
    outputs: dict[int, dict] = {}
    failed = False
    multi = len(steps) > 1
    for n, st in enumerate(steps, start=1):
        if failed:
            results.append({"n": n, "action": st.action, "status": "not_run"})
            continue
        try:
            if st.refs:
                fields = resolve_refs(st.fields, st.refs, outputs)
                st = prepare_step(conv, st.action, fields, path=f"$.steps[{n - 1}].fields")
            elif multi and n > 1:
                st = prepare_step(conv, st.action, dict(st.fields),
                                  path=f"$.steps[{n - 1}].fields")
        except KeyError:
            results.append({"n": n, "action": st.action, "status": "failed",
                            "error": {"code": "ref_unresolved",
                                      "message": _tr("نتيجة الخطوة السابقة لا تحوي القيمة المطلوبة.")}})
            failed = True
            continue
        except ProposalRejected as e:
            results.append({"n": n, "action": st.action, "status": "failed",
                            "error": {"code": e.violations[0].code,
                                      "message": e.violations[0].message}})
            failed = True
            continue
        step_key = f"{idem_key}:{n}" if multi else idem_key
        r = run_step(conv, st, step_key)
        r["n"] = n
        if r["status"] == "done":
            outputs[n] = r.get("outputs") or {}
            _issue_outputs(conv, outputs[n])
        else:
            failed = True
        results.append(r)
    done = sum(1 for r in results if r["status"] == "done")
    if done == len(results):
        status = "executed"
    elif done == 0:
        status = "failed"
    else:
        status = "partial"
    return {"status": status, "steps": results}


__all__ = ["draft_payload", "confirm", "execute_steps", "run_step", "model_result",
           "ConfirmError", "generate_password", "PASSWORD_LENGTH"]
