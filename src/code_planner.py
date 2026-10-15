"""Seam for run.py: rule-based planner with an evidence-based confidence gate."""
import sys

from . import conventions as C
from .planner import plan_with_confidence as _pwc


def plan_with_confidence(query, tables, file_name=None):
    """-> (best_guess_plan | None, confidence 0..1, reasons list[str]). Never raises.
    The plan is returned even below the threshold (best guess); callers decide whether to use it."""
    try:
        return _pwc(query, tables)
    except Exception as e:
        return None, 0.0, [f"error:{type(e).__name__}: {e}"]


def plan(query, tables, file_name=None):
    """Backward-compatible: plan dict, or None when confidence < conventions.PLAN_CONFIDENCE_THRESHOLD."""
    p, conf, reasons = plan_with_confidence(query, tables, file_name)
    if p is None or conf < C.PLAN_CONFIDENCE_THRESHOLD:
        print(f"[plan] low confidence {conf:.2f} {reasons} -> fallback", file=sys.stderr)
        return None
    return p
