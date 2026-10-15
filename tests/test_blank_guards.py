"""Guards that turn confident wrong plans into a blank.

Synthetic sheds and cargo only. Wording is written for these sheets.
Before the guards, the same plan/execute path returned the strings noted
on each test. After, that path is blank or still the near-miss answer.
"""
import os
import sys

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.answer_type import answer_ok
from src.code_planner import plan, plan_with_confidence
from src.plan_executor import execute
from src.xlsx_tables import load_tables


def _answer(tables, query):
    planned = plan(query, tables)
    if not planned:
        return None
    return execute(planned, tables, query)


def _reasons(tables, query):
    _plan, _conf, reasons = plan_with_confidence(query, tables)
    return reasons


def _ledger(path):
    """Title row, two header levels, four sheds."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Shed cargo ledger"
    ws.merge_cells("A1:E1")
    ws["A3"] = "Shed"
    ws.merge_cells("A3:A4")
    ws["B3"] = "Cargo"
    ws.merge_cells("B3:C3")
    ws["D3"] = "Fruit"
    ws.merge_cells("D3:E3")
    ws["B4"] = "Ripe"
    ws["C4"] = "Green"
    ws["D4"] = "Ripe"
    ws["E4"] = "Green"
    for i, (name, ripe, green, fruit, other) in enumerate(
        [("Amber", 40, 5, 8, 2), ("Birch", 25, 9, 30, 1),
         ("Cedar", 18, 3, 12, 7), ("Dune", 9, 4, 4, 6)],
        start=5,
    ):
        ws.cell(i, 1, name)
        ws.cell(i, 2, ripe)
        ws.cell(i, 3, green)
        ws.cell(i, 4, fruit)
        ws.cell(i, 5, other)
    wb.save(path)
    return load_tables(str(path))


def _stub_header(path):
    """A title, a real header, then a numeric stub row the reader keeps as data."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Shed ledger title"
    ws.merge_cells("A1:C1")
    ws["A2"] = "Shed"
    ws["B2"] = "Ripe"
    ws["C2"] = "Green"
    ws["A3"] = "(header)"
    ws["B3"] = 1
    ws["C3"] = 2
    ws["A4"] = "Amber"
    ws["B4"] = 40
    ws["C4"] = 5
    ws["A5"] = "Birch"
    ws["B5"] = 25
    ws["C5"] = 9
    ws["A6"] = "Cedar"
    ws["B6"] = 18
    ws["C6"] = 4
    wb.save(path)
    tables = load_tables(str(path))
    assert tables[0].df["_row_path"].tolist()[0] == "(header)"
    return tables


