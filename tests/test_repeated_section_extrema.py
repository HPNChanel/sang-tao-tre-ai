import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.code_planner import plan_with_confidence
from src.plan_executor import execute
from src.xlsx_tables import load_tables


def test_compare_repeated_measure_across_named_sections(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Activity", "Minutes"])
    for name, value in [("Red group", 9), ("Blue group", 13), ("Green group", 99)]:
        ws.append([name])
        ws.append(["Reading", value])
        ws.append(["Resting", 80])
    path = tmp_path / "activities.xlsx"
    wb.save(path)
    tables = load_tables(path)
    query = "Among Red group and Blue group, which group has the highest Minutes for Reading? Answer with only the group name."
    p, conf, reasons = plan_with_confidence(query, tables)
    assert conf >= .5, reasons
    assert p["args"]["over"] == "sections"
    assert execute(p, tables, query) == "Blue group"
