import os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.subtotals import detect_subtotals
from src.answer_format import parse_instruction, format_answer


def _df(rows, sec=""):
    return pd.DataFrame([{"_row": i + 1, "_section": sec, "_level": 0, "_row_path": (sec + " > " if sec else "") + l,
                          "a": a, "b": b} for i, (l, a, b) in enumerate(rows)])


def test_nested_subtotals():
    d = detect_subtotals(_df([("Total, all", 100, 50), ("Young", 30, 10), ("Old, total", 70, 40),
                              ("Old A", 20, 15), ("Old B", 50, 25)]))
    assert d._is_subtotal.tolist() == [True, False, True, False, False]
    assert d._parent_row.tolist()[1:] == [1, 1, 3, 3]
    assert d._row_path.tolist()[3] == "Total, all > Old, total > Old A"


def test_rounding_tolerance_and_no_false_positive():
    d = detect_subtotals(_df([("Total", 10, 7), ("x", 3.4, 2), ("y", 6.5, 5), ("Rate", 3, 4)]))
    assert d._is_subtotal.tolist()[0]
    d2 = detect_subtotals(_df([("Exports", 10, 20), ("Imports", 15, 25), ("Balance", -5, -5)]))
    assert not d2._is_subtotal.any()


def test_sections_are_boundaries():
    df = pd.concat([_df([("Men", 5, 5), ("a", 2, 2), ("b", 3, 3)], "S1"),
                    _df([("c", 1, 1)], "S2")], ignore_index=True)
    d = detect_subtotals(df)
    assert d._is_subtotal.tolist() == [True, False, False, False]


def test_measure_rows_not_subtotal():
    d = detect_subtotals(_df([("Population", 1000, 900), ("Labor force", 600, 500), ("Rate", 60, 55.6)]))
    assert not d._is_subtotal.any()


def test_parse_formats():
    q = "How many? Answer with only the integer, without commas or units."
    s = parse_instruction(q); assert s.kind == "int"
    assert format_answer(12345.0, s) == "12345"
    s = parse_instruction("Mean? Answer with only the number rounded to two decimal places.")
    assert (s.kind, s.decimals) == ("decimal", 2) and format_answer(3.145, s) == "3.15"
    s = parse_instruction("Is A > B? Provide 'Yes' or 'No'. Answer with only Yes or No.")
    assert s.kind == "yesno" and format_answer(True, s) == "Yes"
    s = parse_instruction("Which one? Answer with only the category name.")
    assert s.kind == "name"
    s = parse_instruction("Rank them. Answer with the three sector names in descending order, separated by a comma and one space, without numbering.")
    assert s.kind == "list" and s.count == 3 and format_answer(["A", "B", "C"], s) == "A, B, C"
    s = parse_instruction("Values? Answer as two integers in the order `x, y`, separated by a comma and one space, without thousands separators or units.")
    assert s.kind == "list" and s.item_kind == "int" and s.order == ["x", "y"]
    assert format_answer([1000.0, 2.0], s) == "1000, 2"
    s = parse_instruction("Hours? Answer as `men, women`, using exactly two decimal places and separating the values with a comma and one space.")
    assert s.kind == "list" and s.decimals == 2 and format_answer([40.6, 37.6], s) == "40.60, 37.60"
