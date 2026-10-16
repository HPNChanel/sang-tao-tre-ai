"""KSP2: send-time schema compaction, robust parser, reasoning control, cost log. Fake LLM only."""
import hashlib, json, sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from src import llm_client as L  # noqa: E402
from src.plan_validate import validate, load_schema, SCHEMA_FILE  # noqa: E402
import fake_llm as F  # noqa: E402

SCHEMA_SHA256_LF = "bdb3b13c5179707826236e5f9d7c7fa1a2db4dff91555d091f279f6443b709eb"
VALID = [F.PLAN,
         {"version": 1, "op": "sum", "columns": ["Revenue | *"], "format": {"kind": "int"}},
         {"version": 1, "op": "argmax", "rows": {"paths": ["S1!A > B"]}, "args": {"by": "age"},
          "table": {"stack_sheets": True}, "format": {"kind": "name"}}]
INVALID = [{"version": 1, "op": "explode", "format": {"kind": "int"}},
           {"version": 1, "op": "sum", "format": {"kind": "int"}, "x": 1},
           {"version": 1, "op": "argmax", "args": {"ties": "some"}, "format": {"kind": "name"}}]


def test_repo_schema_unchanged_on_disk():
    raw = SCHEMA_FILE.read_bytes()
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == SCHEMA_SHA256_LF
    s = json.loads(raw)
    assert {"$schema", "$id", "title"} <= set(s) and b'"description"' in raw
    L.compact_schema(raw.decode())
    assert SCHEMA_FILE.read_bytes() == raw


def test_compacted_schema_same_constraints():
    full = load_schema()
    sent = L.compact_schema(SCHEMA_FILE.read_text(encoding="utf-8"))
    comp = json.loads(sent)
    assert ", " not in sent and ": " not in sent.replace(" | ", "") and "\n" not in sent
    for k in ("description", "$schema", "$id", "title"):
        assert f'"{k}"' not in sent
    assert comp == L._strip(full)
    for p in VALID:
        assert validate(p, full) == [] and validate(p, comp) == []
    for p in INVALID:
        assert validate(p, full) and validate(p, comp)


def test_system_prompt_keeps_five_rules():
    sp = (L.PROMPTS / "plan_system.txt").read_text(encoding="utf-8")
    for r in ("Parent | *", '["*"]', "Sheet!path", "ties", '"age"'):
        assert r in sp, r


def test_sent_body_uses_compact_schema_and_reasoning_off(monkeypatch):
    t = F.FakeTransport([F.plain()])
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(L, "_post", t)
    assert L.llm_plan("Q", "T", SCHEMA_FILE.read_text(), L.Budget(), validate=validate) == F.PLAN
    b = t.bodies[0]
    assert b["model"] == "qwen/qwen3.7-flash" and b["reasoning"] == {"enabled": False}
    assert b["max_tokens"] == 600 and "description" not in b["messages"][1]["content"]
    assert "test-not-a-real-key" not in json.dumps(b)


@pytest.mark.parametrize("r", [F.plain(), F.fenced(), F.fenced(lang=""), F.reasoning_preamble()])
def test_parser_shapes_ok(r):
    assert L.parse_plan(r["choices"][0]["message"]["content"]) == F.PLAN


@pytest.mark.parametrize("txt", [F.truncated()["choices"][0]["message"]["content"], "", None,
                                 "not json", "```json\n{\"version\": 1,\n```", "<think>never ends {"])
def test_parser_bad_returns_none(txt):
    assert L.parse_plan(txt) is None


def _run(monkeypatch, responses, cap=None):
    t = F.FakeTransport(responses)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(L, "_post", t)
    monkeypatch.setattr(L, "RETRY_SLEEP", 0)
    b = L.Budget(cap) if cap else L.Budget()
    plan = L.llm_plan("Q", "T", SCHEMA_FILE.read_text(), b, validate=validate, qid="qX")
    return plan, b, t


@pytest.mark.parametrize("bad", [F.http_error(402), F.http_error(429), TimeoutError("t"),
                                 F.resp("not json"), F.resp(json.dumps(INVALID[0])), F.truncated(),
                                 {"weird": 1}, ValueError("bad json body")])
def test_error_paths_no_plan_bounded(monkeypatch, bad):
    plan, b, t = _run(monkeypatch, [bad] * 5)
    assert plan is None
    assert len(t.bodies) == b.attempts == L.MAX_ATTEMPTS == len(b.log)
    assert b.spent > 0 and b.spent <= b.cap
    if b.token_usage_unknown:
        assert b.unresolved_usd > 0 and b.cost_usd is None
    else:
        assert b.tokens > 0


def test_reasoning_tokens_logged_separately(monkeypatch):
    plan, b, _ = _run(monkeypatch, [F.truncated(), F.reasoning_preamble()])
    assert plan == F.PLAN and b.attempts == 2
    assert [e["status"] for e in b.log] == ["truncated", "ok"]
    assert b.completion_tokens == 600 + 590 and b.reasoning_tokens == 570 + 520
    real = (400 * 2) * L.PRICE_IN + (600 + 590) * L.PRICE_OUT
    assert abs(b.spent - real) < 1e-12


def test_error_charged_worst_case(monkeypatch):
    plan, b, _ = _run(monkeypatch, [F.http_error(402), F.plain()])
    assert plan == F.PLAN and b.log[0]["worst_case"] and b.log[0]["status"] == "error:HTTPError"
    assert b.log[1]["worst_case"] is False


def test_cap_stops_before_overrun(monkeypatch):
    chars = sum(len(m["content"]) for m in L.build_messages("Q", "T", L.compact_schema(SCHEMA_FILE.read_text())))
    cap = L.Budget().worst_case(chars) * 1.5
    plan, b, t = _run(monkeypatch, [F.http_error(429)] * 3, cap=cap)
    assert plan is None and len(t.bodies) == 1 and b.spent <= cap


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(L, "_post", lambda *a: pytest.fail("network called"))
    assert L.llm_plan("Q", "T", "{}", L.Budget()) is None
