import copy
import json

import openpyxl
import pytest

from src import llm_client as L, plan_review as R
from src.plan_validate import load_schema
from src.xlsx_tables import load_tables


@pytest.fixture
def tables(tmp_path):
    wb = openpyxl.Workbook()
    for row in [["Place", "2031", "2032"], ["Bay", 10, 17], ["Hill", 30, 31]]:
        wb.active.append(row)
    path = tmp_path / "survey.xlsx"
    wb.save(path)
    return load_tables(path)


def plan(path="Bay"):
    return {"version": 1, "table": {"index": 0}, "rows": {"paths": [path]},
            "columns": ["2031"], "op": "lookup", "format": {"kind": "int"}}


def transport(monkeypatch, responses):
    calls = []
    def post(body, timeout):
        calls.append(json.loads(body))
        p = responses[len(calls)-1]
        return {"choices": [{"message": {"content": json.dumps(p)}, "finish_reason": "stop"}],
                "usage": {"cost": 0.001, "prompt_tokens": 100, "completion_tokens": 60}}
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic")
    monkeypatch.setenv("QWEN_REVIEW_POLICY", "selective")
    monkeypatch.setattr(L, "_post", post)
    monkeypatch.setattr(L, "RETRY_SLEEP", 0)
    return calls


@pytest.mark.parametrize("mutate", [
    lambda p: p["table"].update(index=99),
    lambda p: p["table"].update(sheet="missing"),
    lambda p: p.update(columns=["not a column"]),
    lambda p: p["rows"].update(paths=["missing"]),
    lambda p: p["rows"].update(where=[{"column":"missing", "op":">", "value":2}]),
    lambda p: p["rows"].update(scope="missing"),
])
def test_reject_bad_references(tables, mutate):
    p = plan(); mutate(p)
    assert R.reference_errors(p, tables)


def test_reversed_increase_and_missing_threshold_are_rejected():
    p = plan(); p.update(op="diff", columns=["2031", "2032"], args={"signed_order":"first_minus_second"})
    assert R.semantic_errors(p, "Increase from 2031 to 2032")
    p["args"]["signed_order"] = "second_minus_first"
    assert not R.semantic_errors(p, "Increase from 2031 to 2032")
    p["columns"] = ["2032", "2031"]
    assert R.semantic_errors(p, "Increase from 2031 to 2032")
    p["args"]["signed_order"] = "first_minus_second"
    assert not R.semantic_errors(p, "Increase from 2031 to 2032")
    assert R.semantic_errors(p, "Decrease from 2031 to 2032")
    assert R.semantic_errors(plan(), "Sum values where 2031 > 14")


def test_easy_lookup_does_not_trigger_selective_review(tables, monkeypatch):
    monkeypatch.setenv("QWEN_REVIEW_POLICY", "selective")
    assert not R.needs_review("Bay in 2031. Answer with an integer.", tables)
    assert R.needs_review("Which place has the largest increase from 2031 to 2032?", tables)
    monkeypatch.setenv("QWEN_REVIEW_POLICY", "fallback")
    assert not R.needs_review("Which place has the largest increase from 2031 to 2032?", tables)


def test_unresolved_disagreement_retains_valid_rule(tables, monkeypatch):
    wrong = plan("Hill")
    calls = transport(monkeypatch, [wrong, {}])
    monkeypatch.setattr(R, "plan_with_confidence", lambda *a, **kw: (plan(), 1.0, []))
    b = L.Budget()
    result = R.choose("Bay or Hill in 2031. Answer with an integer.", tables, "survey.xlsx", b, load_schema(), "q")
    assert result == "10" and len(calls) == 2
    assert b.decision["source"] == "rule_unresolved"
    assert 'plan_A' not in calls[0]["messages"][1]["content"]
    assert 'plan_A' in calls[1]["messages"][1]["content"]


def test_repair_and_adjudication_share_three_attempts(tables, monkeypatch):
    calls = transport(monkeypatch, [{}, plan("Hill"), {}])
    monkeypatch.setattr(R, "plan_with_confidence", lambda *a, **kw: (plan(), 1.0, []))
    b = L.Budget()
    assert R.choose("Bay or Hill in 2031. Answer with an integer.", tables, "survey.xlsx", b, load_schema(), "q") == "10"
    assert b.attempts == len(calls) == 3
    assert b.spent == pytest.approx(.003)


def test_valid_schema_cannot_substitute_another_named_row_or_measure(tables):
    assert R.semantic_errors(plan("Hill"), "Bay in 2031.", tables)
    p = plan(); p["columns"] = ["2032"]
    assert R.semantic_errors(p, "Bay in 2031.", tables)
    p["op"] = "sum"; p["rows"] = {"where": [{"column":"2031", "op":"<", "value":14}]}
    assert R.semantic_errors(p, "Sum 2032 where 2031 > 14.", tables)


def test_aggregate_proposal_cannot_evade_named_operand_checks(tables, monkeypatch):
    wrong = plan("Hill"); wrong.update(op="sum", columns=["2032"])
    calls = transport(monkeypatch, [wrong, wrong])
    b = L.Budget()
    assert R.choose("Bay in 2031. Answer with an integer.", tables, "survey.xlsx", b, load_schema(), "q") == "10"
    assert b.decision["source"] == "rule" and len(calls) == 2


def test_explicit_affixes_are_preserved_without_erasing_decimal_precision():
    p = plan(); p["format"] = {"kind": "decimal", "decimals": 1}
    R.apply_format(p, "Compute a statistic. Answer with only the number.")
    assert p["format"]["kind"] == "decimal"
    R.apply_format(p, "Find the rate. Return exactly two decimal places followed by '%'.")
    assert p["format"]["decimals"] == 2 and p["format"]["suffix"] == "%"
