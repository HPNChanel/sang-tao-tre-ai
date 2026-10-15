"""Paraphrased and Vietnamese operation wording.

Synthetic workbooks only. A second-highest, a difference, a top-N (digits or
number words such as ba/năm/mười), and a pair must not fall through to lookup,
sum, or count. When the operation is still not clear, the planner abstains.
A number that only names a row or column is not a filter and not k.
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


def _ask(path, q):
    tables = load_tables(path)
    planned = plan(q, tables, os.path.basename(path))
    assert planned, q
    return execute(planned, tables, q)


def _scores(tmp_path):
    return _save(tmp_path, "scores.xlsx", ["Name", "Score"], [
        ("An", 90), ("Bo", 70), ("Cy", 50), ("Di", 30), ("Ed", 20),
        ("Fa", 15), ("Gia", 12), ("Ha", 9), ("In", 6), ("Jo", 4), ("Ko", 1),
    ])


def test_second_highest_english_paraphrases(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "What is the second highest score? Answer with only the integer.") == "70"
    assert _ask(path, "What is the 2nd-highest score? Answer with only the integer.") == "70"
    assert _ask(path, "Which person ranks second by score? Answer with only the name.") == "Bo"


def test_second_highest_vietnamese(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "Điểm cao thứ hai là bao nhiêu? Chỉ trả lời bằng số nguyên.") == "70"
    assert _ask(path, "Diem cao thu hai la bao nhieu? Chi tra loi bang so nguyen.") == "70"
    assert _ask(path, "Người xếp thứ hai theo điểm là ai? Chỉ trả lời tên.") == "Bo"


def test_difference_english_paraphrases(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "By how much does Bo exceed Cy? Answer with only the integer.") == "20"
    assert _ask(path, "How much greater is Bo's score than Cy's? Answer with only the integer.") == "20"
    assert _ask(path, "Bo minus Cy on Score. Answer with only the integer.") == "20"


def test_difference_vietnamese(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "Bo hơn Cy bao nhiêu điểm? Chỉ trả lời bằng số nguyên.") == "20"
    assert _ask(path, "Bo hon Cy bao nhieu diem? Chi tra loi bang so nguyen.") == "20"
    assert _ask(path, "Chênh lệch điểm giữa Bo và Cy là bao nhiêu? Chỉ trả lời bằng số nguyên.") == "20"
    assert _ask(path, "Chenh lech diem giua Bo va Cy la bao nhieu? Chi tra loi bang so nguyen.") == "20"


def test_topn_number_words_and_reversed_order(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "What are the three highest scores?") == "90, 70, 50"
    assert _ask(path, "the five lowest scores") == "1, 4, 6, 9, 12"
    assert _ask(path, "the highest three scores") == "90, 70, 50"
    assert _ask(path, "Năm điểm thấp nhất.") == "1, 4, 6, 9, 12"
    assert _ask(path, "top ba điểm") == "90, 70, 50"
    assert _ask(path, "mười điểm cao nhất") == "90, 70, 50, 30, 20, 15, 12, 9, 6, 4"


def test_pair_of_values(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "What are the scores of Bo and Cy? separated by a comma and one space.") == "70, 50"
    assert _ask(path, "Điểm của Bo và Cy lần lượt là bao nhiêu?") == "70, 50"
    assert _ask(path, "Diem cua Bo va Cy lan luot.") == "70, 50"


def test_compare_yes_no_vietnamese(tmp_path):
    path = _scores(tmp_path)
    q = "Điểm của Bo có cao hơn Cy không? Chỉ trả lời Yes hoặc No."
    assert _ask(path, q) == "Yes"
    q = "Bo co diem cao hon Cy khong? Answer with only Yes or No."
    assert _ask(path, q) == "Yes"


def test_plain_lookup_stays(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "What is Bo's score? Answer with only the integer.") == "70"


def test_abstain_when_operation_is_not_determined(tmp_path):
    path = _scores(tmp_path)
    tables = load_tables(path)
    # one side of a difference: used to return Bo's score
    q = "By how much does the score exceed? Answer with only the integer."
    assert plan(q, tables) is None
    _, conf, reasons = plan_with_confidence(q, tables)
    assert conf < 0.5 and reasons
    q = "Bo minus. Answer with only the integer."
    assert plan(q, tables) is None
    # top-N plus a single integer instruction disagrees; do not emit Bo's score
    q = "What is Bo's score for the top five? Answer with only the integer."
    assert plan(q, tables) is None


def test_label_number_is_not_a_filter_or_k(tmp_path):
    path = _save(tmp_path, "bands.xlsx", ["Band", "Employed"], [
        ("16 to 24 years", 10),
        ("25 to 54 years", 40),
        ("55 years and over", 15),
        ("Group 3", 7),
    ])
    tables = load_tables(path)
    q = "How many employed are 55 or older? Answer with only the integer."
    assert _ask(path, q) == "15"
    planned = plan(q, tables)
    assert planned["rows"]["where"] == []

    q = "Which band of 55 years and over has the highest employed? Answer with only the name."
    planned = plan(q, tables)
    assert planned["args"].get("k") != 55
    assert all(w.get("value") != 55 and w.get("value") != 55.0 for w in planned["rows"]["where"])
    assert execute(planned, tables, q) == "25 to 54 years"

    q = "Which 3 groups have the most employed? separated by a comma and one space."
    assert _ask(path, q) == "25 to 54 years, 55 years and over, 16 to 24 years"


def test_explicit_threshold_still_counts(tmp_path):
    path = _scores(tmp_path)
    assert _ask(path, "How many people scored at least 60? Answer with only the integer.") == "2"


def test_fixture_operation_shapes_unchanged(tmp_path):
    """Plan shape for the public Vietnamese and top-N questions, on a stand-in table."""
    path = _save(tmp_path, "ind.xlsx", ["Industry", "Employed"], [("A", 100), ("B", 80), ("C", 10)])
    tables = load_tables(path)
    cases = [
        ("Hai ngành nào có nhiều người từ 55 tuổi trở lên có việc làm nhất? "
         "Chỉ trả lời tên ngành, theo thứ tự giảm dần, phân cách bằng dấu phẩy và một dấu cách.",
         "rank", 2),
        ("Which three industries employed the most people aged 16 to 19 years? "
         "Answer with the industry names, largest first, separated by a comma and one space.",
         "rank", 3),
        ("Trong năm người lớn tuổi nhất, điểm cao nhất là bao nhiêu? Chỉ trả lời bằng số nguyên.",
         "topk_agg", 5),
        ("Tổng số người đi làm của nam giới từ 20 đến 24 tuổi là bao nhiêu? Chỉ trả lời bằng số nguyên.",
         "sum", None),
        ("Ở phụ nữ, nhóm tuổi nào có đông người làm nông nghiệp nhất (cột Total)? Chỉ trả lời tên nhóm tuổi.",
         "argmax", None),
    ]
    for q, op, k in cases:
        planned = plan(q, tables)
        assert planned["op"] == op, q
        assert planned["args"].get("k") == k
