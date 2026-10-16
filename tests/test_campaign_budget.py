"""Campaign transport/cost boundaries. These tests never call OpenRouter."""
import importlib.util
import json
from pathlib import Path

import pytest

adapter_spec = importlib.util.spec_from_file_location("eval_solver", Path(__file__).parents[1] / "scripts" / "eval_solver.py")
eval_solver = importlib.util.module_from_spec(adapter_spec)
adapter_spec.loader.exec_module(eval_solver)


@pytest.mark.parametrize("url", ["http://127.0.0.1:1234@remote.invalid", "https://127.0.0.1:1234",
                                     "http://localhost:1234", "http://127.0.0.1:1234/path"])
def test_gateway_rejects_url_confusion(url):
    with pytest.raises(ValueError):
        eval_solver.checked_gateway(url)


def test_python_egress_guard_allows_only_gateway():
    guard = eval_solver.egress_guard(1234)
    guard("socket.connect", (None, ("127.0.0.1", 1234)))
    with pytest.raises(PermissionError):
        guard("socket.connect", (None, ("127.0.0.1", 1235)))
    with pytest.raises(PermissionError):
        guard("socket.getaddrinfo", ("external.invalid", 443))
    with pytest.raises(PermissionError):
        eval_solver.egress_guard(None)("socket.connect", (None, ("127.0.0.1", 1234)))

spec = importlib.util.spec_from_file_location("qwen_campaign", Path(__file__).parents[1] / "scripts" / "qwen_campaign.py")
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


def setup_ledger(tmp_path, *, stage="baseline", count=100):
    ledger = C.Ledger.create(tmp_path / "ledger.json", "test")
    run = ledger.register(stage, "a" * 40, "b" * 64,
                          {"policy": "fallback", "output_tokens": 600},
                          [f"case_{i}" for i in range(count)])
    return ledger, run


def body(**changes):
    data = {"model": C.MODEL, "messages": [{"role": "user", "content": "PRIVATE QUESTION"}],
            "max_tokens": 600, "temperature": 0, "response_format": {"type": "json_object"},
            "reasoning": {"enabled": False}}
    data.update(changes)
    return C.canonical(data)


def reserve(ledger, run, question="case_0", stage="baseline"):
    return ledger.reserve(stage, run, question, "c" * 64)


def test_durable_reservation_survives_restart(tmp_path):
    ledger, run = setup_ledger(tmp_path)
    reserve(ledger, run)
    reloaded = C.Ledger(ledger.path)
    assert reloaded.totals()["unresolved_usd"] == "0.03"
    assert reloaded.state["attempts"][0]["status"] == "reserved"


@pytest.mark.parametrize("cost", [None, -1, "NaN", "Infinity", "bad", True, {}, []])
def test_invalid_cost_keeps_the_whole_reservation(tmp_path, cost):
    ledger, run = setup_ledger(tmp_path)
    number = reserve(ledger, run)
    ledger.finish(number, {"usage": {"cost": cost, "prompt_tokens": 1, "completion_tokens": 1}})
    assert ledger.totals()["actual_usd"] == "0"
    assert ledger.totals()["unresolved_usd"] == "0.03"


@pytest.mark.parametrize("cost", [0, 0.000123, "0.02999999", "0.03"])
def test_billed_cost_is_authoritative_not_a_token_estimate(tmp_path, cost):
    ledger, run = setup_ledger(tmp_path)
    number = reserve(ledger, run)
    ledger.finish(number, {"usage": {"cost": cost, "prompt_tokens": 999999, "completion_tokens": 1200}})
    assert C.money(ledger.totals()["actual_usd"]) == C.money(cost)
    assert ledger.totals()["unresolved_usd"] == "0"


