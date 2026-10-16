import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan_with_confidence
from src.executor import execute, PlanError
from src.xlsx_tables import load_tables


@pytest.fixture
def tables(tmp_path):
    wb = openpyxl.Workbook()
    for row in [["Place", "Revenue", "Cost"], ["North"], ["Bay", 12, 5], ["Hill", 8, 3], ["South"], ["Bay", 10, 2], ["Lake", 6, None]]:
        wb.active.append(row)
    path = tmp_path / "regions.xlsx"
    wb.save(path)
    return load_tables(path)


def test_unknown_scope_does_not_expand_to_entire_table(tables):
    plan = {"op": "max", "rows": {"scope": "Unknown"}, "columns": ["Revenue"], "format": {"kind": "number"}}
    with pytest.raises(PlanError, match="scope"):
        execute(plan, tables)


def test_ambiguous_leaf_requires_a_path(tables):
    plan = {"op": "lookup", "rows": {"paths": ["Bay"]}, "columns": ["Revenue"], "format": {"kind": "number"}}
    with pytest.raises(PlanError, match="ambiguous"):
        execute(plan, tables)


def test_unbound_conjunct_abstains_instead_of_ignoring_it(tables):
    _, confidence, reasons = plan_with_confidence("Identify the place where Revenue is above 7 and Unknown metric is below 4. Answer with only the name.", tables)
    assert confidence == 0, reasons


def test_derived_expression_requires_exact_arity(tables):
    plan = {"op": "max", "columns": ["Revenue", "Cost", "Revenue"], "args": {"metric": "difference"}, "format": {"kind": "number"}}
    with pytest.raises(ValueError, match="operand count"):
        execute(plan, tables)
