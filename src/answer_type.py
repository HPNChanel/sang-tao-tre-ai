"""Expected answer type from the question (wh-word + instruction) and type checks
for plans (pre-exec) and answers (post-exec). General, no dataset specifics."""
from __future__ import annotations

import re

# types: yesno, count, number, label, year, date, list, any
NUMERIC = {"count", "number"}


def expected_type(query: str, fmt: dict) -> str:
    low = query.lower()
    k = fmt.get("kind")
    if k == "yesno":
        return "yesno"
    if k == "list":
        return "list"
    if k == "date" or re.search(r"yyyy-mm-dd|\bwhat date\b|\bwhich date\b|\bon what day\b", low):
        return "date"
    if re.search(r"\b(which|what|in which) year\b|4-digit year", low):
        return "year"
    if re.search(r"\bhow many (more|less|fewer)\b|\bhow much\b|\bby how much\b", low):
        return "number"
    if re.search(r"\bhow many\b", low):
        return "count"
    if k in ("int", "decimal", "number"):
        return "number"
    if k == "name":
        return "label"
    if re.match(r"\s*(is|are|was|were|does|do|did|has|have|had|can|could|will)\b", low):
        return "yesno"
    if re.search(r"\b(which|who|whom)\b", low):
        return "label"
    if re.search(r"\bwhat (is|was|are|were) the (total|sum|average|mean|number|value|difference|share|ratio|rate|percentage)\b", low):
        return "number"
    return "any"


def plan_type(plan: dict, view=None) -> str:
    op = plan.get("op")
    a = plan.get("args") or {}
    if op == "compare":
        return "yesno"
    if op == "count":
        return "count"
    if op in ("argmax", "argmin"):
        return "year" if a.get("over") == "sheets" else "label"
    if op == "rank":
        return "list"
    if op == "values":
        return "list"
    if op == "nth":
        ret = a.get("return")
        if view is not None and ret and ret in view.date_cols():
            return "date"
        if view is not None and ret and ret in view.numeric_cols():
            return "number"
        return "label"
    if op in ("max", "min") and view is not None and plan.get("columns") and all(c in view.date_cols() for c in plan["columns"]):
        return "date"
    return "number"


def compatible(expected: str, got: str) -> bool:
    if expected == "any" or expected == got:
        return True
    if expected in NUMERIC and got in NUMERIC:
        return True
    if expected == "year" and got in ("label", "number"):
        return True
    if expected == "list" and got in ("list", "label"):
        return True
    if expected == "label" and got == "list":
        return True  # ties
    return False


def answer_ok(expected: str, ans: str, fmt: dict) -> bool:
    if ans is None or str(ans).strip() == "":
        return False
    a = str(ans).strip()
    num = re.fullmatch(r"-?\d+(\.\d+)?", a) is not None
    if expected == "yesno":
        return a in ("Yes", "No")
    if expected == "count":
        return re.fullmatch(r"\d+", a) is not None
    if expected == "number":
        return num or bool(re.fullmatch(r"-?\d+(\.\d+)?(, -?\d+(\.\d+)?)*", a))
    if expected == "year":
        return re.fullmatch(r"(1[89]|20|21)\d{2}", a) is not None or not num
    if expected == "date":
        return re.fullmatch(r"\d{4}-\d{2}-\d{2}", a) is not None
    if expected == "label":
        return not num or fmt.get("kind") == "list"
    return True