def test_fourth_attempt_rejected_after_successes_across_restarts(tmp_path):
    ledger, run = setup_ledger(tmp_path)
    for _ in range(3):
        number = reserve(ledger, run)
        ledger.finish(number, {"usage": {"cost": 0.0001}})
        ledger = C.Ledger(ledger.path)
    with pytest.raises(C.CampaignError, match="question attempt"):
        reserve(ledger, run)
    assert ledger.totals()["attempts"] == 3


def test_re_registering_cannot_reset_same_question(tmp_path):
    ledger, run = setup_ledger(tmp_path)
    for _ in range(3):
        reserve(ledger, run)
    again = ledger.register("baseline", "a" * 40, "b" * 64,
                            {"policy": "fallback", "output_tokens": 600},
                            [f"case_{i}" for i in range(100)])
    assert again == run
    with pytest.raises(C.CampaignError):
        reserve(ledger, again)
    with pytest.raises(C.CampaignError, match="between stages"):
        ledger.register("reserve", "a" * 40, "b" * 64,
                        {"policy": "fallback", "output_tokens": 600},
                        [f"case_{i}" for i in range(100)])


@pytest.mark.parametrize("stage,capacity", [("baseline", 16), ("development", 66), ("sealed", 50), ("performance", 16), ("reserve", 16)])
def test_stage_budget_preserves_other_stages(tmp_path, stage, capacity):
    ledger, run = setup_ledger(tmp_path, stage=stage)
    for i in range(capacity):
        reserve(ledger, run, f"case_{i}", stage)
    with pytest.raises(C.CampaignError, match="stage budget"):
        reserve(ledger, run, f"case_{capacity}", stage)
    assert C.money(ledger.totals()["exposure_usd"]) <= C.money(C.STAGE_CAPS[stage])


def test_billing_over_reservation_halts_all_later_questions(tmp_path):
    ledger, run = setup_ledger(tmp_path)
    ledger.finish(reserve(ledger, run), {"usage": {"cost": "0.030001"}})
    assert ledger.state["halt_reason"] == "actual_cost_exceeded_reservation"
    with pytest.raises(C.CampaignError, match="halted"):
        reserve(ledger, run, "case_1")


def test_a_live_gateway_cannot_share_ledger(tmp_path):
    ledger, _ = setup_ledger(tmp_path)
    with C.exclusive_ledger(ledger.path):
        with pytest.raises(C.CampaignError, match="owns the ledger"):
            with C.exclusive_ledger(ledger.path):
                pass
    assert not Path(str(ledger.path) + ".lock").exists()


