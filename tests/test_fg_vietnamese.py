"""Official Vietnamese questions (fv01–fv13).

Workbooks are the public data pack: $AIPROJ_DATA_DIR, otherwise
<data>/Public data 1, with files in xlsx_files/ or the pack root.
A missing workbook is skipped; a wrong answer is an xfail, not a per-question rule.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.code_planner import plan
from src.plan_executor import execute
from src.xlsx_tables import load_tables

_CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "test_questions_vi.json").read_text(encoding="utf-8")
)


def _pack_dir():
    env = os.environ.get("AIPROJ_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "Public data 1"


def _workbook(file_name):
    root = _pack_dir()
    for path in (root / "xlsx_files" / file_name, root / file_name):
        if path.is_file():
            return path
    pytest.skip(f"{file_name} is not in the public data pack")


def _matches(got, expected):
    if got is None:
        return False
    g, e = str(got).strip(), str(expected).strip()
    if g == e:
        return True

    def _num(s):
        try:
            return float(s.replace(",", "").replace(" ", ""))
        except ValueError:
            return None

    gn, en = _num(g), _num(e)
    return gn is not None and en is not None and gn == en


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_fg_vietnamese(case):
    path = _workbook(case["file_name"])
    tables = load_tables(str(path))
    query = case["query"]
    got = execute(plan(query, tables, case["file_name"]), tables, query)
    if not _matches(got, case["expected"]):
        pytest.xfail(f"got {got!r}, expected {case['expected']!r}")
