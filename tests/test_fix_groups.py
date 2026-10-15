"""Synthetic-table tests for the generalization fix groups (KSP1)."""
import openpyxl
import pytest

from src.planner import plan_with_confidence
from src.plan_executor import execute
from src.xlsx_tables import load_tables


def _ans(path, q):
    t = load_tables(str(path))
    p, _, _ = plan_with_confidence(q, t, strict=False)
    return execute(p, t, q)


@pytest.fixture
def sections(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Status by age"]); ws.append([])
    ws.append(["Age", "Employed", "Unemployed"])
    data = {"TOTAL": [(30, 9), (50, 7), (40, 3)], "Men": [(10, 5), (35, 2), (15, 1)], "Women": [(20, 4), (15, 5), (25, 2)]}
    ages = ["16 to 24 years", "25 to 54 years", "55 years and over"]
    for sec, vals in data.items():
        ws.append([sec])
        for a, (e, u) in zip(ages, vals):
            ws.append([a, e, u])
    f = tmp_path / "sec.xlsx"; wb.save(f); return f


def test_section_scope_sum(sections):
    assert _ans(sections, "What is the combined number of unemployed men across all age groups? Answer with only the integer.") == "8"


def test_section_scope_argmax(sections):
    assert _ans(sections, "Which age group of women had the most employed? Answer with only the age group.") == "55 years and over"


def test_numeric_row_label_not_condition(sections):
    assert _ans(sections, "Among men, the number unemployed aged 55 years and over was how many? Answer with only the integer.") == "1"
    assert _ans(sections, "How many women aged 55 years and over were employed? Answer with only the integer.") == "25"


# ---- Vietnamese (with and without diacritics)
def test_vi_section_and_range(sections):
    assert _ans(sections, "Số người có việc làm của nữ từ 25 đến 54 tuổi là bao nhiêu? Chỉ trả lời bằng số nguyên.") == "15"
    assert _ans(sections, "So nguoi that nghiep cua nam tu 55 tuoi tro len la bao nhieu? Chi tra loi bang so nguyen.") == "1"


def test_vi_argmax_nodiac(sections):
    assert _ans(sections, "Nhom tuoi nao cua nu co nhieu nguoi that nghiep nhat? Chi tra loi ten.") == "25 to 54 years"


def test_vi_average_is_value(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Name", "Score"])
    for n, s in [("A", 70), ("B", 80), ("C", 91)]:
        ws.append([n, s])
    f = tmp_path / "s.xlsx"; wb.save(f)
    assert _ans(f, "Điểm trung bình của tất cả mọi người là bao nhiêu? Chỉ trả lời bằng số, làm tròn đến 2 chữ số thập phân.") == "80.33"
