import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan
from src.plan_executor import execute
from src.planner import _quoted_column_operands, build_views
from src.xlsx_tables import load_tables


@pytest.mark.parametrize("labels", [
    ["Cooling units", "Cooling units, except portable"],
    ["Red equipment", "Red equipment, except leased"],
])
def test_overlapping_quoted_columns_are_distinct_operands(tmp_path, labels):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Depot", "Total units"] + ["Devices | " + x for x in labels])
    ws.append(["Harbor", 900, 7, 13])
    ws.append(["Airport", 800, 12, 18])
    path = tmp_path / "devices.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = f"Sum '{labels[0]}' and '{labels[1]}' at Harbor for the total units. Answer with an integer."
    built = plan(query, tables)
    assert built["columns"] == ["Devices | " + x for x in labels]
    assert execute(built, tables, query) == "20"


def test_ambiguous_or_row_quotes_are_not_column_lists(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Depot", "A | Stock", "B | Stock", "Cost"])
    ws.append(["Harbor", 1, 2, 3])
    path = tmp_path / "devices.xlsx"
    wb.save(path)
    view = build_views(load_tables(path))[0]
    assert _quoted_column_operands(view, "Sum 'Stock' and 'Cost'") is None
    assert _quoted_column_operands(view, "Compare 'Harbor' and 'Airport'") is None
