import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan_with_confidence
from src.plan_executor import execute
from src.plan_validate import validate
from src.xlsx_tables import load_tables


def run(tmp_path, rows, query):
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    path = tmp_path / "metrics.xlsx"
    wb.save(path)
    tables = load_tables(path)
    plan, confidence, reasons = plan_with_confidence(query, tables)
    assert confidence >= .5, reasons
    assert validate(plan) == []
    return execute(plan, tables, query)


def test_previous_difference_sorts_years_and_rejects_gaps(tmp_path):
    rows = [["Year", "Sales"], [2018, 100], [2016, 160], [2019, 70], [2017, 150], [2022, 1]]
    q = "In which year did Sales fall the most compared with the previous year? Answer as `year, decrease`, as integers."
    assert run(tmp_path, rows, q) == "2018, 50"


def test_largest_gap_uses_derived_measure_not_largest_operand(tmp_path):
    rows = [["Region", "Revenue", "Cost"], ["Coast", 90, 80], ["Hill", 40, 10], ["Lake", None, 1]]
    q = "Which region has the largest gap between Revenue and Cost? Answer as `group, gap`, with one decimal place."
    assert run(tmp_path, rows, q) == "Hill, 30.0"
