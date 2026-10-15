"""Rule answers that must not depend on an LLM key. Duplicate surnames stay.

A named section is answered from that section only.
"""
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


def _industries_94(tmp_path):
    """94 data rows and 20 distinct labels, on several sheets and sections.

    Males lists those labels in its own row order. The other sections list
    them in a different order, on other sheets and other row numbers.
    """
    labels = [f"Category number {i:02d} alpha" for i in range(20)]
    males = list(reversed(labels))
    wb = openpyxl.Workbook()
    blocks = [
        ("2020", [("All persons", labels), ("Males", males)]),
        ("2021", [("All persons", labels), ("Females", labels)]),
        ("2022", [("Females", labels[:14])]),
    ]
    for s, (sheet, sections) in enumerate(blocks):
        ws = wb.active if s == 0 else wb.create_sheet()
        ws.title = sheet
        ws.append(["Industry", "Count"])
        for sec, chunk in sections:
            ws.append([sec, None])
            for label in chunk:
                ws.append([label, 10])
    path = tmp_path / "sections94.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    assert sum(len(t.df) for t in tables) == 94
    return males, tables


def test_named_section_listed_once(tmp_path):
    """The answer is the queried section, in that section's order."""
    males, tables = _industries_94(tmp_path)
    q = (
        "Rank the industries in Males by count in descending order, "
        "separated by a comma and one space."
    )
    ans = execute(plan(q, tables), tables, q)
    assert ans == ", ".join(males)
    assert ans.split(", ") == males


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
    assert len(tables[0].df) == len(set(tables[0].df["_row"]))
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
