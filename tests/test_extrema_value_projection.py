import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.answer_format import format_answer, parse_instruction
from src.code_planner import plan_with_confidence
from src.plan_executor import execute
from src.plan_validate import validate
from src.xlsx_tables import load_tables


def test_mixed_projection_keeps_label_and_rounds_only_measure():
    spec = parse_instruction("Which group wins? Answer as `group, value`, with exactly two decimal places.")
    assert format_answer(["North, coastal", 2.125], spec) == "North, coastal, 2.13"
    with pytest.raises(ValueError):
        format_answer(["North"], spec)


def test_extrema_can_return_another_measure(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Year", "Score", "Cost"])
    ws.append([2001, 8, 37])
    ws.append([2002, 6, 51])
    path = tmp_path / "scores.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = "Which year has the highest Score? Answer as `year, cost`, separated by a comma and one space, with cost as an integer."
    plan, conf, reasons = plan_with_confidence(query, tables)
    assert conf >= .5, reasons
    assert validate(plan) == []
    assert execute(plan, tables, query) == "2001, 37"
