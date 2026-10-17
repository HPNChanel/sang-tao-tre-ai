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
    if re.search(r"\b(which|what|in which) year\b|4-digit year|\bonly the year\b", low):
        return "year"
    if re.search(r"\bhow many (more|less|fewer|percentage points?|percent points?)\b|\bhow much\b|\bby how much\b", low):
        return "number"
    if re.search(r"\bhow many (?:signed )?(?:calendar )?days\b", low):
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
    if op == "select":
        return "label"
    if op in ("argmax", "argmin"):
        if a.get("include_value"):
            return "list"
        return "year" if a.get("over") == "sheets" else "label"
    if op == "rank":
        return "list"
    if op == "values":
        return "list"
    if op == "winner_diff":
        return "list"
    if op == "nth":
        ret = a.get("return")
        if view is not None and ret and ret in view.date_cols():
            return "date"
        if view is not None and ret and ret in view.numeric_cols():
            return "number"
        return "label"
    if a.get("metric") == "completed_years":
        return "number"
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


def answer_ok(expected: str, ans: str, fmt: dict, query: str | None = None, plan: dict | None = None) -> bool:
    if ans is None or str(ans).strip() == "":
        return False
    # Formatting affixes do not change the computed value's type. Require
    # their exact presence before checking the numeric/list payload.
    ans = str(ans)
    from .answer_format import parse_instruction
    if query and fmt.get("empty") is not None and ans == fmt["empty"] == parse_instruction(query).empty:
        return True
    prefix, suffix = fmt.get("prefix", ""), fmt.get("suffix", "")
    if not ans.startswith(prefix) or not ans.endswith(suffix):
        return False
    ans = ans[len(prefix):len(ans) - len(suffix) if suffix else None]
    from . import blank_guards as G
    # A ranking that already has its column and direction, or a single superlative,
    # is not rejected on shape. The same predicate gates the pre-exec blanks.
    if not (G.ranking_ready(plan) or G.single_superlative(plan)):
        if not _answer_counts(ans, fmt, query, plan):
            return False
    a = str(ans).strip()
    if fmt.get("item_kinds"):
        parts = a.rsplit(fmt.get("sep", ", "), len(fmt["item_kinds"]) - 1)
        if len(parts) != len(fmt["item_kinds"]):
            return False
        for part, kind in zip(parts, fmt["item_kinds"]):
            if kind in ("number", "decimal", "int") and not re.fullmatch(r"-?\d+(?:\.\d+)?", part):
                return False
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


def _answer_counts(ans: str, fmt: dict, query: str | None, plan: dict | None) -> bool:
    """A demanded pair needs two elements. A list of labels is not a single number."""
    from . import blank_guards as G
    fmt = fmt or {}
    if G.pair_demanded(fmt, query) and G.item_count(ans, fmt) != 2:
        return False
    blob = query or ""
    if G.asks_single_number(blob, fmt) and G.label_list(ans, fmt):
        return False
    if G.asks_list(blob, fmt) and G.single_number(ans) and _aggregate_answer(plan):
        return False
    return True


def _aggregate_answer(plan: dict | None) -> bool:
    op = (plan or {}).get("op")
    return op in {"sum", "mean", "count", "max", "min", "topk_agg"}
