"""Five Vietnamese questions on the synthetic dataset-2 pack.

The pack is generated with the fixed seed in scripts/gen_fake_dataset2.py.
Each expected answer is computed from those rows with pandas. The same
aggregations are computed again from the workbook the generator writes.
The signed Sales difference is also compared with plan/execute. The other
four questions are checked by recomputing the generated rows.
"""
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from gen_fake_dataset2 import PEOPLE_ROWS, SEED, XLSX_NAME, write_pack  # noqa: E402
from src.code_planner import plan  # noqa: E402
from src.plan_executor import execute  # noqa: E402
from src.xlsx_tables import load_tables  # noqa: E402

_NEW = ("d2q06", "d2q07", "d2q08", "d2q09", "d2q10")
_RULE = ("d2q08",)


def _rebuild(seed, n):
    """Same draws as the generator: shuffled birth offsets, then scores, then Sales."""
    rng = random.Random(seed)
    offsets = list(range(n))
    rng.shuffle(offsets)
    base = datetime(1970, 1, 1)
    first = ["An", "Binh", "Chi", "Dung", "Ha", "Khoa", "Lan", "Minh", "Ngoc", "Phuc"]
    last = ["Le", "Tran", "Pham", "Vo", "Do", "Bui", "Ho", "Dang", "Ly", "Truong"]
    cities = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    shifts = ["day", "evening", "night"]
    rows = []
    for i in range(n):
        rows.append({
            "born": base + timedelta(days=int(offsets[i])),
            "score": rng.randint(40, 99),
            "city": cities[i % len(cities)],
            "shift": shifts[i % len(shifts)],
        })
    sales = {}
    for year in (2023, 2024, 2025):
        for region in ("North", "South", "East"):
            sales[(year, region)] = rng.randint(10, 80)
    return pd.DataFrame(rows), sales


def _from_workbook(path):
    raw = pd.read_excel(path, sheet_name="People", header=None)
    body = raw.iloc[2:].copy()
    body.columns = ["no", "first", "last", "born", "score", "city", "shift"]
    people = pd.DataFrame({
        "born": pd.to_datetime(body["born"]),
        "score": pd.to_numeric(body["score"]),
        "city": body["city"].astype(str),
        "shift": body["shift"].astype(str),
    })
    sales = {}
    for year in (2023, 2024, 2025):
        sheet = pd.read_excel(path, sheet_name=str(year))
        for rec in sheet.to_dict("records"):
            sales[(int(year), rec["Region"])] = int(rec["Sales"])
    return people, sales


def _answers(people, sales):
    night_c3 = (people["city"] == "C3") & (people["shift"] == "night")
    low_c5 = (people["city"] == "C5") & (people["score"] <= 49)
    # 'chín mươi chín' is 99
    evening_top = (people["shift"] == "evening") & (people["score"] >= 99)
    oldest = people.nsmallest(5, "born")
    return {
        "d2q06": int(people.loc[night_c3, "score"].sum()),
        "d2q07": int(oldest["score"].sum()),
        "d2q08": int(sales[(2025, "South")] - sales[(2023, "South")]),
        "d2q09": int(low_c5.sum()),
        "d2q10": int(evening_top.sum()),
    }


def test_vi_questions_recomputed_from_seed_and_workbook(tmp_path):
    pack = tmp_path / "pack"
    write_pack(pack)
    queries = json.loads((pack / "queries.json").read_text(encoding="utf-8"))
    by_id = {q["id"]: q["query"] for q in queries}
    assert [q["id"] for q in queries] == [f"d2q{i:02d}" for i in range(1, 11)]
    assert by_id["d2q06"] == (
        "Chỉ xét những người làm ca night ở thành phố C3, tổng điểm Score của nhóm này là bao nhiêu? Ghi một số nguyên."
    )
    assert by_id["d2q07"] == (
        "Lấy 5 người có ngày sinh sớm nhất trong bảng People rồi cộng điểm Score của họ, kết quả bằng mấy? Ghi một số nguyên."
    )
    assert by_id["d2q08"] == (
        "Doanh số Sales của vùng South năm 2025 cao hơn hay thấp hơn năm 2023 bao nhiêu? Ghi số nguyên có dấu, lấy 2025 trừ 2023."
    )
    assert by_id["d2q09"] == (
        "Trong danh sách People, những ai thuộc City C5 mà Score chưa tới 50 thì tổng cộng là mấy người?"
    )
    assert by_id["d2q10"] == (
        "Đếm số người làm ca evening đạt đúng chín mươi chín điểm Score. Ghi một số nguyên."
    )

    built = _answers(*_rebuild(SEED, PEOPLE_ROWS))
    written = _answers(*_from_workbook(pack / "xlsx_files" / XLSX_NAME))
    assert written == built
    assert set(built) == set(_NEW)

    tables = load_tables(str(pack / "xlsx_files" / XLSX_NAME))
    for qid in _RULE:
        planned = plan(by_id[qid], tables, XLSX_NAME)
        assert planned, qid
        assert execute(planned, tables, by_id[qid]) == str(built[qid])
