import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.xlsx_tables import load_tables


def test_continuation_codes_retain_aligned_descriptive_headers(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None, None, "North", "South"])
    ws.append([None, None, "Output", "Output"])
    ws.append(["Levels"])
    ws.append([None, 2018, 5, 7])
    ws.append(["Indices"])
    ws.append([None, None, "AX12", "BY34"])
    for label, a, b in [(2017, 90, 95), (2018, 100, 100), ("2018 Q1", 130, 150)]:
        ws.append([None, label, a, b])
    path = tmp_path / "series.xlsx"
    wb.save(path)
    tables = load_tables(path)
    assert len(tables) == 2
    index = tables[1]
    assert "North | Output | AX12" in index.df
    assert "South | Output | BY34" in index.df
    assert index.df[index.label_col].tolist() == ["2017", "2018", "2018 Q1"]
    assert "Indices" in index.title
    assert "Levels" not in tables[0].df[tables[0].label_col].tolist()


def test_unrelated_text_header_does_not_inherit_previous_schema(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Stock", "Stock"])
    ws.append([None, "East", "West"])
    ws.append(["A", 4, 5])
    ws.append(["New data"])
    ws.append(["Item", "Revenue", "Cost"])
    ws.append(["B", 6, 2])
    path = tmp_path / "unrelated.xlsx"
    wb.save(path)
    tables = load_tables(path)
    assert "Revenue" in tables[1].df
    assert all("Stock" not in c for c in tables[1].df.columns)
