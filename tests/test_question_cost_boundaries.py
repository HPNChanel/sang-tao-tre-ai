import json
from pathlib import Path
import sys
import os
import signal

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from src import llm_client as L


def response(cost=0.0001):
    return {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "cost": cost},
            "choices": [{"message": {"content": '{"version":1,"op":"sum","format":{"kind":"int"}}'}}]}


def setup(monkeypatch, value):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    monkeypatch.setattr(L, "BILLING_HALT_REASON", None)
    monkeypatch.setattr(L, "RETRY_SLEEP", 0)
    calls = []
    def post(body, timeout):
        calls.append(json.loads(body))
        if isinstance(value, BaseException):
            raise value
        return value
    monkeypatch.setattr(L, "_post", post)
    return calls


def test_three_attempt_limit_shared_across_separate_planning_rounds(monkeypatch):
    calls = setup(monkeypatch, response())
    budget = L.Budget()
    for _ in range(7):
        L.llm_plan("question", "tables", "{}", budget)
    assert len(calls) == budget.attempts == 3
    assert budget.actual_usd == .0003 and budget.unresolved_usd == 0


@pytest.mark.parametrize("cost", [None, True, -1, "NaN", "Infinity", "bad", {}, []])
def test_invalid_usage_cost_remains_reserved(monkeypatch, cost):
    setup(monkeypatch, response(cost))
    budget = L.Budget()
    assert L.llm_plan("question", "tables", "{}", budget)
    assert budget.actual_usd == 0 and budget.unresolved_usd == .03 and budget.cost_usd is None
    assert budget.tokens == 20


def test_interrupt_during_transport_keeps_pre_dispatch_reservation(monkeypatch):
    calls = setup(monkeypatch, KeyboardInterrupt())
    budget = L.Budget()
    with pytest.raises(KeyboardInterrupt):
        L.llm_plan("question", "tables", "{}", budget)
    assert len(calls) == budget.attempts == 1
    assert budget.unresolved_usd == .03 and budget.cost_usd is None
    assert budget.token_usage_unknown and budget.tokens == 0


def test_billing_anomaly_stops_the_process_from_further_paid_calls(monkeypatch):
    calls = setup(monkeypatch, response(.04))
    budget = L.Budget()
    assert L.llm_plan("question", "tables", "{}", budget) is None
    assert budget.actual_usd == .04 and budget.halt_reason
    assert L.llm_plan("another", "tables", "{}", L.Budget()) is None
    assert len(calls) == 1


@pytest.mark.parametrize("text", ["x" * 25000, "界" * 9000, "😀" * 7000], ids=["ascii", "cjk", "emoji"])
def test_serialized_utf8_body_limit_blocks_without_truncating(monkeypatch, text):
    calls = setup(monkeypatch, response())
    budget = L.Budget()
    assert L.llm_plan(text, "tables", "{}", budget) is None
    assert calls == [] and budget.attempts == 0
    assert budget.halt_reason == "request_exceeds_24_kib"


def test_request_pins_provider_rates_and_complete_question(monkeypatch):
    calls = setup(monkeypatch, response())
    question = "The exact question with café and ✓"
    L.llm_plan(question, "tables", "{}", L.Budget())
    body = calls[0]
    assert question in body["messages"][1]["content"]
    assert body["provider"] == L.PROVIDER
    assert body["reasoning"] == {"enabled": False}
    assert body["max_tokens"] <= 1200


def test_declared_model_substitution_is_blocked(monkeypatch):
    calls = setup(monkeypatch, response())
    monkeypatch.setattr(L, "MODEL", "another/model")
    assert L.llm_plan("question", "tables", "{}", L.Budget()) is None
    assert not calls


@pytest.mark.skipif(not hasattr(signal, "pthread_sigmask"), reason="POSIX alarm accounting boundary")
@pytest.mark.parametrize("cost", [.001, .04])
def test_alarm_inside_settlement_is_delivered_only_after_accounting(monkeypatch, cost):
    monkeypatch.setattr(L, "BILLING_HALT_REASON", None)
    budget = L.Budget()
    index = budget.reserve()
    class Interrupted(BaseException):
        pass
    previous = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Interrupted()))
    original_money = L._money
    def interrupt_during_cost_parse(value):
        os.kill(os.getpid(), signal.SIGALRM)
        return original_money(value)
    monkeypatch.setattr(L, "_money", interrupt_during_cost_parse)
    try:
        with pytest.raises(Interrupted):
            budget.finish(index, response(cost))
        assert budget.actual_usd == cost and budget.unresolved_usd == 0
        assert budget.log[0]["finished"] and budget.log[0]["cost_usd"] == cost
        assert bool(L.BILLING_HALT_REASON) == (cost > .03)
    finally:
        signal.signal(signal.SIGALRM, previous)
