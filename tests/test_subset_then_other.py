"""Subset, nth rank, difference, and value pairs on Public data 1.

Each expected string was computed from the workbook with load_tables
(birth year, age at admission, column cells), then checked as an exact answer.
The set is skipped only when that directory is absent.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.code_planner import plan
from src.plan_executor import execute
from src.vi import translate
from src.xlsx_tables import load_tables


def _pack_dir():
    env = os.environ.get("AIPROJ_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "Public data 1"


def _workbook(root, file_name):
    for path in (root / "xlsx_files" / file_name, root / file_name):
        if path.is_file():
            return path
    raise FileNotFoundError(f"{file_name} is not in {root}")


CASES = [
    (
        "born-1996-max",
        "p4_t01.xlsx",
        "For people born in 1996, what is the highest Score? Answer with only the integer.",
        "94",
    ),
    (
        "born-2004-min",
        "p4_t01.xlsx",
        "Born in 2004, minimum Score, integer.",
        "92",
    ),
    (
        "born-2005-max",
        "p4_t01.xlsx",
        "Among people born in 2005, what is the highest Score? Answer with only the integer.",
        "92",
    ),
    (
        "born-1998-max",
        "p4_t01.xlsx",
        "Among people born in 1998, the highest score is what value? Answer with only the integer.",
        "83",
    ),
    (
        "four-youngest-min",
        "p4_t01.xlsx",
        "Among the four youngest, minimum Score, integer.",
        "61",
    ),
    (
        "two-oldest-sum",
        "p4_t01.xlsx",
        "Among the two oldest, what is the total score? Answer with only the integer.",
        "124",
    ),
    (
        "women-45-54-unpaid",
        "p4_t06.xlsx",
        "For women aged 45 to 54, unpaid family workers in agriculture what is the value? Answer with only the integer.",
        "3",
    ),
    (
        "men-65-self-employed",
        "p4_t06.xlsx",
        "For men aged 65 years and over, self-employed workers in agriculture what is the value? Answer with only the integer.",
        "185",
    ),
    (
        "men-16-19-at-work",
        "p4_t10.xlsx",
        "Men aged 16 to 19, total at work what is the value? Answer with only the integer.",
        "2640",
    ),
    (
        "second-highest-admission",
        "p4_t01.xlsx",
        "What is the admission date of the person with the second highest score? Answer in YYYY-MM-DD format.",
        "2025-10-21",
    ),
    (
        "second-oldest-score",
        "p4_t01.xlsx",
        "Second oldest row: its Score, written as an integer.",
        "83",
    ),
    (
        "third-oldest-name",
        "p4_t01.xlsx",
        "Who is the third oldest person? Answer with only the first name.",
        "Khanh",
    ),
    (
        "third-oldest-score",
        "p4_t01.xlsx",
        "Third oldest person's Score, integer.",
        "81",
    ),
    (
        "second-highest-first-name",
        "p4_t01.xlsx",
        "Who has the second highest score? Answer with only the first name.",
        "Ha",
    ),
    (
        "second-cpi-category",
        "p4_t03.xlsx",
        "Which expenditure category has the second highest unadjusted percent change from Oct. 2023 to Oct. 2024? Answer with only the name.",
        "Transportation services",
    ),
    (
        "score-gap-nga-nam",
        "p4_t01.xlsx",
        "How many more points did Nga Ngo score than Nam Nguyen? Answer with only the integer.",
        "41",
    ),
    (
        "fiscal-2020-gap",
        "p4_t02.xlsx",
        "What is the difference between agricultural exports and agricultural imports in fiscal year 2020? Answer with only the number rounded to 1 decimal place.",
        "3.7",
    ),
    (
        "fiscal-2021-gap",
        "p4_t02.xlsx",
        "What is the difference between agricultural exports and agricultural imports in fiscal year 2021? Answer with only the number rounded to 1 decimal place.",
        "8.5",
    ),
    (
        "hours-40-vs-35",
        "p4_t08.xlsx",
        "How many more persons at work are there in all industries for 40 hours than for 35 to 39 hours? Answer with only the integer.",
        "67159",
    ),
    (
        "score-pair-lan-quang",
        "p4_t01.xlsx",
        "What are the scores of Lan Nguyen and Quang Bui? Answer with the two integers separated by a comma and one space.",
        "76, 61",
    ),
]


@pytest.mark.parametrize("file_name,query,expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_subset_nth_diff_pair(file_name, query, expected):
    root = _pack_dir()
    if not root.is_dir():
        pytest.skip("public data directory is absent")
    path = _workbook(root, file_name)
    tables = load_tables(str(path))
    planned = plan(query, tables, file_name)
    assert planned, query
    got = execute(planned, tables, query)
    assert got == expected, f"got {got!r}, expected {expected!r}"


def test_enrollment_cue_is_admission():
    text, hits = translate("hoc vien nhap hoc hom qua")
    assert hits >= 1
    assert "admission" in text
