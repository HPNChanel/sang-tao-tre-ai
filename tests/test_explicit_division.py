import openpyxl

from src.code_planner import plan
from src.plan_executor import execute
from src.xlsx_tables import load_tables


def test_division_ignores_column_order_and_does_not_need_ratio_in_format(tmp_path):
    wb = openpyxl.Workbook()
    for row in [["Place", "Crates", "Visits"], ["Bến Gió", 16, 175], ["Pond", 5, 11]]:
        wb.active.append(row)
    path = tmp_path / "counts.xlsx"; wb.save(path)
    tables = load_tables(path)
    q = "For Bến Gió, divide Visits by Crates. Return exactly two decimal places."
    p = plan(q, tables)
    assert p["op"] == "ratio" and p["columns"] == ["Visits", "Crates"]
    assert execute(p, tables, q) == "10.94"
    q = "For Bến Gió, divide Crates by Visits. Return exactly two decimal places."
    assert execute(plan(q, tables), tables, q) == "0.09"


def test_division_with_ambiguous_row_abstains(tmp_path):
    wb = openpyxl.Workbook()
    for row in [["Place", "First", "Second"], ["Bay", 3, 8], ["Hill", 4, 9]]:
        wb.active.append(row)
    path = tmp_path / "counts.xlsx"; wb.save(path)
    from src.native_comparisons import _division
    assert _division("Divide First by Second. Return an integer.", load_tables(path)) is None
