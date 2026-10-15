import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan, plan_with_confidence
from src.plan_executor import execute
from src.xlsx_tables import load_tables


@pytest.fixture
def tables(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Product", "Inventory | All depots", "Inventory | Harbor", "Inventory | Airport"])
    ws.append(["Bolts", 80, 30, 50])
    ws.append(["Nuts", 60, 20, 40])
    path = tmp_path / "inventory.xlsx"
    wb.save(path)
    return load_tables(path)


def test_nested_population_is_a_constraint(tables):
    query = "Find Bolts inventory in all depots and in Harbor. Answer with an integer."
    built = plan(query, tables)
    assert built["op"] == "lookup"
    assert built["columns"] == ["Inventory | Harbor"]
    assert execute(built, tables, query) == "30"


def test_explicit_addition_preserves_both_columns(tables):
    query = "Sum Bolts inventory in all depots and in Harbor. Answer with an integer."
    built = plan(query, tables)
    assert len(built["columns"]) == 2
    assert execute(built, tables, query) == "110"


def test_ambiguous_pair_still_abstains(tables):
    query = "Give Bolts inventory in all depots and in Harbor, respectively. Answer with two integers separated by a comma."
    built, confidence, reasons = plan_with_confidence(query, tables)
    # Existing operation ambiguity must not become a confident one-column answer.
    assert confidence == 0
    assert "op:ambiguous" in reasons


def test_unrelated_column_groups_are_not_intersected(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Product", "Stock | All depots", "Sales | Harbor"])
    ws.append(["Bolts", 80, 30])
    path = tmp_path / "unrelated.xlsx"
    wb.save(path)
    query = "Find Bolts in all depots and in Harbor. Answer with an integer."
    built = plan(query, load_tables(path))
    assert len(built["columns"]) == 2
