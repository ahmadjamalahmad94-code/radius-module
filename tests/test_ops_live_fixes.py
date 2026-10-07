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