def _orchard(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Band", "Pears", "Plums"])
    for row in [("Total, span 9 and over", 100, 80), ("span 1 to 3", 10, 12),
                ("span 4 to 6", 40, 30), ("span 7 to 8", 20, 22)]:
        ws.append(list(row))
    wb.save(path)
    return load_tables(str(path))


def _dual(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Tag", "Grams", "Lux"])
    for row in [("Nia", 88, 3), ("Pax", 63, 9), ("Quill", 52, 8), ("Ren", 41, 1)]:
        ws.append(list(row))
    wb.save(path)
    return load_tables(str(path))


def _grams(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Tag", "Grams"])
    for row in [("Nia", 88), ("Pax", 63), ("Quill", 52), ("Ren", 41)]:
        ws.append(list(row))
    wb.save(path)
    return load_tables(str(path))


def test_topn_signal_blanks_when_plan_is_not_resolved(tmp_path):
    # Before: lookup of the first Cargo Ripe cell, '40', confidence 0.60.
    tables = _ledger(tmp_path / "ledger.xlsx")
    q = "Ba loai shed co Cargo Ripe lon nhat. Liet ke giam dan, phan cach bang dau phay va mot dau cach."
    assert _answer(tables, q) is None
    assert "blank:topn" in _reasons(tables, q)


def test_topn_list_is_not_a_numeric_aggregate(tmp_path):
    # Before: sum of the bushels column, '96'.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Pier", "Bushels"])
    for row in [("Amber", 40), ("Birch", 25), ("Cedar", 18), ("Dune", 9), ("Elm", 4)]:
        ws.append(list(row))
    path = tmp_path / "piers.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    q = "Name three pier labels using the total bushels figure, ordered downward and comma-separated."
    assert _answer(tables, q) is None
    assert "blank:shape" in _reasons(tables, q)


def test_resolved_topn_on_multilevel_header_still_answers(tmp_path):
    tables = _ledger(tmp_path / "ledger.xlsx")
    q = ("Name the three sheds with the largest Cargo Ripe, descending, "
         "separated by a comma and one space.")
    assert _answer(tables, q) == "Amber, Birch, Cedar"


def test_complete_rank_is_not_second_guessed(tmp_path):
    # The question says 'ripe' and two Ripe columns exist. The planner still
    # emits a rank with N, a column, and a direction. That plan is kept.
    tables = _ledger(tmp_path / "ledger.xlsx")
    q = "Name three sheds with the largest ripe figure, comma-separated."
    assert _answer(tables, q) == "Amber, Birch, Cedar"
    assert "blank:topn" not in _reasons(tables, q)


def test_topn_with_two_directions_blanks(tmp_path):
    # 'largest' and 'ascending' disagree, so the direction is not resolved.
    tables = _ledger(tmp_path / "ledger.xlsx")
    q = "Name the three largest Cargo Ripe sheds, ascending, separated by a comma."
    assert _answer(tables, q) is None
    assert "blank:topn" in _reasons(tables, q)


def test_dong_dau_signal_blanks_when_the_plan_is_not_rank(tmp_path):
    # Before: lookup of the first Cargo Ripe cell. The signal is '3 dòng đầu'.
    tables = _ledger(tmp_path / "ledger.xlsx")
    q = "3 dong dau theo Cargo Ripe."
    assert _answer(tables, q) is None
    assert "blank:topn" in _reasons(tables, q)


def test_vietnamese_topn_with_column_and_direction_answers(tmp_path):
    tables = _grams(tmp_path / "grams.xlsx")
    assert _answer(tables, "Grams: ba lớn nhất.") == "Nia, Pax, Quill"
    assert _answer(tables, "Grams: ba lon nhat.") == "Nia, Pax, Quill"


def test_scalar_extreme_stays_a_single_number(tmp_path):
    tables = _grams(tmp_path / "grams.xlsx")
    assert _answer(tables, "highest 3 Grams. Respond with an integer.") == "88"
    assert _answer(tables, "top 2 Grams. Respond with an integer.") == "88"


def test_shorter_topn_list_stays_when_the_plan_is_complete(tmp_path):
    # A filtered or short table can legitimately return fewer than N labels.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Shed", "Ripe"])
    ws.append(["Amber", 40])
    ws.append(["Birch", 25])
    path = tmp_path / "short.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    q = "Name the three sheds with the largest Ripe, descending, separated by a comma and one space."
    assert _answer(tables, q) == "Amber, Birch"
    planned, conf, reasons = plan_with_confidence(q, tables)
    assert planned and planned["op"] == "rank" and conf >= 0.5
    assert "blank:topn" not in reasons


def test_nth_without_a_rank_column_blanks(tmp_path):
    # Before: an arbitrary tag, 'Pax', confidence 1.0, by=None.
    tables = _dual(tmp_path / "dual.xlsx")
    q = "Which tag is the second highest?"
    assert _answer(tables, q) is None
    assert "blank:nth-column" in _reasons(tables, q)
    assert _answer(tables, "The 2nd-largest tag.") is None


def test_nth_with_one_measure_still_answers(tmp_path):
    tables = _grams(tmp_path / "grams.xlsx")
    assert _answer(tables, "Which tag ranks second when Grams decides the order?") == "Pax"
    assert _answer(tables, "Find the 2nd-largest Grams tag.") == "Pax"


def test_ordinal_inside_a_column_name_still_answers(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Region", "2nd quarter", "3rd quarter"])
    ws.append(["North", 11, 15])
    ws.append(["South", 7, 9])
    path = tmp_path / "quarters.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    q = "What were 2nd quarter arrivals in North? Answer with only the integer."
    assert _answer(tables, q) == "11"


def test_pair_single_number_blanks(tmp_path):
    # Before: one summed figure, '134'.
    tables = _orchard(tmp_path / "orch.xlsx")
    q = ("Pears and plums for the total older span, in that order, "
         "separated by a comma and one space.")
    assert _answer(tables, q) is None
    assert "blank:shape" in _reasons(tables, q)
    assert not answer_ok("number", "134", {"kind": "list", "count": 2, "sep": ", "}, q, {"op": "sum"})


def test_pair_of_cells_still_answers(tmp_path):
    tables = _orchard(tmp_path / "orch.xlsx")
    q = ("What are the pears and plums of span 4 to 6? "
         "Answer as two integers in the order `pears, plums`, separated by a comma and one space.")
    assert _answer(tables, q) == "40, 30"


def test_combined_sum_still_answers(tmp_path):
    tables = _orchard(tmp_path / "orch.xlsx")
    q = "What is the sum of pears and plums across all spans? Answer with only the integer."
    assert _answer(tables, q) == "134"
    tables = _ledger(tmp_path / "ledger.xlsx")
    q = "What is the sum of Cargo Ripe across all sheds? Answer with only the integer."
    assert _answer(tables, q) == "92"


def test_unscoped_lookup_blanks_on_a_multirow_table(tmp_path):
    # Before: the first shed's Cargo Ripe, '40'.
    tables = _ledger(tmp_path / "ledger.xlsx")
    assert _answer(tables, "Read the Cargo Ripe cell.") is None
    assert "blank:lookup-row" in _reasons(tables, "Read the Cargo Ripe cell.")
    q = "Cargo Ripe recorded against Birch. Answer with only the integer."
    assert _answer(tables, q) == "25"


def test_lookup_on_a_single_row_still_answers(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Shed", "Ripe"])
    ws.append(["Only", 7])
    path = tmp_path / "one.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    assert _answer(tables, "Read the Ripe cell.") == "7"


def test_header_stub_row_is_not_used_as_the_lookup(tmp_path):
    # The reader still keeps the stub. Before, an unscoped lookup returned '1'.
    tables = _stub_header(tmp_path / "stub.xlsx")
    assert _answer(tables, "Read the Ripe cell.") is None
    assert _answer(tables, "Ripe recorded against Amber. Answer with only the integer.") == "40"


def test_single_number_question_rejects_a_label_list():
    fmt = {"kind": "text", "sep": ", "}
    q = "Tong so bushels la bao nhieu?"
    assert not answer_ok("any", "Amber, Birch", fmt, q, {"op": "rank"})
    assert answer_ok("number", "96", {"kind": "int", "sep": ", "},
                     "What is the sum of bushels? Answer with only the integer.",
                     {"op": "sum"})


def test_oldest_list_keeps_the_planner_order(tmp_path):
    import datetime as dt
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Birth", "Points"])
    for row in [
        ("Dee", dt.datetime(1975, 1, 1), 11),
        ("Cy", dt.datetime(1984, 1, 1), 27),
        ("Ada", dt.datetime(1988, 1, 1), 6),
        ("Bo", dt.datetime(1992, 1, 1), 44),
    ]:
        ws.append(list(row))
    path = tmp_path / "people.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    q = "Who are the three oldest? Answer with names, oldest first, separated by a comma and one space."
    reasons = _reasons(tables, q)
    assert "blank:nth-column" not in reasons and "blank:topn" not in reasons
    assert _answer(tables, q) == "Dee, Cy, Ada"


def test_list_order_first_is_not_an_nth_rank(tmp_path):
    # 'highest first' and 'lowest first' order a top-N or bottom-N list.
    tables = _grams(tmp_path / "grams.xlsx")
    assert _answer(
        tables,
        "List the top 3 tags by Grams, highest first, separated by a comma and one space.",
    ) == "Nia, Pax, Quill"
    assert _answer(
        tables,
        "Name the bottom 2 tags by Grams, lowest first, separated by a comma and one space.",
    ) == "Ren, Quill"


def test_number_words_and_bottom_n_stay(tmp_path):
    tables = _grams(tmp_path / "grams.xlsx")
    assert _answer(tables, "Grams: ba lớn nhất.") == "Nia, Pax, Quill"
    assert _answer(tables, "Grams: năm nhỏ nhất.") == "Ren, Quill, Pax, Nia"


def test_ten_smallest_number_word(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Tag", "Grams"])
    for i, n in enumerate([88, 63, 52, 41, 30, 22, 18, 11, 7, 4, 2, 1]):
        ws.append([f"T{i}", n])
    path = tmp_path / "long.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    got = _answer(tables, "Grams: mười nhỏ nhất.")
    assert got == "T11, T10, T9, T8, T7, T6, T5, T4, T3, T2"
    assert "blank:topn" not in _reasons(tables, "Grams: mười nhỏ nhất.")
    assert _answer(tables, "bottom 5 Grams tags, separated by a comma and one space.") == "T11, T10, T9, T8, T7"


def test_which_name_with_a_total_column_stays_a_label(tmp_path):
    # Same shape as a which-group question that names the Total column.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Age", "Total", "Agriculture"])
    for row in [
        ("Women", None, None),
        ("16 to 19 years", 10, 1),
        ("35 to 44 years", 50, 30),
        ("55 years and over", 15, 8),
        ("Men", None, None),
        ("35 to 44 years", 40, 9),
    ]:
        ws.append(list(row))
    path = tmp_path / "ages.xlsx"
    wb.save(path)
    tables = load_tables(str(path))
    q = "Ở phụ nữ, nhóm tuổi nào có đông người làm nông nghiệp nhất (cột Total)? Chỉ trả lời tên nhóm tuổi."
    assert "blank:shape" not in _reasons(tables, q)
    assert _answer(tables, q) == "35 to 44 years"
