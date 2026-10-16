import csv
import json
import time

import pytest

import run
from src import llm_client as L


def test_default_deadlines_reserve_csv_time():
    assert run.DEFAULT_TIMEOUT_S == 90
    assert run.PROCESSING_LIMIT_S + run.FINALIZE_RESERVE_S == 2400


def test_global_remaining_time_caps_next_question(tmp_path, monkeypatch):
    queries = [{"id": str(i), "query": "Q", "file_name": "unused.xlsx"} for i in range(3)]
    query_path = tmp_path / "queries.json"; query_path.write_text(json.dumps(queries))
    limits = []
    clock = [100.0]
    monkeypatch.setattr(run.time,'monotonic',lambda:clock[0])
    def answer(*args, **kwargs):
        limits.append(kwargs["timeout"])
        clock[0] += .06
        return "ok", L.Budget()
    monkeypatch.setattr(run, "PROCESSING_LIMIT_S", .1)
    monkeypatch.setattr(run, "answer_one", answer)
    out = tmp_path / "submission.csv"
    run.main(["--xlsx-dir", str(tmp_path), "--queries", str(query_path), "--output", str(out)])
    assert 0 < limits[-1] < limits[0] <= .1
    assert run._RUN.deadline_skip == ["2"]
    with out.open(newline="", encoding="utf-8") as f:
        assert list(csv.reader(f)) == [["id", "answer"], ["0", "ok"], ["1", "ok"], ["2", ""]]


def test_parse_and_llm_share_one_clock(tmp_path, monkeypatch):
    path = tmp_path / "book.xlsx"; path.touch()
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic")
    monkeypatch.setattr(run, "_load_into_parent", lambda *a: time.sleep(.03) or [])
    monkeypatch.setattr(run, "_answer_rule", lambda *a: None)
    observed = []
    def llm(q, tables, fname, budget, limit, use_fork, started):
        observed.append((budget.deadline - time.monotonic(), time.monotonic()-started))
        return None
    monkeypatch.setattr(run, "_answer_llm", llm)
    run.answer_one({"id":"q", "query":"Q", "file_name":"book.xlsx"}, tmp_path, {}, timeout=.2, use_fork=False)
    assert .01 < observed[0][0] < .18 and observed[0][1] >= .03
