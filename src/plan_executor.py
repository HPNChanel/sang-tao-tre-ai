"""Seam for run.py: execute a plan dict -> formatted answer string (or None)."""
from .executor import answer


def execute(plan, tables, query=None):
    if not plan:
        return None
    try:
        return answer(plan, tables)
    except Exception:
        return None
