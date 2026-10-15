"""Placeholder tests for Vietnamese question handling.

A teammate will replace these with the official questions. The sentences below
are generic phrasing written for this file; they are not evaluation items.
"""
import os
import sys

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.code_planner import plan
from src.plan_executor import execute
from src.vi import is_vietnamese, translate
from src.xlsx_tables import load_tables


def test_placeholder_no_diacritic_cues_count_as_vietnamese():
    # "nguoi" / "nao" / "tuoi" / "nhat" are detection cues and carry no diacritics.
    assert is_vietnamese("Nguoi nao tuoi nhat?")
    assert not is_vietnamese("Where is the depot?")


def test_placeholder_statistics_age_span_and_value_wording():
    text, hits = translate(
        "Diện tích nông nghiệp và dân số phụ nữ từ 18 tới 24 tuổi là bao nhiêu?"
    )
    assert "area" in text
    assert "agricultural" in text
    assert "and" in text
    assert "population" in text
    assert "women" in text
    assert "aged 18 to 24" in text
    assert "what is the value" in text
    assert "how many" not in text
    assert hits >= 5


def test_placeholder_men_age_floor_and_calendar_year():
    text, _ = translate("Số lượng người đàn ông từ 65 tuổi trở lên bằng bao nhiêu?")
    assert "number of people" in text
    assert "men" in text
    assert "aged 65 years and over" in text
    assert "what is the value" in text

    exports, _ = translate("Xuất khẩu năm 2018 của nam giới là bao nhiêu?")
    assert "export" in exports
    assert "in 2018" in exports
    assert "men" in exports
    assert "of men" in exports


def test_placeholder_long_most_phrase_and_no_surname_rule():
    # More than 40 characters sit between "nhiều" and "nhất".
    ranked, _ = translate(
        "Nhóm tuổi nào có nhiều lao động phi nông nghiệp trong bảng này nhất?"
    )
    assert "age group" in ranked
    assert "most" in ranked
    assert "workers" in ranked
    assert "nonagricultural" in ranked

    surname, _ = translate("Chỉ ghi họ.")
    assert "last name" not in surname.lower()
    assert "surname" not in surname.lower()


def test_placeholder_value_question_returns_the_cell(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Region", "Population"])
    ws.append(["North", 1200])
    ws.append(["South", 800])
    path = tmp_path / "regions.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    question = "Dân số của North là bao nhiêu? Chỉ trả lời bằng số nguyên."
    # strict=False so the case does not depend on OPENROUTER_API_KEY.
    assert execute(plan(question, tables, strict=False), tables, question) == "1200"
