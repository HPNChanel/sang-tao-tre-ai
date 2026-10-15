import os, sys
import openpyxl
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.code_planner import plan
from src.plan_executor import execute
from src.xlsx_tables import load_tables
from src import conventions as C


def _wb(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Station", "Jan", "Feb"])
    for r in [("Alpha", 5, 9), ("Beta", 9, 1), ("Gamma", 9, 1), ("Delta", 2, 4)]:
        ws.append(list(r))
    p = tmp_path / "t.xlsx"; wb.save(p); return str(p)


def _ask(p, q):
    t = load_tables(p); pl = plan(q, t, "t.xlsx"); return execute(pl, t, q), pl


def test_plural_returns_all_in_table_order(tmp_path):
    p = _wb(tmp_path)
    a, pl = _ask(p, "Which stations recorded the highest rainfall in Jan? Answer with only the station names.")
    assert pl["args"]["ties"] == "all" and a == "Beta, Gamma"
    a, _ = _ask(p, "List all stations with the lowest rainfall in Feb. Answer with the names separated by a comma and one space.")
    assert a == "Beta, Gamma"


def test_singular_keeps_first(tmp_path, capsys):
    p = _wb(tmp_path)
    a, pl = _ask(p, "Which station recorded the highest rainfall in Jan? Answer with only the station name.")
    assert pl["args"]["ties"] == "first" and a == "Beta"
    assert "[tie]" in capsys.readouterr().err


def test_no_tie_unchanged(tmp_path):
    p = _wb(tmp_path)
    a, _ = _ask(p, "Which stations recorded the lowest rainfall in Jan? Answer with only the station names.")
    assert a == "Delta"


def test_switch_off(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "TIES_RETURN_ALL", False)
    p = _wb(tmp_path)
    a, _ = _ask(p, "Which stations recorded the highest rainfall in Jan? Answer with only the station names.")
    assert a == "Beta"


def test_tie_over_columns(tmp_path):
    p = _wb(tmp_path)
    a, _ = _ask(p, "In which months did Alpha have its highest value? Answer with the month names.")
    assert a == "Feb"
