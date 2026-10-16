import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan
from src.plan_executor import execute
from src.xlsx_tables import load_tables


def test_period_in_measure_does_not_reverse_comparison(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["District", "Percent change from Apr to May"])
    ws.append(["Cedar", 7.8])
    ws.append(["Birch", 2.1])
    path = tmp_path / "districts.xlsx"
    wb.save(path)
    tables = load_tables(path)
    for query in [
        "How many percentage points higher was the percent change from Apr to May for Cedar than Birch? Answer with a number using one decimal place.",
        "How many percentage points lower was the percent change from Apr to May for Birch than Cedar? Answer with a number using one decimal place.",
    ]:
        built = plan(query, tables)
        assert built["op"] == "diff"
        assert execute(built, tables, query) == "5.7"


def test_difference_of_stored_shares_is_not_share_arithmetic(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["District", "Share | 2018", "Share | 2019"])
    ws.append(["Cedar", 12.7, 10.3])
    path = tmp_path / "shares.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = "Find the absolute difference between 2018 and 2019 in Cedar share. Answer with a number using two decimal places."
    built = plan(query, tables)
    assert built["op"] == "diff"
    assert execute(built, tables, query) == "2.40"
