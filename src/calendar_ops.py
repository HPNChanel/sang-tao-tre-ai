"""Calendar intervals and completed ages, with explicit date operands."""
import datetime as dt
import re
from dataclasses import asdict

from .answer_format import parse_instruction
from .native_comparisons import mentions
from .vi import fold


def interval(first, second, unit):
    if not isinstance(first, (dt.date, dt.datetime)) or not isinstance(second, (dt.date, dt.datetime)):
        raise ValueError("calendar operands must be dates")
    first = first.date() if isinstance(first, dt.datetime) else first
    second = second.date() if isinstance(second, dt.datetime) else second
    if unit == "days":
        return (second - first).days
    if unit != "completed_years" or second < first:
        raise ValueError("completed age needs an event on or after birth")
    return second.year - first.year - ((second.month, second.day) < (first.month, first.day))


def plan(query, tables):
    low = fold(query).lower()
    years = bool(re.search(r"\b(?:completed|full) years\b|\btuoi tron\b", low))
    days = bool(re.search(r"\b(?:calendar )?days\b|\bngay\b", low) and re.search(r"\bfrom\b.*\bto\b|\btu\b.*\bden\b", low))
    if not years and not days:
        return None
    from .planner import build_views, _birth_column
    spec = parse_instruction(query)
    fmt = asdict(spec); fmt.pop("instruction")
    candidates = []
    for view in build_views(tables):
        dates = view.date_cols()
        named_dates = list(dict.fromkeys(mentions(query, dates)))
        labels = [label for label in dict.fromkeys(mentions(query, view.df["_label"]))
                  if re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", query, re.I)]
        paths = [p for label in labels for p in view.df.loc[view.df["_label"] == label, "_row_path"]]
        base = {"version": 1, "table": {"index": view.idx[0], "stack_sheets": view.stacked},
                "rows": {"paths": paths, "exclude_subtotals": True}, "format": fmt}
        if years:
            birth = _birth_column(view)
            refs = [c for c in named_dates if c != birth]
            if birth not in dates or len(refs) != 1:
                continue
            cols = [birth, refs[0]]
            if len(paths) == 1:
                base.update(op="date_diff", columns=cols, args={"date_unit": "completed_years"})
            elif not paths and re.search(r"\bgreatest|\blargest|\boldest|\bmaximum|\byoungest|\bsmallest|\bminimum", low):
                minimum = bool(re.search(r"\byoungest|\bsmallest|\bminimum", low))
                label = spec.kind in ("name", "list") or bool(re.search(r"\bwho\b|\bwhich\b", low))
                base.update(op=("argmin" if minimum else "argmax") if label else ("min" if minimum else "max"),
                            columns=cols, args={"metric":"completed_years", "ties":"first"})
            else:
                continue
        else:
            if len(paths) == 2 and len(named_dates) == 1:
                cols = named_dates
            elif len(paths) == 1 and len(named_dates) == 2:
                cols = named_dates
            else:
                continue
            base.update(op="date_diff", columns=cols, args={"date_unit":"days"})
        candidates.append(base)
    return candidates[0] if len(candidates) == 1 else None
