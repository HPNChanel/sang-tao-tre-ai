"""Subset on one column, then a value from a different column.

Tables are built in the test. Wrong mechanisms disagree with the expected number:
the boundary row of the subset, an unfiltered aggregate, or age at the other date.
"""
import datetime as dt
import os
import sys

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.code_planner import plan
from src.plan_executor import execute
from src.vi import translate
from src.xlsx_tables import load_tables


def _ask(path, q):
    tables = load_tables(path)
    planned = plan(q, tables, os.path.basename(path))
    assert planned, q
    return execute(planned, tables, q)


def _save(tmp_path, name, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(list(row))
    path = tmp_path / name
    wb.save(path)
    return str(path)


def test_numeric_birth_year_then_max_of_another_column(tmp_path):
    # Hired is a decoy date listed first. Birth year is a number, not a date.
    path = _save(tmp_path, "years.xlsx", ["Name", "Hired", "Birth year", "Points"], [
        ("A", dt.datetime(1990, 1, 1), 1970, 5),
        ("B", dt.datetime(2001, 1, 1), 1982, 18),
        ("C", dt.datetime(2001, 5, 1), 1982, 9),
        ("D", dt.datetime(1990, 6, 1), 1999, 40),
    ])
    q = "For people born in 1982, points highest what is the value? Answer with only the integer."
    assert _ask(path, q) == "18"


def test_date_birth_year_then_max_ignores_earlier_date_column(tmp_path):
    path = _save(tmp_path, "dates.xlsx", ["Name", "Hired", "Birth", "Points"], [
        ("A", dt.datetime(2010, 1, 1), dt.datetime(1982, 4, 4), 3),
        ("B", dt.datetime(2011, 1, 1), dt.datetime(1982, 8, 8), 21),
        ("C", dt.datetime(1982, 1, 1), dt.datetime(1995, 1, 1), 50),
    ])
    q = "For people born in 1982, points highest what is the value? Answer with only the integer."
    assert _ask(path, q) == "21"


def test_topk_birth_subset_then_max_not_boundary_row(tmp_path):
    # Birth order of the three oldest: 11, 27, 6 (max 27, third is 6).
    # Age at Start puts 44 first, so that maximum is 44.
    path = _save(tmp_path, "cohort.xlsx", ["Name", "Birth", "Start", "Points"], [
        ("Dee", dt.datetime(1975, 1, 1), dt.datetime(1976, 6, 1), 11),
        ("Cy", dt.datetime(1984, 1, 1), dt.datetime(1985, 1, 1), 27),
        ("Ada", dt.datetime(1988, 1, 1), dt.datetime(1989, 1, 1), 6),
        ("Bo", dt.datetime(1992, 1, 1), dt.datetime(2022, 1, 1), 44),
    ])
    q = "Among the three oldest, the highest points is what value? Answer with only the integer."
    assert _ask(path, q) == "27"
    # A single oldest person still uses age at the other date on the row.
    one = "What points did the oldest person receive? Answer with only the integer."
    assert _ask(path, one) == "44"


def test_youngest_returns_other_date_not_age_at_that_date(tmp_path):
    # Latest birth is 2016-05-05 (other date 2019-11-04).
    # Smallest age at Start is the 1988 row (about one year), other date 1989-01-01.
    path = _save(tmp_path, "when.xlsx", ["Name", "Birth", "Start", "Points"], [
        ("Dee", dt.datetime(1975, 1, 1), dt.datetime(1976, 6, 1), 11),
        ("Ada", dt.datetime(1988, 1, 1), dt.datetime(1989, 1, 1), 6),
        ("Neo", dt.datetime(2016, 5, 5), dt.datetime(2019, 11, 4), 3),
    ])
    q = "What is the start date of the youngest person? Answer in YYYY-MM-DD format."
    assert _ask(path, q) == "2019-11-04"


def test_enrollment_cue_is_admission():
    text, hits = translate("hoc vien nhap hoc hom qua")
    assert hits >= 1
    assert "admission" in text
