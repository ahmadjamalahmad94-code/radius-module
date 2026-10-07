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
