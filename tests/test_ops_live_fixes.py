"""client20 live-test fixes (2026-10-07): required-field rejections become questions; zero-shot prompt mode."""
from types import SimpleNamespace

from app.radius.services.ops_assistant import conversation as C
from app.radius.services.ops_assistant import model_client as M
from app.radius.services.ops_assistant import zeroshot_prompt as Z


def _res(viol):
    return SimpleNamespace(ok=False, status=422, data=None,
                           error={"code": "proposal_rejected", "message": "x", "details": {"violations": viol}})


def test_required_violations_become_missing_fields():
    res = _res([{"code": "schema", "message": "is required", "path": "$.fields.plan_id"},
                {"code": "schema", "message": "is required", "path": "$.fields.username"},
                {"code": "schema", "message": "does not match the required pattern", "path": "$.fields.mobile"}])
    assert C._required_missing(res) == ["plan_id", "username"]
    q = C.missing_question(["plan_id", "username"])
    assert "الباقة" in q and "اسم المستخدم" in q and "is required" not in q


def test_other_violations_are_not_questions():
    assert C._required_missing(_res([{"code": "schema", "message": "unknown field", "path": "$.fields.x"}])) == []


def test_zeroshot_prompt_mode(monkeypatch):
    monkeypatch.setenv(M.ENV_PROMPT, "zeroshot")
    p = M.system_prompt()
    assert p == Z.system_prompt()
    for must in ("list_card_batches", "create_subscriber", "حزمة", "ملف سرعة", "charge_mode", "reply"):
        assert must in p
    monkeypatch.setenv(M.ENV_PROMPT, "v1")
    assert M.system_prompt() == M.SYSTEM_PROMPT_V1
    monkeypatch.delenv(M.ENV_PROMPT)
    assert M.system_prompt() == M.SYSTEM_PROMPT_V3


def test_model_name_env(monkeypatch):
    monkeypatch.setenv(M.ENV_MODEL_NAME, "para")
    assert M.request_body([{"role": "user", "content": "hi"}])["model"] == "para"
    monkeypatch.delenv(M.ENV_MODEL_NAME)
    assert M.request_body([{"role": "user", "content": "hi"}])["model"] == "hoberadius-ops"


def test_status_is_a_search_filter_only_for_find_subscriber():
    from app.radius.services.ops_assistant import validator as V
    ok = {"action": "find_subscriber", "fields": {"query": "", "status": "expired"}, "missing": [],
          "message": "بدوّر على المنتهين"}
    assert V._deep_forbidden(ok, allow=frozenset(V.SEARCH_FILTER_PATHS["find_subscriber"])) == []
    bad = {"action": "renew_or_extend_subscriber", "fields": {"username": "a", "status": "enabled"}}
    assert V._deep_forbidden(bad, allow=frozenset(V.SEARCH_FILTER_PATHS.get("renew_or_extend_subscriber", ())))
    nested = {"action": "find_subscriber", "fields": {"query": "x", "extra": {"status": "x"}}}
    assert V._deep_forbidden(nested, allow=frozenset(V.SEARCH_FILTER_PATHS["find_subscriber"]))


def test_read_only_limit_is_clamped_not_rejected():
    from app.radius.services.ops_assistant import validator as V
    p = {"action": "list_card_batches", "fields": {"limit": 50}, "missing": [], "message": "x"}
    assert V._clamp_read_limits(p)["fields"]["limit"] == 10
    ex = {"action": "create_card_batch", "fields": {"count": 50, "limit": 50}}
    assert V._clamp_read_limits(ex) is ex


def test_validator_reasons_shown_in_arabic():
    assert C.why_ar("greater than 10") == "قيمة أكبر من الحدّ المسموح (10)"
    assert "greater" not in C.why_ar("greater than 10; unknown field")


def test_zeroshot_self_corrects_once_after_rejection(monkeypatch):
    """A rejected proposal is fed back (with the violation paths) and the model gets ONE more turn."""
    import json as _j
    monkeypatch.setenv(M.ENV_PROMPT, "zeroshot")
    turns = iter([
        _j.dumps({"action": "create_plan", "fields": {"name": "x"}, "missing": [], "message": "m", "summary_ar": "s"}),
        _j.dumps({"action": "ask", "fields": {}, "missing": ["speed_down_kbps"], "message": "كم السرعة؟"}),
    ])
    appended = []
    monkeypatch.setattr(C, "model_messages", lambda cid, tid: [])
    monkeypatch.setattr(C, "model_append", lambda cid, tid, obj: appended.append(("a", obj)))
    monkeypatch.setattr(C, "append", lambda cid, tid, role, text: appended.append((role, text)))
    monkeypatch.setattr(C, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(C, "_lookup_key", lambda *a: None)

    def api(method, path, body=None):
        if body["proposal"]["action"] == "create_plan":
            return _res([{"code": "schema", "message": "is required", "path": "$.fields.speed_down_kbps"}])
        return SimpleNamespace(ok=True, status=200, data={"kind": "control"}, error=None)
    api.tenant_id, api.admin_id = 1, 1
    out = C.run_model(api, "c1", call=lambda msgs, deadline: next(turns))
    assert out[-1]["action"] == "ask" and "كم السرعة" in out[-1]["text"]
    fed = [t for r, t in appended if r == "tool"]
    assert fed and "speed_down_kbps" in fed[0] and "is required" in fed[0]


def test_mobile_result_rows_are_arabic_and_readable():
    from app.radius.services.ops_assistant import display as D
    reps = [{"type": "result", "source": "card_info", "data": {
        "card": "55039046", "status": "active", "counting": "from_first_connect", "used_time": "2h 25m",
        "remaining": "6h 16m", "expires_local": "2026-10-08T03:00", "sessions": 6}}]
    d = D.for_client(reps)[0]["data"]
    assert "card" not in d and "used_time" not in d and "counting" not in d
    assert d["وقت الاتصال الفعليّ (كل الجلسات)"] == "2 ساعة و25 دقيقة"
    assert d["ينتهي"] == "2026-10-08 03:00" and d["الحالة"] == "فعّال" and d["عدد الجلسات"] == 6
    assert D.for_client([{"type": "assistant", "text": "x"}]) == [{"type": "assistant", "text": "x"}]
