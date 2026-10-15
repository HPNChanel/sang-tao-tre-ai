import os, sys, warnings
import openpyxl
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.code_planner import plan as _plan, plan_with_confidence as _pwc


def plan(q, t):
    return _plan(q, t, strict=True)


def plan_with_confidence(q, t):
    return _pwc(q, t, strict=True)
from src.plan_executor import execute
from src.xlsx_tables import load_tables


@pytest.fixture
def t(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Store", "Sales 2023 (estimate)", "Sales 2023 (final)", "Sales 2024", "Staff"])
    ws.append(["Riverside", 100, 120, 0, 5]); ws.append(["Hilltop", 300, 280, 310, 9])
    p = tmp_path / "s.xlsx"; wb.save(p); return load_tables(str(p))


@pytest.fixture
def sec(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Age", "Employed", "Unemployed"])
    for s, vals in [("Men", [(10, 5), (20, 1)]), ("Women", [(30, 2), (40, 9)])]:
        ws.append([s]); ws.append(["16 to 24", *vals[0]]); ws.append(["25 to 54", *vals[1]])
    p = tmp_path / "e.xlsx"; wb.save(p); return load_tables(str(p))


def test_clear_question_is_confident(t):
    q = "What were the final 2023 sales of Hilltop? Answer with only the integer."
    p, c, r = plan_with_confidence(q, t)
    assert c >= 0.5 and execute(p, t, q) == "280"


@pytest.mark.parametrize("q", [
    "What were the 2023 sales of Hilltop? Answer with only the integer.",          # two similar columns
    "What were the 2025 sales of Hilltop? Answer with only the integer.",          # year not in table
    "What were the 2024 sales of Seaside? Answer with only the integer.",          # row not in table
    "Which store is the most efficient? Answer with only the store name.",         # undefined metric
    "How many employees work at Hilltop? Answer with only the integer.",           # synonym, not a header
])
def test_guesses_route_to_fallback_but_keep_best_guess(t, q):
    p, c, r = plan_with_confidence(q, t)
    assert c < 0.5 and r and p is not None   # best guess still exposed
    assert plan(q, t) is None


def test_sections_need_scope(sec):
    assert plan("How many people aged 25 to 54 were employed? Answer with only the integer.", sec) is None
    q = "Which age group had the most unemployed women? Answer with only the age group."
    assert execute(plan(q, sec), sec, q) == "25 to 54"
    q = "How many women aged 25 to 54 were employed? Answer with only the integer."
    assert execute(plan(q, sec), sec, q) == "40"


def test_rate_without_column_is_not_guessed(sec):
    assert plan("What is the unemployment rate for women aged 16 to 24? Answer with only the number rounded to 1 decimal place.", sec) is None


def test_ratio_divide_by_zero_is_clean(t):
    q = "What is the ratio (Sales 2023 (final) / Sales 2024) for Riverside? Answer with only the number rounded to 2 decimal places."
    p, _, _ = plan_with_confidence(q, t)
    p = dict(p, op="ratio", columns=["Sales 2023 (final)", "Sales 2024"], rows=dict(p["rows"], paths=["Riverside"]))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert execute(p, t, q) is None
