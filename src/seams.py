"""Interface seam between run.py and KSP1's planner/executor.

KSP1 fills in either module below; run.py auto-detects them:
  src/code_planner.py : def plan(query: str, tables: list, file_name: str) -> dict | None
  src/plan_executor.py: def execute(plan: dict, tables: list, query: str) -> str | None
`plan` returns a JSON-serialisable dict in the fixed plan schema (or None if
it can't plan). `execute` returns the FINAL formatted answer string (use
answer_format.format_answer) or None. The LLM fallback must emit the same
plan schema, so the executor is shared.
"""
import importlib


def _load(name, attr):
    try:
        mod = importlib.import_module(f"src.{name}")
        return getattr(mod, attr)
    except (ImportError, AttributeError):
        return None


def code_plan(query, tables, file_name):
    fn = _load("code_planner", "plan")
    return fn(query, tables, file_name) if fn else None  # TODO(KSP1)


def execute(plan, tables, query):
    fn = _load("plan_executor", "execute")
    return fn(plan, tables, query) if fn else None  # TODO(KSP1)


def have_executor():
    return _load("plan_executor", "execute") is not None
