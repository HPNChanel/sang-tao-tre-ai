"""Seam for run.py: rule-based planner with confidence gate."""
import sys

from . import conventions as C
from .planner import plan_with_confidence


def plan(query, tables, file_name=None):
    try:
        p, conf, reasons = plan_with_confidence(query, tables)
    except Exception as e:
        print(f"[plan] error {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if conf < C.PLAN_CONFIDENCE_THRESHOLD:
        print(f"[plan] low confidence {conf:.2f} {reasons} -> fallback", file=sys.stderr)
        return None
    return p
