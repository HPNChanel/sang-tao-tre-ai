import json

import openpyxl
import pytest

import run
from src import llm_client
from src.plan_validate import validate
from src.xlsx_tables import load_tables


@pytest.mark.parametrize("plan, expected", [
    ({"op": "max", "rows": {"exclude_paths": ["All locations"]}, "columns": ["First", "Second"],
      "args": {"metric": "sum"}}, "17.0"),
    ({"op": "spread", "rows": {"paths": ["Bay", "Hill", "Lake"]}, "columns": ["First"]}, "5.0"),
])
def test_mocked_fallback_executes_compositions_without_network(tmp_path, monkeypatch, plan, expected):
    wb = openpyxl.Workbook()
    for row in [["Place", "First", "Second"], ["All locations", 90, 95], ["Bay", 8, 9], ["Hill", 3, 6], ["Lake", 5, 10]]:
        wb.active.append(row)
    path = tmp_path / "places.xlsx"
    wb.save(path)
    tables = load_tables(path)
    plan = {"version": 1, "format": {"kind": "decimal", "decimals": 1}, **plan}
    assert validate(plan) == []
    calls = []
    def fake_post(body, timeout):
        calls.append(json.loads(body))
        return {"usage": {"prompt_tokens": 20, "completion_tokens": 40},
                "choices": [{"message": {"content": json.dumps(plan)}, "finish_reason": "stop"}]}
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(llm_client, "_post", fake_post)
    budget = llm_client.Budget()
    result = run._llm_then_rule({"id": "synthetic", "query": "Compute the requested statistic. Answer with only the number."}, tables, path.name, budget)
    assert result == expected
    assert len(calls) == budget.attempts == 1
    assert calls[0]["model"] == "qwen/qwen3.7-flash"
    assert "synthetic-test-key" not in json.dumps(calls)
