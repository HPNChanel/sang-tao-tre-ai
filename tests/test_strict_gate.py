"""No LLM key -> b8f1082 behavior. Strict evidence is an explicit flag, not the key."""
import os, sys
import openpyxl
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import conventions as C
from src.code_planner import plan, plan_with_confidence
from src.plan_executor import execute
from src.xlsx_tables import load_tables

AMBIG = "What were the 2023 sales of Hilltop? Answer with only the integer."  # two similar 2023 columns


@pytest.fixture
def t(tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Store", "Sales 2023 (estimate)", "Sales 2023 (final)", "Sales 2024"])
    ws.append(["Riverside", 100, 120, 90]); ws.append(["Hilltop", 300, 280, 310])
    ws.append(["Lakeview", 50, 60, 70])
    p = tmp_path / "s.xlsx"; wb.save(p); return load_tables(str(p))


def test_no_key_is_legacy_best_guess(t, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(C, "STRICT_CONFIDENCE", None)
    assert not C.strict_active()
    p = plan(AMBIG, t)
    assert p is not None and execute(p, t, AMBIG) == "300"   # b8f1082 answered (first matching column)


def test_key_does_not_change_rule_answer(t, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(C, "STRICT_CONFIDENCE", None)
    assert not C.strict_active()
    off = plan(AMBIG, t)
    assert execute(off, t, AMBIG) == "300"
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert not C.strict_active()
    assert plan(AMBIG, t) == off


def test_explicit_flag_overrides_env(t, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert plan(AMBIG, t, strict=True) is None
    monkeypatch.setattr(C, "STRICT_CONFIDENCE", True)
    assert plan(AMBIG, t) is None
    q = "What were the final 2023 sales of Hilltop? Answer with only the integer."
    assert execute(plan(q, t, strict=True), t, q) == "280"   # clear question still answered in strict mode


def test_possessive_flag_restored(t):
    from src import textmatch
    plan_with_confidence(AMBIG, t, strict=True)
    assert textmatch.STRICT_POSSESSIVE is False
