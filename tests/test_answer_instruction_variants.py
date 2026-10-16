from pathlib import Path
import sys

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from src.answer_format import format_answer, parse_instruction
from src.answer_type import answer_ok
from src.code_planner import plan_with_confidence
from src.plan_executor import execute
from src.plan_validate import validate
from src.xlsx_tables import load_tables


@pytest.mark.parametrize("instruction,value,expected", [
    ("Return exactly an integer with no units.", 12, "12"),
    ("Return exactly a number with 2 decimal places. Round half up at the final decimal place.", 1.235, "1.24"),
    ("Return exactly a percentage with 2 decimal places and a % sign.", 12.5, "12.50%"),
    ("Return exactly a decimal ratio with 2 decimal places, no unit or % sign.", 1.25, "1.25"),
    ("Return exactly an integer followed by ' pp' for percentage points.", -3, "-3 pp"),
    ("Return names joined with ' > ' and no values.", ["Pine", "Oak"], "Pine > Oak"),
    ("Use exactly ' | ' between three integer values; no labels.", [2, 4, 6], "2 | 4 | 6"),
    ("Return exactly 'Pine: <signed integer>' where the integer is the difference.", -5, "Pine: -5"),
])
def test_explicit_format_has_exact_output(instruction, value, expected):
    spec = parse_instruction("What is the requested result? " + instruction)
    assert format_answer(value, spec) == expected


def test_decorated_numeric_output_still_checks_the_payload():
    fmt = {"kind": "decimal", "decimals": 2, "suffix": "%"}
    assert answer_ok("number", "12.50%", fmt)
    assert not answer_ok("number", "twelve%", fmt)
    assert not answer_ok("number", "12.50", fmt)


def test_return_instruction_does_not_leak_rounding_words_into_operands(tmp_path):
    wb = openpyxl.Workbook()
    for row in [["District", "Value"], ["Pine", 1.235], ["Oak", 4]]:
        wb.active.append(row)
    path = tmp_path / "values.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = "What is the Value for Pine? Return exactly a number with 2 decimal places. Round half up at the final decimal place."
    plan, confidence, reasons = plan_with_confidence(query, tables)
    assert confidence >= .5, reasons
    assert validate(plan) == []
    assert execute(plan, tables, query) == "1.24"


def test_legacy_plan_without_affixes_is_still_valid():
    assert validate({"version": 1, "op": "sum", "columns": ["Value"], "format": {"kind": "int"}}) == []
