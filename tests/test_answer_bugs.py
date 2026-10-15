"""Rule answers that must not depend on an LLM key. Duplicate surnames stay.

A rank list with format.count stops at that count.
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


def _repeated_labels(tmp_path):
    """One table, no sections. 19 labels, copied across 6 groups: 114 data rows."""
    labels = [f"Industry {i:02d}" for i in range(19)]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Industry", "Count", "Share"])
    for cycle in range(6):
        for i, label in enumerate(labels):
            ws.append([label, 100 - i, cycle + 1])
    path = tmp_path / "cycles114.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    assert len(tables) == 1
    assert len(tables[0].df) == 114
    return labels, tables


def test_rank_missing_k_stops_at_format_count(tmp_path):
    """A rank plan that only has order must stop at format.count.

    The raw plan has no args.k. The planner fills k from that count.
    """
    _labels, tables = _repeated_labels(tmp_path)
    raw = {
        "version": 1,
        "table": {"index": 0, "sheet": None, "title_hint": None, "stack_sheets": False},
        "rows": {"paths": [], "scope": None, "range": None, "leaf_only": False,
                 "exclude_subtotals": True, "where": []},
        "columns": ["Count", "Share"],
        "op": "rank",
        "args": {"order": "desc"},
        "format": {"kind": "list", "decimals": None, "count": 19, "item_kind": "name",
                   "sep": ", ", "order": []},
    }
    ans = execute(raw, tables, None)
    items = ans.split(", ")
    assert len(items) == 19
    assert items.count("Industry 00") == 6
    assert "Industry 18" not in items

    q = (
        "Rank the industries by count and share in descending order. "
        "Answer with the 19 industry names, separated by a comma and one space."
    )
    built = plan(q, tables)
    assert built["op"] == "rank"
    assert built["args"].get("order") == "desc"
    assert "over" not in built["args"]
    assert built["args"]["k"] == 19
    assert built["format"]["kind"] == "list"
    assert built["format"]["count"] == 19
    assert len(built["columns"]) == 2
    assert not built["rows"]["paths"]
    assert built["rows"]["scope"] is None
    listed = execute(built, tables, q).split(", ")
    assert len(listed) == 19
    assert listed.count("Industry 00") == 6


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
