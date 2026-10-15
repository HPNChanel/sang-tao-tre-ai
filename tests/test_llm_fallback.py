"""Fake API key + a failing LLM must match the no-key answer. No network, no external data."""
import json
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run  # noqa: E402
from src import conventions as C  # noqa: E402
from src import llm_client  # noqa: E402

# Two similar 2023 columns: strict mode declines, the no-key planner answers "300".
QUERY = "What were the 2023 sales of Hilltop? Answer with only the integer."
VALID_BUT_UNEXECUTABLE = {
    "version": 1,
    "op": "lookup",
    "rows": {"paths": ["No Such Store"]},
    "columns": ["Sales 2023 (estimate)"],
    "format": {"kind": "int"},
}


def _workbook(tmp_path):
    data = tmp_path / "data"
    xlsx = data / "xlsx_files"
    xlsx.mkdir(parents=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Store", "Sales 2023 (estimate)", "Sales 2023 (final)", "Sales 2024"])
    ws.append(["Riverside", 100, 120, 90])
    ws.append(["Hilltop", 300, 280, 310])
    ws.append(["Lakeview", 50, 60, 70])
    wb.save(xlsx / "sales.xlsx")
    return data


def _question():
    return {"id": "q1", "file_name": "sales.xlsx", "query": QUERY}


def _no_key_answer(monkeypatch, data):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(C, "STRICT_CONFIDENCE", None)
    ans, budget = run.answer_one(_question(), data, {})
    assert budget.attempts == 0
    assert ans != ""
    return ans


def _install_llm(monkeypatch, responder):
    calls = []

    def fake_post(body, timeout):
        calls.append(body)
        return responder(len(calls))

    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-not-a-real-key")
    monkeypatch.setattr(C, "STRICT_CONFIDENCE", None)
    monkeypatch.setattr(llm_client, "RETRY_SLEEP", 0)
    monkeypatch.setattr(llm_client, "_post", fake_post)
    return calls


def _assert_matches_nokey(monkeypatch, tmp_path, responder, expect_calls):
    data = _workbook(tmp_path)
    expected = _no_key_answer(monkeypatch, data)
    calls = _install_llm(monkeypatch, responder)
    got, _budget = run.answer_one(_question(), data, {})
    assert got == expected
    if expect_calls:
        assert calls
    return got


def test_llm_raises_uses_nonstrict_answer(monkeypatch, tmp_path):
    def responder(_n):
        raise TimeoutError("timeout")

    _assert_matches_nokey(monkeypatch, tmp_path, responder, False)


def test_llm_garbage_json_uses_nonstrict_answer(monkeypatch, tmp_path):
    def responder(_n):
        return {"usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "choices": [{"message": {"content": "this is not json"}}]}

    _assert_matches_nokey(monkeypatch, tmp_path, responder, False)


def test_llm_schema_invalid_uses_nonstrict_answer(monkeypatch, tmp_path):
    bad = json.dumps({"version": 1, "op": "explode", "format": {"kind": "int"}})

    def responder(_n):
        return {"usage": {"prompt_tokens": 10, "completion_tokens": 8},
                "choices": [{"message": {"content": bad}}]}

    _assert_matches_nokey(monkeypatch, tmp_path, responder, False)


def test_llm_executor_error_uses_nonstrict_answer(monkeypatch, tmp_path):
    body = json.dumps(VALID_BUT_UNEXECUTABLE)

    def responder(_n):
        return {"usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "choices": [{"message": {"content": body}}]}

    _assert_matches_nokey(monkeypatch, tmp_path, responder, False)


def test_llm_cost_cap_uses_nonstrict_answer(monkeypatch, tmp_path):
    class Blocked(llm_client.Budget):
        def allow(self, prompt_chars):
            return False

    data = _workbook(tmp_path)
    expected = _no_key_answer(monkeypatch, data)
    def responder(_n):
        raise AssertionError("LLM must not be called once the cost cap blocks it")

    calls = _install_llm(monkeypatch, responder)
    monkeypatch.setattr(llm_client, "Budget", Blocked)
    got, budget = run.answer_one(_question(), data, {})
    assert got == expected and calls == [] and budget.attempts == 0
