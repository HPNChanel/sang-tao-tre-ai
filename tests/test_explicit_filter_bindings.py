import openpyxl
import pytest

from src.code_planner import plan
from src.plan_executor import execute
from src.plan_review import semantic_errors
from src.plan_validate import validate
from src.xlsx_tables import load_tables


@pytest.fixture
def tables(tmp_path):
    wb = openpyxl.Workbook()
    for row in [["Shop", "Region", "Type", "Score", "Crates", "Visits"],
                ["Birch", "West", "Market", 62, 12, 70], ["Maple", "West", "Depot", 91, 37, 13],
                ["Aspen", "East", "Depot", 53, 30, 100], ["Spruce", "East", "Depot", 75, 14, 6]]:
        wb.active.append(row)
    path = tmp_path / "shops.xlsx"; wb.save(path)
    return load_tables(path)


@pytest.mark.parametrize("query,expected", [
    ("Count rows where Region is West AND Score is at least 70. Return exactly an integer.", "1"),
    ("Among Type Depot rows with Crates strictly greater than 15, sum Visits. If none qualify, use 0. Return exactly an integer.", "113"),
    ("List shops with Region East AND Score at most 60, in workbook row order. Return names joined by ', '; if none, return NONE.", "Aspen"),
    ("For Type Depot and Score from 60 through 95 inclusive, find the maximum Crates. If no row qualifies, return NONE. Otherwise return exactly an integer.", "37"),
    ("For Type Depot and Score from 10 through 20 inclusive, find the maximum Crates. If no row qualifies, return NONE. Otherwise return exactly an integer.", "NONE"),
    ("List shops with Region West AND Score at most 10. Return names joined by ', '; if none, return NONE.", "NONE"),
])
def test_each_condition_keeps_its_column(tables, query, expected):
    p = plan(query, tables)
    assert validate(p) == []
    assert execute(p, tables, query) == expected


def test_dropped_categorical_condition_is_rejected(tables):
    q = "Count rows where Region is West AND Score is at least 70. Return exactly an integer."
    p = plan(q, tables)
    p["rows"]["where"] = [w for w in p["rows"]["where"] if w["column"] != "Region"]
    assert semantic_errors(p, q, tables)


def test_unknown_or_disjunctive_scope_does_not_get_a_conjunction(tables):
    from src.explicit_filters import plan as explicit
    assert explicit("Count rows where Region is Faraway AND Score at least 70.", tables) is None
    assert explicit("Count rows where Region is West OR Score at least 70.", tables) is None
