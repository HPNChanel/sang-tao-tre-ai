"""Paraphrased operation wording on a made-up kiln log.

Every question is written for this file. Column names, numbers, and sentence
shapes are not taken from an evaluation set. Ambiguous matches must abstain.
"""
import os
import sys

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.code_planner import plan
from src.plan_executor import execute
from src.planner import plan_with_confidence
from src.xlsx_tables import load_tables


def _save(tmp_path, name, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(list(row))
    path = tmp_path / name
    wb.save(path)
    return str(path)


def _kiln(tmp_path):
    # Grams never equals 2, 3, 4, 5, or 10, so those counts are not also cell values.
    return _save(tmp_path, "kiln.xlsx", ["Tag", "Grams"], [
        ("Nia", 88), ("Pax", 63), ("Quill", 52), ("Ren", 41), ("Sol", 36),
        ("Uma", 29), ("Vex", 18), ("Wren", 14), ("Yul", 11), ("Zed", 7), ("Ohm", 6),
    ])


def _ask(path, q):
    tables = load_tables(path)
    planned = plan(q, tables, os.path.basename(path))
    assert planned, q
    return execute(planned, tables, q)


def _blank(path, q):
    tables = load_tables(path)
    assert plan(q, tables, os.path.basename(path)) is None
    _, conf, reasons = plan_with_confidence(q, tables)
    assert conf < 0.5 and reasons


def test_second_place_english_paraphrases(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "Which tag ranks second when Grams decides the order?") == "Pax"
    assert _ask(path, "Find the 2nd-largest Grams tag.") == "Pax"


def test_second_place_vietnamese(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "Tìm thẻ có Grams lớn thứ hai.") == "Pax"
    assert _ask(path, "Tim the co Grams lon thu hai.") == "Pax"
    assert _ask(path, "Thẻ xếp thứ hai theo Grams là thẻ nào?") == "Pax"


def test_gap_english_paraphrases(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "Nia minus Pax, measured in Grams.") == "25"
    assert _ask(path, "By how much does Nia exceed Pax on Grams?") == "25"
    assert _ask(path, "How much greater is Nia than Pax in Grams?") == "25"


def test_gap_vietnamese(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "Grams của Nia hơn Grams của Pax bao nhiêu?") == "25"
    assert _ask(path, "Grams cua Nia hon Grams cua Pax bao nhieu?") == "25"
    assert _ask(path, "Grams giữa Nia với Pax chênh nhau.") == "25"
    assert _ask(path, "Grams giua Nia voi Pax chenh nhau.") == "25"


def test_headings_number_words_and_reversed(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "four largest amounts of Grams") == "88, 63, 52, 41"
    assert _ask(path, "the largest four amounts of Grams") == "88, 63, 52, 41"
    assert _ask(path, "Grams: ba lớn nhất.") == "Nia, Pax, Quill"
    assert _ask(path, "Grams: ba lon nhat.") == "Nia, Pax, Quill"
    assert _ask(path, "Grams: năm nhỏ nhất.") == "Ohm, Zed, Yul, Wren, Vex"
    assert _ask(path, "Grams: muoi lon nhat.") == "Nia, Pax, Quill, Ren, Sol, Uma, Vex, Wren, Yul, Zed"


def test_two_tags_respectively(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "Grams for Nia and Pax, respectively.") == "88, 63"
    assert _ask(path, "Grams của Nia và Pax lần lượt.") == "88, 63"
    assert _ask(path, "Grams cua Nia va Pax lan luot.") == "88, 63"


def test_yes_no_vietnamese(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "Nia có Grams lớn hơn Pax không?") == "Yes"
    assert _ask(path, "Nia co Grams lon hon Pax khong? Respond yes or no.") == "Yes"


def test_plain_cell_stays_a_lookup(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "Grams recorded against Pax.") == "63"


def test_unfinished_phrase_abstains(tmp_path):
    path = _kiln(tmp_path)
    _blank(path, "By how much does Grams exceed?")
    _blank(path, "Nia minus.")


def test_ambiguous_new_keywords_abstain(tmp_path):
    # Two measures, and the phrase never names one of them.
    dual = _save(tmp_path, "dual.xlsx", ["Tag", "Grams", "Lux"], [
        ("Nia", 88, 3), ("Pax", 63, 9), ("Quill", 52, 8),
    ])
    _blank(dual, "Tim muc lon thu hai.")
    _blank(dual, "Tìm mức lớn thứ hai.")

    # The operation word is also a row label.
    labeled = _save(tmp_path, "labeled.xlsx", ["Tag", "Grams", "Lux"], [
        ("Exceed", 88, 3), ("Pax", 63, 9),
    ])
    _blank(labeled, "By how much would exceed move the dock shipment today?")

    # 41 is both a count candidate and a stored Grams value.
    _blank(dual, "Show the highest 41 tags from the dock log today.")

    # A group is named, but the sheet has no such rows.
    _blank(path_of(tmp_path), "Within the coastal cluster, show four largest Grams tags.")


def path_of(tmp_path):
    return _kiln(tmp_path)


def test_label_number_is_not_a_cutoff(tmp_path):
    path = _save(tmp_path, "shelves.xlsx", ["Shelf", "Jars"], [
        ("crate 8", 4),
        ("span 20 or more", 9),
        ("wide bin", 40),
        ("slot D", 2),
    ])
    tables = load_tables(path)
    q = "How many jars sit on span 20 or more?"
    assert _ask(path, q) == "9"
    planned = plan(q, tables)
    assert planned["rows"]["where"] == []

    q = "Which shelf of span 20 or more carries the largest jars?"
    planned = plan(q, tables)
    assert planned["args"].get("k") != 20
    assert all(w.get("value") != 20 and w.get("value") != 20.0 for w in planned["rows"]["where"])
    assert execute(planned, tables, q) == "wide bin"

    q = "Which 4 shelves hold the most jars?"
    assert _ask(path, q) == "wide bin, span 20 or more, crate 8, slot D"


def test_threshold_wording_still_counts(tmp_path):
    path = _kiln(tmp_path)
    assert _ask(path, "How many tags carry Grams of 60 or more?") == "2"


def test_scalar_count_stays_a_single_extreme(tmp_path):
    # A trailing integer instruction is one number. 'highest 3' must not become a
    # ranked list, even when 3 is also stored on a row.
    path = _save(tmp_path, "weights.xlsx", ["Tag", "Grams"], [
        ("Nia", 88), ("Pax", 3), ("Quill", 52),
    ])
    assert _ask(path, "highest 3 Grams. Respond with an integer.") == "88"
    assert _ask(path, "top 2 Grams. Respond with an integer.") == "88"


def test_sort_sentinel_can_return_that_column(tmp_path):
    # The sort key is a real column, so an integer question returns that column.
    path = _save(tmp_path, "crew.xlsx", ["Tag", "age"], [
        ("Nia", 40), ("Pax", 22), ("Quill", 31),
    ])
    assert _ask(path, "The oldest age in the crew log. Respond with an integer.") == "40"
