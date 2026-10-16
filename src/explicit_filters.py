"""Exact column-bound conjunctions, independent from operation-word heuristics."""
import re
from dataclasses import asdict

from .answer_format import parse_instruction

_NUMBER = r"(-?\d+(?:\.\d+)?)"
_OPS = {"at least": ">=", "at most": "<=", "greater than": ">", "more than": ">",
        "less than": "<", "below": "<", "above": ">", ">=": ">=", "<=": "<=", ">": ">", "<": "<", "=": "==", "==": "=="}


def predicates(body, view):
    """Return explicit predicates plus whether a mentioned categorical binding was unresolved."""
    clauses, unresolved = [], False
    date_columns, numeric_columns = set(view.date_cols()), set(view.numeric_cols())
    for column in view.columns:
        name = r"(?<!\w)[`'\"]?" + re.escape(str(column)) + r"[`'\"]?(?!\w)"
        if column in date_columns:
            continue
        if column in numeric_columns:
            bound = re.search(name + r"\s+(?:is\s+)?(?:from|between)\s+" + _NUMBER
                              + r"\s+(?:through|to|and)\s+" + _NUMBER, body, re.I)
            if bound:
                clauses.extend([{"column":column, "op":">=", "value":float(bound[1])},
                                {"column":column, "op":"<=", "value":float(bound[2])}])
                continue
            match = re.search(name + r"\s*(?:is\s+)?(?:strictly\s+)?(" + "|".join(re.escape(x) for x in sorted(_OPS, key=len, reverse=True))
                              + r")\s*" + _NUMBER, body, re.I)
            if match:
                clauses.append({"column":column, "op":_OPS[match[1].lower()], "value":float(match[2])})
        else:
            found = False
            for value in sorted({str(v) for v in view.df[column].dropna()}, key=len, reverse=True):
                if re.search(name + r"\s*(?:(?:is|equals?|=)\s*)?[`'\"]?" + re.escape(value) + r"[`'\"]?(?!\w)", body, re.I):
                    clauses.append({"column":column, "op":"==", "value":value}); found = True; break
            if not found and re.search(name + r"\s+(?:is|equals?)\s+", body, re.I):
                unresolved = True
    return clauses, unresolved


def plan(query, tables):
    if not re.search(r"\b(?:where|with|among|for)\b", query, re.I):
        return None
    from .planner import build_views, strip_instruction
    spec = parse_instruction(query)
    body = strip_instruction(query, spec)
    # The v1 where contract is a conjunction. Do not turn a union into AND.
    if re.search(r"\bor\b", body, re.I):
        return None
    candidates = []
    for view in build_views(tables):
        clauses, unresolved = predicates(body, view)
        if unresolved or len({w["column"] for w in clauses}) < 2:
            continue
        op, result_columns = None, []
        if re.search(r"^\s*count\b|\bhow many\b", body, re.I):
            op = "count"
        else:
            for column in view.numeric_cols():
                match = re.search(r"\b(sum|total|average|mean|maximum|minimum|max|min)\s+(?:of\s+)?[`'\"]?"
                                  + re.escape(column) + r"[`'\"]?(?!\w)", body, re.I)
                if match:
                    if op is not None:
                        op = None; break
                    op = {"total":"sum", "average":"mean", "maximum":"max", "minimum":"min"}.get(match[1].lower(), match[1].lower())
                    result_columns = [column]
            if op is None and re.search(r"^\s*(?:list|identify|show|which)\b", body, re.I):
                op = "select"
        if op is None:
            continue
        fmt = asdict(spec); fmt.pop("instruction")
        candidates.append({"version":1, "table":{"index":view.idx[0], "stack_sheets":view.stacked},
                           "rows":{"paths":[], "where":clauses, "exclude_subtotals":True},
                           "columns":result_columns, "op":op, "args":{}, "format":fmt})
    return candidates[0] if len(candidates) == 1 else None
