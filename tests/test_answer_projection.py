import datetime as dt
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan
from src.plan_executor import execute
from src.xlsx_tables import load_tables


@pytest.mark.parametrize("projection", ["Badge", "Alias"])
def test_instruction_projection_preserves_event_age(tmp_path, projection):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Person", projection, "Birth", "Enrollment", "Points"])
    for label, badge, birth, event, points in [
        ("Elm", "E1", 2000, 2025, 30),
        ("Ash", "A2", 1998, 2016, 10),
        ("Oak", "O3", 1999, 2020, 20),
    ]:
        ws.append([label, badge, dt.datetime(birth, 1, 1), dt.datetime(event, 1, 1), points])
    path = tmp_path / "people.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = f"Identify the youngest entrant at enrollment. Answer with only the {projection} name."
    built = plan(query, tables)
    assert built["args"]["return"] == projection
    assert execute(built, tables, query) == "A2"


def test_unmatched_output_instruction_keeps_body_projection(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Person", "Age", "Points"])
    ws.append(["Elm", 31, 80])
    ws.append(["Ash", 22, 90])
    path = tmp_path / "scores.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = "What points did the oldest entrant obtain? Answer with only the integer."
    built = plan(query, tables)
    assert execute(built, tables, query) == "80"
