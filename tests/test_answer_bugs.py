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


def _scoped_industries(tmp_path):
    """Same industry names under three sections. Food is the global top value three times."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Industry", "Count"])
    blocks = {
        "Services": {"Food": 300, "Steel": 10, "Wood": 9},
        "Trade": {"Food": 200, "Steel": 8, "Wood": 7},
        "Manufacturing": {"Food": 100, "Steel": 40, "Wood": 30},
    }
    for sec, counts in blocks.items():
        ws.append([sec, None])
        for name, count in counts.items():
            ws.append([name, count])
    path = tmp_path / "scoped.xlsx"
    wb.save(path)
    return load_tables(str(path))


def test_rank_named_section_before_count(tmp_path):
    """Top 3 inside the named section, not the three highest rows in the workbook."""
    tables = _scoped_industries(tmp_path)
    q = (
        "Rank the industries in Manufacturing by count in descending order. "
        "Answer with the three industry names, separated by a comma and one space."
    )
    built = plan(q, tables)
    assert built["rows"]["scope"] == "Manufacturing"
    assert built["args"]["k"] == 3
    assert execute(built, tables, q) == "Food, Steel, Wood"

    unnamed = (
        "Rank the industries by count in descending order. "
        "Answer with the three industry names, separated by a comma and one space."
    )
    wide = execute(plan(unnamed, tables), tables, unnamed)
    assert wide == "Food, Food, Food"

    both = (
        "Rank the industries in Manufacturing and Trade by count in descending order. "
        "Answer with the three industry names, separated by a comma and one space."
    )
    assert plan(both, tables) is None


def test_rank_named_range_before_count(tmp_path):
    """An age band in the question limits the rows before the top-3 cut."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Age group", "People"])
    for label, n in [("0 to 15", 1), ("16 to 24", 9), ("25 to 34", 3),
                     ("35 to 44", 8), ("45 to 54", 2), ("55 to 64", 7)]:
        ws.append([label, n])
    path = tmp_path / "ages.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    q = (
        "Rank the age groups from 25 to 54 by people in descending order. "
        "Answer with the three age group names, separated by a comma and one space."
    )
    ans = execute(plan(q, tables), tables, q)
    assert ans == "35 to 44, 25 to 34, 45 to 54"


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
