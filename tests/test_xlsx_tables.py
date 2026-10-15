import os, sys
import openpyxl
from openpyxl.styles import Alignment
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.xlsx_tables import load_tables, summarize, to_number, clean_text, strip_footnotes


def _save(wb, tmp_path, name="t.xlsx"):
    p = tmp_path / name
    wb.save(p)
    return str(p)


def test_to_number():
    assert to_number("1,234") == 1234
    assert to_number("12.5(1)") == 12.5
    assert to_number("-") is None and to_number("(NA)") is None
    assert to_number("(5)") == -5
    assert to_number(" 3 ") == 3


def test_clean_and_footnotes():
    assert clean_text("Con-\nstruction") == "Construction"
    assert clean_text("Self-\nemployed") == "Self-employed"
    assert strip_footnotes("Hospital services(1)(3)") == "Hospital services"
    assert strip_footnotes("Trade balance 3/") == "Trade balance"


def test_merged_headers_title_notes_sections(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "S"
    ws["A1"] = "TABLE 1. Something long title"; ws.merge_cells("A1:E1")
    ws["A3"] = "Group"; ws.merge_cells("A3:A4")
    ws["B3"] = 2023; ws.merge_cells("B3:E3")
    ws["B4"] = "Men"; ws["C4"] = "Women"; ws["D4"] = "Total(1)"; ws["E4"] = "Rate"
    ws["A5"] = "SECTION A"
    ws.append(["Alpha", "1,000", 2, 3, "-"])
    ws.append(["Beta(2)", 4, "(NA)", 6, 1.5])
    ws["A9"] = "SECTION B"
    ws.append(["Alpha", 7, 8, 9, 2.5])
    ws["A12"] = "Footnotes:"
    ws["A13"] = "(1) Includes things."; ws.merge_cells("A13:E13")
    ts = load_tables(_save(wb, tmp_path))
    assert len(ts) == 1
    t = ts[0]
    assert "TABLE 1" in t.title
    assert t.meta == ["2023"]
    cols = [c for c in t.df.columns if not c.startswith("_")]
    assert cols == ["Group", "Men", "Women", "Total", "Rate"]
    assert t.df["_row_path"].tolist() == ["SECTION A > Alpha", "SECTION A > Beta", "SECTION B > Alpha"]
    assert t.df["Men"].tolist() == [1000, 4, 7]
    assert t.df["Women"].isna().tolist() == [False, True, False]
    assert len(t.notes) == 2
    assert "Men" in summarize(ts)


def test_two_level_merged_header_and_indent(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws["A1"] = "Item"; ws.merge_cells("A1:A2")
    ws["B1"] = "Index"; ws.merge_cells("B1:C1")
    ws["B2"] = "Sep"; ws["C2"] = "Oct"
    rows = [("All", 0, 10, 11), ("Food", 1, 5, 6), ("Bread", 2, 2, 3), ("Energy", 1, 4, 4)]
    for i, (n, ind, a, b) in enumerate(rows, start=3):
        ws.cell(i, 1, n).alignment = Alignment(indent=ind)
        ws.cell(i, 2, a); ws.cell(i, 3, b)
    t = load_tables(_save(wb, tmp_path))[0]
    assert [c for c in t.df.columns if not c.startswith("_")] == ["Item", "Index | Sep", "Index | Oct"]
    assert t.df["_row_path"].tolist() == ["All", "All > Food", "All > Food > Bread", "All > Energy"]


def test_leading_space_indent(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Name", "Val"])
    for n, v in [("Total", 3), ("  A", 1), ("  B", 2)]:
        ws.append([n, v])
    t = load_tables(_save(wb, tmp_path))[0]
    assert t.df["_row_path"].tolist() == ["Total", "Total > A", "Total > B"]


def test_multiple_tables_one_sheet(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws["B1"] = "Fiscal"; ws.merge_cells("B1:C1"); ws["B2"] = 2023; ws["C2"] = 2024
    ws["A3"] = "Exports"; ws["B3"] = 1.5; ws["C3"] = 2.5
    ws["A4"] = "Imports"; ws["B4"] = 3; ws["C4"] = 4
    ws["B6"] = "Calendar"; ws.merge_cells("B6:C6"); ws["B7"] = 2023; ws["C7"] = 2024
    ws["A8"] = "Exports"; ws["B8"] = 9; ws["C8"] = 8
    ts = load_tables(_save(wb, tmp_path))
    assert len(ts) == 2
    assert "Fiscal | 2024" in ts[0].df.columns and "Calendar | 2023" in ts[1].df.columns
    assert ts[1].df["Calendar | 2023"].tolist() == [9]


def test_record_table_with_dates(tmp_path):
    import datetime as dt
    wb = openpyxl.Workbook(); ws = wb.active
    ws["A1"] = "No"; ws.merge_cells("A1:A2"); ws["B1"] = "Name"; ws.merge_cells("B1:C1")
    ws["B2"] = "First"; ws["C2"] = "Last"; ws["D1"] = "Born"; ws.merge_cells("D1:D2")
    ws.append([1, "An", "Le", dt.datetime(2000, 1, 1)])
    ws.append([2, "Binh", "Tran", dt.datetime(1999, 5, 2)])
    t = load_tables(_save(wb, tmp_path))[0]
    assert len(t.df) == 2
    assert "Name | Last" in t.df.columns and "Born" in t.df.columns
    assert t.df["Name | Last"].tolist() == ["Le", "Tran"]
