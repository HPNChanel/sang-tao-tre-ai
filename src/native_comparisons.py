"""Native comparison grammars grounded exclusively in worksheet labels.

No entity translation or dataset vocabulary: unresolved operands abstain.
"""
import re


def mentions(text, labels):
    occupied = set()
    found = []
    for label in sorted(set(str(x) for x in labels if str(x)), key=len, reverse=True):
        for m in re.finditer(re.escape(label), text, re.I):
            span = set(range(m.start(), m.end()))
            if not span & occupied:
                occupied |= span
                found.append((m.start(), label))
    return [label for _, label in sorted(found)]


def plan(query, tables):
    division = _division(query, tables)
    if division is not None:
        return division
    chinese = bool(re.search(r"比.+(?:低|高)多少个?百分点", query))
    korean = "퍼센트" in query and bool(re.search(r"감소|증가", query)) and "대비" in query
    if not (chinese or korean):
        return None
    from .planner import build_views
    views = build_views(tables)
    candidates = []
    for view in views:
        df = view.df
        sections = list(dict.fromkeys(mentions(query, df["_section"])))
        rows = df[df["_section"] == sections[0]] if len(sections) == 1 else df
        labels = list(dict.fromkeys(mentions(query, rows["_label"])))
        if len(labels) != 2:
            continue
        paths = []
        for label in labels:
            paths += rows.loc[rows["_label"] == label, "_row_path"].tolist()
        if len(paths) != 2:
            continue
        col_mentions = mentions(query, [p for c in view.numeric_cols() for p in c.split(" | ")])
        cols = [c for c in view.numeric_cols() if any(p in col_mentions for p in c.split(" | "))]
        if korean and len(cols) > 1:
            cols = [c for c in cols if c.split(" | ")[-1].lower() in {"number", "count", "total"}]
        if len(cols) != 1:
            continue
        decimals = None
        if chinese:
            m = re.search(r"保留([一二三四五六\d])位小数", query)
            if m:
                decimals = int(m[1]) if m[1].isdigit() else "一二三四五六".index(m[1]) + 1
        else:
            m = re.search(r"소수점\s*(첫째|둘째|셋째|\d+)\s*자리", query)
            if m:
                decimals = int(m[1]) if m[1].isdigit() else {"첫째": 1, "둘째": 2, "셋째": 3}[m[1]]
        args = {"signed_order": "second_minus_first" if chinese and "低" in query else "first_minus_second"}
        if korean:
            before, after = query.split("대비", 1)
            if labels[0] not in before or labels[1] not in after:
                continue
            args = {"decrease": "감소" in query}
        candidates.append({"version": 1, "table": {"index": view.idx[0], "stack_sheets": view.stacked},
                           "rows": {"paths": paths}, "columns": cols,
                           "op": "percent_change" if korean else "diff", "args": args,
                           "format": {"kind": "decimal" if decimals is not None else "number", "decimals": decimals}})
    return candidates[0] if len(candidates) == 1 else None


def _division(query, tables):
    """Explicit divide-A-by-B syntax supplies the operation and operand order."""
    match = re.search(r"\bdivide\s+(.+?)\s+by\s+([^.;?]+)", query, re.I)
    if not match:
        return None
    from dataclasses import asdict
    from .answer_format import parse_instruction
    from .planner import build_views
    from .textmatch import Query
    candidates = []
    for view in build_views(tables):
        operands = [[c for c in view.numeric_cols() if Query(part).match(c)[0] == 1.0] for part in match.groups()]
        paths = [p for p, label in zip(view.df["_row_path"], view.df["_label"])
                 if re.search(r"(?<!\w)" + re.escape(str(label)) + r"(?!\w)", query, re.I)]
        if any(len(x) != 1 for x in operands) or len(paths) != 1 or operands[0] == operands[1]:
            continue
        fmt = asdict(parse_instruction(query)); fmt.pop("instruction")
        candidates.append({"version":1, "table":{"index":view.idx[0], "stack_sheets":view.stacked},
                           "rows":{"paths":paths}, "columns":[x[0] for x in operands],
                           "op":"ratio", "args":{}, "format":fmt})
    return candidates[0] if len(candidates) == 1 else None
