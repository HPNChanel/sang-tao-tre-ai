"""Mocked (no network) retry/failure cost accounting."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run  # noqa: E402
from src import llm_client, seams  # noqa: E402


def _setup(monkeypatch, responses, tmp_path):
    import openpyxl
    from src import plan_review
    from src.xlsx_tables import load_tables
    wb = openpyxl.Workbook()
    wb.active.append(["Item", "Amount"])
    wb.active.append(["Sample", 42])
    path = tmp_path / "retry_fixture.xlsx"
    wb.save(path)
    tables = load_tables(path)
    calls = []

    def fake_post(body, timeout):
        calls.append(body)
        r = responses[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(llm_client, "_post", fake_post)
    monkeypatch.setattr(llm_client, "RETRY_SLEEP", 0)
    monkeypatch.setattr(run, "load_tables", lambda p: tables)
    monkeypatch.setattr(run, "summarize", lambda t: "T")
    monkeypatch.setattr(seams, "code_plan", lambda *a: None)
    monkeypatch.setattr(plan_review, "plan_with_confidence", lambda *a, **kw: (None, 0, ["synthetic abstention"]))
    monkeypatch.setattr(seams, "have_executor", lambda: True)
    monkeypatch.setattr(seams, "execute", lambda plan, t, q: "42" if plan else None)
    return calls


def test_unknown_transport_cost_halts_before_retry(monkeypatch, tmp_path):
    ok = {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.000045},
          "choices": [{"message": {"content": '{"version": 1, "op": "sum", "format": {"kind": "int"}}'}}]}
    calls = _setup(monkeypatch, [TimeoutError("boom"),
                                 {"choices": [{"message": {"content": "not json"}}]}, ok], tmp_path)
    ans, b = run.answer_one({"id": "q1", "file_name": "f.xlsx", "query": "Q?"}, tmp_path, {})
    assert ans == "" and len(calls) == 1 and b.attempts == 1
    assert b.tokens == 0 and b.unresolved_usd == 0.03
    assert b.actual_usd == 0 and b.cost_usd is None
    assert llm_client.BILLING_HALT_REASON == 'billing_unresolved'
    assert b.token_usage_unknown  # no fabricated token usage for failed calls


def test_all_fail_blank_answer_still_logs_cost(monkeypatch, tmp_path):
    _setup(monkeypatch, [OSError("x")] * 3, tmp_path)
    d = tmp_path / "data dir"; (d / "xlsx_files").mkdir(parents=True)
    (d / "queries.json").write_text(json.dumps([{"id": "q1", "file_name": "f.xlsx", "query": "Q"}]))
    out = tmp_path / "o" / "submission.csv"
    run.main(["--data", str(d), "--out", str(out)])
    log = [json.loads(l) for l in open(out.parent / "run_log.jsonl")]
    assert log[0]["id"] == "q1" and log[0]["tokens"] == 0 and log[0]["cost_usd"] is None
    assert log[0]["unresolved_cost_usd"] == .03 and log[0]["budget_exposure_usd"] == .03
    assert log[0]["token_usage_unknown"] is True
    assert out.read_text().splitlines()[1] == "q1,"


def test_cap_blocks_before_exceeding(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, [OSError("x")] * 3, tmp_path)
    chars = sum(len(m["content"]) for m in llm_client.build_messages("Q", "T", "{}"))
    cap = llm_client.Budget().worst_case(chars) * 1.5  # room for exactly one attempt
    b = llm_client.Budget(cap)
    assert llm_client.llm_plan("Q", "T", "{}", b) is None
    assert len(calls) == 1 and b.spent <= cap


def test_schema_invalid_plan_blank_bounded(monkeypatch, tmp_path, capsys):
    bad = {"usage": {"prompt_tokens": 50, "completion_tokens": 10, "cost": .0001},
           "choices": [{"message": {"content": '{"version": 1, "op": "explode", "format": {"kind": "int"}}'}}]}
    calls = _setup(monkeypatch, [bad] * 10, tmp_path)
    executed = []
    monkeypatch.setattr(seams, "execute", lambda p, t, q: executed.append(p) or "42")
    ans, b = run.answer_one({"id": "q9", "file_name": "f.xlsx", "query": "Q?"}, tmp_path, {})
    assert ans == "" and executed == []
    assert len(calls) == llm_client.MAX_ATTEMPTS == b.attempts
    assert b.tokens == 3 * 60 and b.spent > 0
    err = capsys.readouterr().err
    assert "[schema] q9" in err and "not in enum" in err


def test_validator_accepts_valid_plan():
    from src.plan_validate import validate
    assert validate({"version": 1, "op": "argmax", "args": {"ties": "all"},
                     "format": {"kind": "name"}}) == []
    assert validate({"version": 1, "op": "sum", "format": {"kind": "int"}, "x": 1})
    assert validate({"version": 1, "op": "nth", "args": {"n": 0}, "format": {"kind": "int"}})
