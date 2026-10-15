"""With a key set and a failing LLM, a question the strict planner answers by itself must not call the LLM."""
import openpyxl

import run
from src import llm_client


def test_strict_answers_without_llm(tmp_path, monkeypatch):
    d = tmp_path / "data"; (d / "xlsx_files").mkdir(parents=True)
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Employment by age"]); ws.append([]); ws.append(["Age", "Employed", "Unemployed"])
    for sec, vals in (("Men", [(100, 30), (200, 10), (150, 5)]), ("Women", [(120, 12), (210, 25), (90, 7)])):
        ws.append([sec])
        for age, (e, u) in zip(["16 to 24", "25 to 54", "55 and over"], vals):
            ws.append([age, e, u])
    wb.save(d / "xlsx_files" / "t.xlsx")

    calls = []

    def boom(body, timeout):
        calls.append(body)
        raise RuntimeError("LLM down")

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-key")
    monkeypatch.setattr(llm_client, "_post", boom)
    monkeypatch.setattr(llm_client, "RETRY_SLEEP", 0, raising=False)
    q = {"id": "x1", "file_name": "t.xlsx",
         "query": "Which age group had the most unemployed women? Answer with only the age group."}
    ans, budget = run.answer_one(q, d, {})
    assert ans == "25 to 54"
    assert calls == []
    assert budget.attempts == 0
