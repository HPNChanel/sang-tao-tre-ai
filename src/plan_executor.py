"""Seam for run.py: execute a plan dict -> formatted answer string (or None)."""
import sys

from . import conventions as C
from .answer_type import answer_ok, expected_type
from .executor import answer


def execute(plan, tables, query=None):
    if not plan:
        return None
    try:
        ans = answer(plan, tables)
    except Exception as e:
        print(f"[exec] error {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if C.POST_EXEC_TYPE_CHECK and query:
        q = query
        try:
            from .vi import is_vietnamese, translate
            if is_vietnamese(q):
                q = translate(q)[0]
        except Exception:
            pass
        exp = expected_type(q, plan.get("format") or {})
        if not answer_ok(exp, ans, plan.get("format") or {}):
            print(f"[exec] answer {ans!r} does not fit expected type {exp} -> fallback", file=sys.stderr)
            return None
    return ans
