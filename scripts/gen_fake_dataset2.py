#!/usr/bin/env python3
"""Write a synthetic dataset-2 pack for the dry run.

Pure Python, openpyxl, fixed seed. Not contest data. The pack matches the
layout run.py reads:

  <out>/queries.json
  <out>/sample_submission.csv
  <out>/xlsx_files/dataset2_fake.xlsx

The workbook has three kinds of sheet, same shapes as round 1:
  People  — record table, two-row merged header, tens of thousands of rows
  Stats   — grouped rows plus a Total row
  2023, 2024, 2025 — same columns, one sheet per period

Round-1 workbook byte sizes are not recorded in this repo (only an 87 MB
RAM note for a full answer run), so People is generated large enough to
stand in for a big round-1 file. The script prints the xlsx size in bytes.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font

SEED = 20261016
XLSX_NAME = "dataset2_fake.xlsx"
PEOPLE_ROWS = 40000


def _people(wb, rng: random.Random):
    ws = wb.active
    ws.title = "People"
    # Two-row merged header. Several columns; only Score is numeric so a long
    # flat sheet is not mistaken for nested subtotals.
    ws["A1"] = "No"
    ws.merge_cells("A1:A2")
    ws["B1"] = "Name"
    ws.merge_cells("B1:C1")
    ws["B2"] = "First"
    ws["C2"] = "Last"
    ws["D1"] = "Born"
    ws.merge_cells("D1:D2")
    ws["E1"] = "Score"
    ws.merge_cells("E1:E2")
    ws["F1"] = "City"
    ws.merge_cells("F1:F2")
    ws["G1"] = "Shift"
    ws.merge_cells("G1:G2")
    for col in ("A", "B", "D", "E", "F", "G"):
        ws[f"{col}1"].alignment = Alignment(horizontal="center", vertical="center")
    ws["B1"].alignment = Alignment(horizontal="center")
    first = ["An", "Binh", "Chi", "Dung", "Ha", "Khoa", "Lan", "Minh", "Ngoc", "Phuc"]
    last = ["Le", "Tran", "Pham", "Vo", "Do", "Bui", "Ho", "Dang", "Ly", "Truong"]
    cities = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    shifts = ["day", "evening", "night"]
    # Unique birth dates so "oldest" is a single row. Ids are text codes so
    # Score stays the only numeric column, and a question number is not a row label.
    offsets = list(range(PEOPLE_ROWS))
    rng.shuffle(offsets)
    base = datetime(1970, 1, 1)
    for i in range(PEOPLE_ROWS):
        ws.append((
            f"P{i + 1:05d}",
            first[i % len(first)],
            last[(i * 3) % len(last)],
            base + timedelta(days=int(offsets[i])),
            rng.randint(40, 99),
            cities[i % len(cities)],
            shifts[i % len(shifts)],
        ))


def _stats(wb):
    ws = wb.create_sheet("Stats")
    ws["A1"] = "Synthetic employment counts"
    ws.merge_cells("A1:C1")
    ws["A1"].font = Font(bold=True)
    ws["A3"] = "Group"
    ws["B3"] = "Employed"
    ws["C3"] = "Unemployed"
    # Counts are fixed so a Total row is the sum of the four group rows.
    rows = [
        (4, "Total", 100, 17),
        (5, "Men", None, None),
        (6, "16 to 24", 10, 5),
        (7, "25 to 54", 20, 1),
        (8, "Women", None, None),
        (9, "16 to 24", 30, 2),
        (10, "25 to 54", 40, 9),
    ]
    for r, label, employed, unemployed in rows:
        ws.cell(r, 1, label)
        if employed is None:
            continue
        ws.cell(r, 2, employed)
        ws.cell(r, 3, unemployed)


def _periods(wb, rng: random.Random):
    regions = ["North", "South", "East"]
    for year in (2023, 2024, 2025):
        ws = wb.create_sheet(str(year))
        ws["A1"] = "Region"
        ws["B1"] = "Sales"
        for i, region in enumerate(regions):
            ws.cell(i + 2, 1, region)
            ws.cell(i + 2, 2, rng.randint(10, 80))


def _queries():
    fname = XLSX_NAME
    items = [
        (
            "d2q01",
            "What score did the oldest person receive? Answer with only the integer score.",
        ),
        (
            "d2q02",
            "How many people scored at least 60? Answer with only the integer.",
        ),
        (
            "d2q03",
            "How many women aged 25 to 54 were employed? Answer with only the integer.",
        ),
        (
            "d2q04",
            "Which age group had the most unemployed women? Answer with only the age group.",
        ),
        (
            "d2q05",
            "Which region had the highest Sales in 2024? Answer with only the region name.",
        ),
        (
            "d2q06",
            "Chỉ xét những người làm ca night ở thành phố C3, tổng điểm Score của nhóm này là bao nhiêu? Ghi một số nguyên.",
        ),
        (
            "d2q07",
            "Lấy 5 người có ngày sinh sớm nhất trong bảng People rồi cộng điểm Score của họ, kết quả bằng mấy? Ghi một số nguyên.",
        ),
        (
            "d2q08",
            "Doanh số Sales của vùng South năm 2025 cao hơn hay thấp hơn năm 2023 bao nhiêu? Ghi số nguyên có dấu, lấy 2025 trừ 2023.",
        ),
        (
            "d2q09",
            "Trong danh sách People, những ai thuộc City C5 mà Score chưa tới 50 thì tổng cộng là mấy người?",
        ),
        (
            "d2q10",
            "Đếm số người làm ca evening đạt đúng chín mươi chín điểm Score. Ghi một số nguyên.",
        ),
    ]
    return [{"id": i, "file_name": fname, "query": q} for i, q in items]


def write_pack(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    xlsx_dir = out / "xlsx_files"
    xlsx_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    wb = openpyxl.Workbook()
    _people(wb, rng)
    _stats(wb)
    _periods(wb, rng)
    path = xlsx_dir / XLSX_NAME
    wb.save(path)
    print(f"{XLSX_NAME} bytes={path.stat().st_size} rows_people={PEOPLE_ROWS}")
    queries = _queries()
    (out / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Id order differs from queries.json so a sample file is actually exercised.
    sample_ids = [
        "d2q05", "d2q01", "d2q03", "d2q02", "d2q04",
        "d2q10", "d2q06", "d2q08", "d2q09", "d2q07",
    ]
    with open(out / "sample_submission.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "answer"])
        for qid in sample_ids:
            w.writerow([qid, ""])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a synthetic dataset-2 pack.")
    ap.add_argument("--out", required=True, help="Directory to create")
    args = ap.parse_args(argv)
    write_pack(Path(args.out))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
