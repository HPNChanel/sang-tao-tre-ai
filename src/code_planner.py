"""Seam for run.py: rule-based planner."""
from .planner import plan_question


def plan(query, tables, file_name=None):
    try:
        return plan_question(query, tables)
    except Exception:
        return None
