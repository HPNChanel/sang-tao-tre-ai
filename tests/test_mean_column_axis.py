import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan
from src.plan_executor import execute
from src.xlsx_tables import load_tables


@pytest.mark.parametrize("headers,values,expected", [
    (["Copper", "Silver", "Gold"], [3, 9, 15], "9.00"),
    (["Total", "East", "West"], [12, 4, 8], "8.00"),
    (["T1", "T2", "T3"], [None, 2, 6], "4.00"),
])
def test_horizontal_mean_uses_each_stored_cell(tmp_path, headers, values, expected):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Batch"] + headers)
    ws.append(["Red"] + values)
    ws.append(["Blue", 100, 200, 300])
    path = tmp_path / "batch.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = "Compute Red's mean across all readings. Answer with the number rounded to 2 decimal places."
    built = plan(query, tables)
    assert built["columns"] == headers
    assert execute(built, tables, query) == expected


def test_named_measure_mean_stays_vertical(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Batch", "Copper", "Silver"])
    ws.append(["Red", 3, 80])
    ws.append(["Blue", 9, 20])
    path = tmp_path / "batch.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = "Find the mean Copper across all batches. Answer with only the number."
    built = plan(query, tables)
    assert built["columns"] == ["Copper"]
    assert execute(built, tables, query) == "6"

