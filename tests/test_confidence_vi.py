import os, sys
import openpyxl
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.code_planner import plan
from src.plan_executor import execute
from src.planner import plan_with_confidence
from src.answer_type import expected_type, answer_ok, compatible
from src.vi import fold, translate, is_vietnamese
from src.xlsx_tables import load_tables


@pytest.fixture
def t(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Chi nhánh", "2023", "2024"])
    for r in [("Hà Nội", 500, 700), ("Huế", 300, 200), ("Đà Nẵng", 650, 650)]:
        ws.append(list(r))
    p = tmp_path / "vn.xlsx"; wb.save(p); return load_tables(str(p))


def test_fold_and_translate():
    assert fold("Đà Nẵng") == "Da Nang"
    assert is_vietnamese("Chi nhánh nào cao nhất?")
    s, hits = translate("Tổng doanh thu tháng 1 là bao nhiêu? Chỉ trả lời bằng số nguyên.")
    assert "total" in s and "Jan" in s and "Answer with only the integer." in s and hits >= 3


def test_expected_types():
    assert expected_type("In which year were most sold?", {}) == "year"
    assert expected_type("How many branches are there?", {}) == "count"
    assert expected_type("How much did it change?", {}) == "number"
    assert expected_type("Did sales rise?", {}) == "yesno"
    assert expected_type("Which branch had the most?", {}) == "label"
    assert not compatible("yesno", "number") and compatible("count", "number")
    assert answer_ok("count", "3", {}) and not answer_ok("count", "3.5", {}) and not answer_ok("label", "42", {})


def test_vietnamese_end_to_end(t):
    q = "Chi nhánh nào có doanh thu năm 2024 cao nhất? Chỉ trả lời tên chi nhánh."
    assert execute(plan(q, t), t, q) == "Hà Nội"
    q = "Doanh thu năm 2024 của Huế có cao hơn năm 2023 không? Chỉ trả lời Yes hoặc No."
    assert execute(plan(q, t), t, q) == "No"
    q = "Có bao nhiêu chi nhánh có doanh thu năm 2024 từ 600 trở lên? Chỉ trả lời bằng số nguyên."
    assert execute(plan(q, t), t, q) == "2"


def test_low_confidence_routes_to_llm(t):
    # nothing in the question matches the table -> no guess
    assert plan("What is the meaning of the third quarterly widget?", t) is None
    _, c, r = plan_with_confidence("Is the price of tea rising?", t)
    assert c < 0.5


def test_post_exec_type_check_blanks_mismatch(t):
    q = "Which branch had the highest 2024 revenue? Answer with only the branch name."
    p = plan(q, t)
    p2 = dict(p, op="sum", args={})  # an LLM-style wrong plan: number for a label question
    assert execute(p2, t, q) is None