@pytest.mark.parametrize("changes", [
    {"model": "other-model"}, {"max_tokens": 1201}, {"max_tokens": -1},
    {"temperature": 1}, {"reasoning": {"enabled": True}},
    {"tools": []}, {"plugins": []}, {"stream": True},
    {"response_format": {"type": "text"}},
    {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
    {"messages": [{"role": "tool", "content": "x"}]},
    {"messages": []},
])
def test_unapproved_request_never_reaches_the_network(changes):
    with pytest.raises(C.CampaignError):
        C.guarded_body(body(**changes))


def test_unicode_request_cap_is_bytes_not_characters():
    with pytest.raises(C.CampaignError, match="24 KiB"):
        C.guarded_body(body(messages=[{"role": "user", "content": "漢" * 9000}]))


def test_guard_addition_is_included_in_size_limit():
    raw = body(messages=[{"role": "user", "content": "x"}])
    room = C.MAX_BODY - len(raw)
    exactly = body(messages=[{"role": "user", "content": "x" * (room + 1)}])
    assert len(exactly) == C.MAX_BODY
    with pytest.raises(C.CampaignError, match="guarded request"):
        C.guarded_body(exactly)


@pytest.mark.parametrize("question", ["unknown", "a secret question?", "case_0\nprivate", ""])
def test_unknown_or_nonopaque_question_rejected_before_network(tmp_path, monkeypatch, question):
    ledger, run = setup_ledger(tmp_path)
    monkeypatch.setattr(C, "remote_json", lambda *a, **kw: pytest.fail("network must not be reached"))
    gateway = C.Gateway(ledger, "provider-secret", "t" * 32, "baseline", run, tmp_path / "pricing.json")
    with pytest.raises(C.CampaignError):
        gateway.call("Bearer " + "t" * 32, question, body())
    assert ledger.totals()["attempts"] == 0


@pytest.mark.parametrize("authorization", [None, "", "Bearer wrong", "Bearer 漢"])
def test_authentication_fails_without_creating_a_reservation(tmp_path, authorization):
    ledger, run = setup_ledger(tmp_path)
    gateway = C.Gateway(ledger, "provider-secret", "t" * 32, "baseline", run, tmp_path / "pricing.json")
    with pytest.raises(C.CampaignError, match="unauthorized"):
        gateway.call(authorization, "case_0", body())
    assert ledger.totals()["attempts"] == 0


def test_timeout_is_not_reported_as_zero_cost(tmp_path, monkeypatch):
    ledger, run = setup_ledger(tmp_path)
    monkeypatch.setattr(C, "verify_prices", lambda _p: None)
    def timeout(*_a, **_kw):
        assert C.Ledger(ledger.path).state["attempts"][0]["status"] == "dispatched"
        raise TimeoutError("provider-secret PRIVATE QUESTION")
    monkeypatch.setattr(C, "remote_json", timeout)
    gateway = C.Gateway(ledger, "provider-secret", "t" * 32, "baseline", run, tmp_path / "pricing.json")
    with pytest.raises(C.CampaignError, match="reservation retained") as caught:
        gateway.call("Bearer " + "t" * 32, "case_0", body())
    persisted = ledger.path.read_text()
    for private in ("provider-secret", "PRIVATE QUESTION", "case_0"):
        assert private not in persisted and private not in str(caught.value)
    assert ledger.totals()["unresolved_usd"] == "0.03"


def test_nonloopback_server_cannot_start(tmp_path):
    with pytest.raises(C.CampaignError, match="loopback"):
        C.serve(None, "0.0.0.0", 0, tmp_path / "ready.json")


def test_provider_configuration_is_not_controlled_by_solver():
    guarded = json.loads(C.guarded_body(body(provider={"allow_fallbacks": True, "only": ["unapproved"]})))
    assert guarded["provider"]["only"] == ["alibaba"]
    assert guarded["provider"]["allow_fallbacks"] is False
    assert guarded["provider"]["max_price"]["request"] == 0


def test_price_drift_blocks_before_spending(tmp_path, monkeypatch):
    ledger, run = setup_ledger(tmp_path)
    def changed(_p):
        raise C.CampaignError("changed")
    monkeypatch.setattr(C, "verify_prices", changed)
    monkeypatch.setattr(C, "remote_json", lambda *a, **kw: pytest.fail("paid call must not occur"))
    gateway = C.Gateway(ledger, "provider-secret", "t" * 32, "baseline", run, tmp_path / "pricing.json")
    with pytest.raises(C.CampaignError, match="no paid request sent"):
        gateway.call("Bearer " + "t" * 32, "case_0", body())
    assert ledger.totals()["attempts"] == 0
    assert ledger.state["halt_reason"] == "provider_metadata_not_verified"


def test_offline_invocation_cannot_be_used_for_paid_calls(tmp_path, monkeypatch):
    ledger = C.Ledger.create(tmp_path / "ledger.json", "test")
    run = ledger.register("baseline", "a" * 40, "b" * 64,
                          {"policy": "fallback", "output_tokens": 600, "api_enabled": False}, ["case_0"])
    monkeypatch.setattr(C, "remote_json", lambda *a, **kw: pytest.fail("network must not be reached"))
    gateway = C.Gateway(ledger, "provider-secret", "t" * 32, "baseline", run, tmp_path / "pricing.json")
    with pytest.raises(C.CampaignError, match="network disabled"):
        gateway.call("Bearer " + "t" * 32, "case_0", body())
