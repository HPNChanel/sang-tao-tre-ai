import datetime as dt

import openpyxl
import pytest

from src.calendar_ops import interval
from src.code_planner import plan
from src.plan_executor import execute
from src.plan_validate import validate
from src.xlsx_tables import load_tables


@pytest.fixture
def tables(tmp_path):
    wb = openpyxl.Workbook()
    for row in [["Person", "Birth", "Event"],
                ["Iris", dt.date(2000, 3, 2), dt.date(2025, 3, 1)],
                ["Opal", dt.date(1999, 12, 31), dt.date(2024, 12, 31)],
                ["Cedar", dt.date(2000, 1, 1), dt.date(2025, 10, 1)]]:
        wb.active.append(row)
    path = tmp_path / "dates.xlsx"; wb.save(path)
    return load_tables(path)


@pytest.mark.parametrize("birth,event,expected", [
    (dt.date(2000, 3, 2), dt.date(2025, 3, 1), 24),
    (dt.date(2000, 3, 2), dt.date(2025, 3, 2), 25),
    (dt.date(2000, 2, 29), dt.date(2025, 2, 28), 24),
    (dt.date(2000, 2, 29), dt.date(2025, 3, 1), 25),
])
def test_completed_years_use_birthdays(birth, event, expected):
    assert interval(birth, event, "completed_years") == expected


def test_birth_and_event_are_explicit_operands(tables):
    q = "How old in completed years was Iris on her Event date? Return exactly an integer."
    p = plan(q, tables)
    assert validate(p) == [] and p["op"] == "date_diff"
    assert execute(p, tables, q) == "24"


def test_completed_age_tie_uses_row_order_not_fractional_years(tables):
    q = "Who had the greatest age in completed years on their Event date? Break ties by first workbook row. Return exactly the name."
    p = plan(q, tables)
    assert validate(p) == [] and p["args"]["metric"] == "completed_years"
    assert execute(p, tables, q) == "Opal"


def test_signed_days_can_be_negative(tables):
    q = "How many signed calendar days from Iris's Event date to Opal's Event date? Return exactly an integer."
    p = plan(q, tables)
    assert validate(p) == []
    assert execute(p, tables, q) == "-60"


def test_invalid_age_and_missing_date_do_not_invent_values():
    with pytest.raises(ValueError):
        interval(dt.date(2020, 1, 1), dt.date(2019, 1, 1), "completed_years")
    with pytest.raises(ValueError):
        interval(None, dt.date(2025, 1, 1), "days")
