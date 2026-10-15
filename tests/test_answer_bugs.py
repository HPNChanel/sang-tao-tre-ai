"""Rule answers that must not depend on an LLM key. Duplicate surnames stay."""
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run
from src import conventions as C
from src import llm_client
from src.code_planner import plan
from src.plan_executor import execute
from src.xlsx_tables import load_tables


def test_duplicate_surnames_stay(tmp_path):
    """Two different people may share a surname. The list keeps both."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Last", "First", "Score"])
    ws.append(["Ngo", "Minh", 99])
    ws.append(["Ngo", "Lan", 98])
    ws.append(["Vu", "An", 94])
    ws.append(["Tran", "Nam", 60])
    path = tmp_path / "scorers.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    q = (
        "Who are the three highest scorers? Give their last names from highest "
        "to lowest score, separated by a comma and one space."
    )
    ans = execute(plan(q, tables), tables, q)
    assert ans == "Ngo, Ngo, Vu"


def test_key_does_not_change_section_answer(tmp_path, monkeypatch):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Group", "People"])
    ws.append(["Men", None])
    ws.append(["City", 10])
    ws.append(["Town", 30])
    ws.append(["Women", None])
    ws.append(["City", 5])
    ws.append(["Town", 7])
    path = tmp_path / "groups.xlsx"
    wb.save(path)
    q = "What is the total number of people in Men? Answer with only the integer."
    question = {"id": "s1", "file_name": "groups.xlsx", "query": q}

    def fake_post(body, timeout):
        raise AssertionError("network")

    monkeypatch.setattr(llm_client, "_post", fake_post)
    monkeypatch.setattr(llm_client, "RETRY_SLEEP", 0)
    monkeypatch.setattr(C, "STRICT_CONFIDENCE", None)

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert C.strict_active() is False
    without, _ = run.answer_one(question, tmp_path, {})

    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert C.strict_active() is False
    with_key, _ = run.answer_one(question, tmp_path, {})
    assert with_key == without == "52"
