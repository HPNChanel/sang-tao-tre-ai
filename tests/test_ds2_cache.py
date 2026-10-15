"""Cache, per-query timeout, and durable CSV on a generated workbook."""
import csv
import datetime as dt
import json
import os
import signal
import time
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))
import run  # noqa: E402

COUNT_Q = "How many people scored at least 60? Answer with only the integer."


def _workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Birth", "Admission", "Score"])
    for name, birth, admission, score in (
        ("An", 2000, 2020, 50),
        ("Binh", 1990, 2024, 70),
        ("Chi", 1995, 2015, 90),
    ):
        ws.append([name, dt.datetime(birth, 1, 1), dt.datetime(admission, 1, 1), score])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _pack(tmp_path, queries, order=None):
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "book.xlsx")
    (data / "queries.json").write_text(json.dumps(queries), encoding="utf-8")
    if order is not None:
        with open(data / "sample_submission.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["id", "answer"])
            for qid in order:
                w.writerow([qid, ""])
    return data


def _rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def _q(qid, query=COUNT_Q, file_name="book.xlsx"):
    return {"id": qid, "file_name": file_name, "query": query}


def _count_loads(monkeypatch, log_path):
    """Record load_tables names in a file. A forked loader cannot append to a parent list."""
    real = run.load_tables

    def wrapped(path):
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(Path(path).name + "\n")
        return real(path)

    monkeypatch.setattr(run, "load_tables", wrapped)


def _load_names(log_path):
    if not log_path.exists():
        return []
    return [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _hwm_kb():
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    return 0


def test_workbook_loaded_once(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = _pack(tmp_path, [_q("a"), _q("b"), _q("a2")])
    log = tmp_path / "loads.txt"
    _count_loads(monkeypatch, log)
    out = tmp_path / "submission.csv"
    assert run.main(["--data", str(data), "--out", str(out), "--query-timeout", "30"]) == 0
    assert _load_names(log) == ["book.xlsx"]
    assert [r[1] for r in _rows(out)[1:]] == ["2", "2", "2"]


def test_mtime_reloads_and_tiny_budget_evicts(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "a.xlsx")
    _workbook(data / "xlsx_files" / "b.xlsx")
    log = tmp_path / "loads.txt"
    _count_loads(monkeypatch, log)
    cache = run.TableCache(byte_budget=1, rss_budget=10**18)
    timeout = 30
    run.answer_one(_q("1", file_name="a.xlsx"), data, cache, timeout=timeout)
    run.answer_one(_q("2", file_name="a.xlsx"), data, cache, timeout=timeout)
    assert _load_names(log) == ["a.xlsx"]
    book = data / "xlsx_files" / "a.xlsx"
    os.utime(book, (time.time() + 10, time.time() + 10))
    run.answer_one(_q("3", file_name="a.xlsx"), data, cache, timeout=timeout)
    assert _load_names(log) == ["a.xlsx", "a.xlsx"]
    run.answer_one(_q("4", file_name="b.xlsx"), data, cache, timeout=timeout)
    run.answer_one(_q("5", file_name="a.xlsx"), data, cache, timeout=timeout)
    assert _load_names(log) == ["a.xlsx", "a.xlsx", "b.xlsx", "a.xlsx"]


def test_timeout_blanks_that_query_and_keeps_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = _pack(
        tmp_path,
        [_q("slow", "SLEEP please"), _q("fast")],
        order=["fast", "slow"],
    )
    log = tmp_path / "loads.txt"
    _count_loads(monkeypatch, log)
    real_rule = run._rule_answer

    def rule(query, tables, fname, strict=None):
        if "SLEEP" in query:
            time.sleep(30)
        return real_rule(query, tables, fname, strict)

    monkeypatch.setattr(run, "_rule_answer", rule)
    out = tmp_path / "out" / "submission.csv"
    t0 = time.perf_counter()
    assert run.main(["--data", str(data), "--out", str(out), "--query-timeout", "0.4"]) == 0
    elapsed = time.perf_counter() - t0
    rows = _rows(out)
    assert [r[0] for r in rows[1:]] == ["fast", "slow"]
    assert rows[1][1] == "2"
    assert rows[2][1] == ""
    assert _load_names(log) == ["book.xlsx"]
    assert elapsed < 8


def test_cached_csv_matches_uncached(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = _pack(
        tmp_path,
        [_q("a"), _q("b"), _q("c")],
        order=["c", "a", "b"],
    )
    cached = tmp_path / "cached.csv"
    plain = tmp_path / "plain.csv"
    assert run.main(["--data", str(data), "--out", str(cached), "--query-timeout", "30"]) == 0
    assert run.main(["--data", str(data), "--out", str(plain), "--no-cache", "--query-timeout", "30"]) == 0
    assert cached.read_bytes() == plain.read_bytes()
    assert [r[0] for r in _rows(cached)[1:]] == ["c", "a", "b"]
    assert [r[1] for r in _rows(cached)[1:]] == ["2", "2", "2"]


def test_interrupt_leaves_every_id(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = _pack(tmp_path, [_q("a"), _q("b"), _q("c")], order=["a", "b", "c"])
    calls = {"n": 0}
    real = run.answer_one

    def wrapped(q, data_dir, cache, timeout=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt()
        return real(q, data_dir, cache, timeout=timeout, **kwargs)

    monkeypatch.setattr(run, "answer_one", wrapped)
    out = tmp_path / "submission.csv"
    try:
        run.main(["--data", str(data), "--out", str(out), "--query-timeout", "30"])
        raised = False
    except KeyboardInterrupt:
        raised = True
    assert raised
    rows = _rows(out)
    assert [r[0] for r in rows[1:]] == ["a", "b", "c"]
    assert rows[1][1] == "2"
    assert rows[2][1] == "" and rows[3][1] == ""
    assert not list(out.parent.glob("*.partial"))


def test_sigterm_handler_flushes_every_id(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = tmp_path / "submission.csv"
    run._RUN.reset(out, ["id", "answer"], ["a", "b", "c"], 1)
    run._RUN.answers["a"] = "2"
    try:
        run._on_sigterm(signal.SIGTERM, None)
        raised = False
    except SystemExit as exc:
        raised = exc.code == 128 + signal.SIGTERM
    assert raised
    assert _rows(out) == [["id", "answer"], ["a", "2"], ["b", ""], ["c", ""]]

    data = _pack(tmp_path / "installed", [_q("a")])
    seen = []
    real_signal = signal.signal

    def wrap(sig, handler):
        seen.append((sig, handler))
        return real_signal(sig, handler)

    monkeypatch.setattr(signal, "signal", wrap)
    assert run.main(["--data", str(data), "--out", str(tmp_path / "ok.csv"), "--query-timeout", "30"]) == 0
    assert any(sig == signal.SIGTERM and handler is run._on_sigterm for sig, handler in seen)


def test_slow_parse_is_outside_the_answer_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = _pack(tmp_path, [_q("a")])
    real = run.load_tables

    def wrapped(path):
        time.sleep(0.6)
        return real(path)

    monkeypatch.setattr(run, "load_tables", wrapped)
    out = tmp_path / "submission.csv"
    t0 = time.perf_counter()
    assert run.main(["--data", str(data), "--out", str(out), "--query-timeout", "0.25"]) == 0
    elapsed = time.perf_counter() - t0
    assert _rows(out)[1][1] == "2"
    assert elapsed >= 0.5
    assert "a" not in run._RUN.timed_out


def test_preload_timeout_skips_cache_and_bounds_the_query(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "book.xlsx")
    _workbook(data / "xlsx_files" / "slow.xlsx")
    (data / "queries.json").write_text(json.dumps([
        _q("fast", file_name="book.xlsx"),
        _q("slow", file_name="slow.xlsx"),
    ]), encoding="utf-8")
    log = tmp_path / "loads.txt"
    real = run.load_tables

    def wrapped(path):
        name = Path(path).name
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(name + "\n")
        if name == "slow.xlsx":
            time.sleep(30)
        return real(path)

    monkeypatch.setattr(run, "load_tables", wrapped)
    out = tmp_path / "submission.csv"
    t0 = time.perf_counter()
    assert run.main([
        "--data", str(data), "--out", str(out),
        "--preload-timeout", "0.4", "--query-timeout", "0.5",
    ]) == 0
    elapsed = time.perf_counter() - t0
    rows = {row[0]: row[1] for row in _rows(out)[1:]}
    assert rows["fast"] == "2"
    assert rows["slow"] == ""
    assert elapsed < 8
    assert run._RUN.timed_out == ["slow"]
    names = _load_names(log)
    assert names.count("book.xlsx") == 1
    assert names.count("slow.xlsx") >= 1


def test_hanging_query_is_killed_and_later_answer_is_correct(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = _pack(
        tmp_path,
        [_q("slow", "SLEEP please"), _q("later")],
        order=["slow", "later"],
    )
    real_rule = run._rule_answer

    def rule(query, tables, fname, strict=None):
        if "SLEEP" in query:
            time.sleep(30)
        return real_rule(query, tables, fname, strict)

    monkeypatch.setattr(run, "_rule_answer", rule)
    before = _hwm_kb()
    out = tmp_path / "submission.csv"
    t0 = time.perf_counter()
    assert run.main(["--data", str(data), "--out", str(out), "--query-timeout", "0.4"]) == 0
    elapsed = time.perf_counter() - t0
    after = _hwm_kb()
    rows = _rows(out)
    assert [row[0] for row in rows[1:]] == ["slow", "later"]
    assert rows[1][1] == ""
    assert rows[2][1] == "2"
    assert elapsed < 8
    assert run._RUN.timed_out == ["slow"]
    assert after < before + 200 * 1024
    assert after < 2 * 1024 * 1024


def test_spawn_and_in_process_do_not_fork(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DS2_IN_PROCESS", raising=False)
    data = _pack(tmp_path, [_q("a"), _q("b")])

    def boom(*_args, **_kwargs):
        raise AssertionError("fork path used")

    monkeypatch.setattr(run, "_run_forked", boom)
    monkeypatch.setenv("DS2_START_METHOD", "spawn")
    spawn_out = tmp_path / "spawn.csv"
    assert run.main(["--data", str(data), "--out", str(spawn_out), "--query-timeout", "30"]) == 0
    assert [row[1] for row in _rows(spawn_out)[1:]] == ["2", "2"]

    monkeypatch.delenv("DS2_START_METHOD", raising=False)
    in_out = tmp_path / "inproc.csv"
    assert run.main([
        "--data", str(data), "--out", str(in_out), "--in-process", "--query-timeout", "30",
    ]) == 0
    assert [row[1] for row in _rows(in_out)[1:]] == ["2", "2"]
    assert run._RUN.timed_out == []


def test_in_process_deadline_skips_remaining_queries(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = _pack(tmp_path, [_q("q1"), _q("q2")], order=["q1", "q2"])
    real_rule = run._rule_answer

    def rule(query, tables, fname, strict=None):
        time.sleep(0.35)
        return real_rule(query, tables, fname, strict)

    monkeypatch.setattr(run, "_rule_answer", rule)
    out = tmp_path / "submission.csv"
    assert run.main([
        "--data", str(data), "--out", str(out),
        "--in-process", "--query-timeout", "0.15",
    ]) == 0
    rows = _rows(out)
    assert [row[0] for row in rows[1:]] == ["q1", "q2"]
    assert rows[1][1] == "2"
    assert rows[2][1] == ""
    assert run._RUN.timed_out == []
    assert run._RUN.deadline_skip == ["q2"]


def test_want_fork_switches(monkeypatch):
    monkeypatch.delenv("DS2_IN_PROCESS", raising=False)
    monkeypatch.delenv("DS2_START_METHOD", raising=False)
    if run._fork_context() is None:
        assert run._want_fork(False) is False
        return
    assert run._want_fork(False) is True
    assert run._want_fork(True) is False
    monkeypatch.setenv("DS2_START_METHOD", "spawn")
    assert run._want_fork(False) is False
    monkeypatch.setenv("DS2_START_METHOD", "fork")
    assert run._want_fork(False) is True
    monkeypatch.setenv("DS2_IN_PROCESS", "1")
    assert run._want_fork(False) is False
