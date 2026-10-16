import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.xlsx_tables import load_tables


def test_suppressed_body_row_does_not_replace_headers(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Band", "Median", "Upper", "Lower"])
    ws.append(["Alpha"])
    ws.append(["Small", 4.0, 8.0, 2.0])
    ws.append(["Beta"])
    ws.append(["Small", "-", "(-12-)", "..."])
    ws.append(["Large", 6.0, 11.0, 3.0])
    path = tmp_path / "suppressed.xlsx"
    wb.save(path)
    tables = load_tables(path)
    assert len(tables) == 1
    df = tables[0].df
    assert df["_row_path"].tolist() == ["Alpha > Small", "Beta > Small", "Beta > Large"]
    assert df.loc[1, ["Median", "Upper", "Lower"]].isna().all()
    assert df.loc[2, "Upper"] == 11
    assert pd.api.types.is_numeric_dtype(df["Upper"])


def test_parenthesized_negative_numbers_are_not_suppression(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Band", "Balance"])
    ws.append(["A", "(12)"])
    ws.append(["B", 3])
    path = tmp_path / "negative.xlsx"
    wb.save(path)
    assert load_tables(path)[0].df["Balance"].tolist() == [-12, 3]
