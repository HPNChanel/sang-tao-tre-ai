"""Composed reductions: derive one numeric measure per candidate, then rank it."""
import re

import pandas as pd


def refine(plan, view, body, spec):
    from .planner import match_columns
    from .textmatch import Query, tokens
    from .xlsx_tables import strip_footnotes

    low = body.lower()
    extreme = re.search(r"\b(highest|largest|greatest|most|maximum|lowest|smallest|least|minimum)\b", low)
    if not extreme:
        return False
    metric = None
    cols = list(plan["columns"])
    groups = []
    if re.search(r"\bprevious year\b", low) and re.search(r"\b(fall|fell|drop|decrease|increase|rise|grew)\b", low):
        metric = "previous_difference"
        if len(cols) != 1 or cols[0] not in view.numeric_cols():
            return False
    elif re.search(r"\b(gap|difference|increase|decrease)\b", low):
        metric = "difference"
        years = re.search(r"\bfrom (\d{4}) to (\d{4})\b", low)
        if years:
            # Change direction follows the named interval, never worksheet
            # column order. A decrease measures earlier minus later.
            decreasing = bool(re.search(r"\bdecrease\b", low))
            operand_years = [years[1], years[2]] if decreasing else [years[2], years[1]]
            parents = {}
            for c in view.numeric_cols():
                if " | " not in c:
                    continue
                parent, leaf = c.rsplit(" | ", 1)
                if leaf in years.groups() and Query(body).match(parent)[0] == 1.0:
                    parents.setdefault(parent, {})[leaf] = c
            groups = [{"name": k, "columns": [v[y] for y in operand_years]} for k, v in parents.items() if len(v) == 2]
            if not groups and re.search(r"\b(increase|decrease)\b", low):
                named = {y: [c for c in cols if re.findall(r"\b\d{4}\b", c) == [y]]
                         for y in operand_years}
                if all(len(named[y]) == 1 for y in operand_years):
                    cols = [named[y][0] for y in operand_years]
        if groups:
            if len(groups) < 2 or len(plan["rows"]["paths"]) != 1:
                return False
        else:
            between = re.search(r"\bbetween (.+?) and (.+?)(?:,|\?|$)", body, re.I)
            if between:
                operands = []
                for part in between.groups():
                    # Parent prose may repeat across quartile/median leaves;
                    # an explicitly named leaf is the narrower evidence.
                    leaf_hits = [c for c in view.numeric_cols() if " | " in c
                                 and tokens(c.split(" | ")[-1])
                                 and Query(part).match(c.split(" | ")[-1])[0] == 1.0]
                    matches, _ = match_columns(view, Query(part))
                    matches = [c for c, _ in matches if c in view.numeric_cols()]
                    matches = leaf_hits or matches
                    if len(matches) != 1:
                        break
                    operands += matches
                if len(operands) == 2:
                    cols = operands
            if len(cols) != 2 or any(c not in view.numeric_cols() for c in cols):
                return False
    elif re.search(r"\b(sum|combined)\b", low) and len(cols) >= 2:
        metric = "sum"
    if not metric:
        return False
    args = plan["args"]
    args.clear()
    args.update(metric=metric, over="column_groups" if groups else "rows", ties="first")
    if groups:
        args["metric_groups"] = groups
        cols = list(dict.fromkeys(c for g in groups for c in g["columns"]))
    else:
        plan["rows"]["paths"] = []
        plan["rows"]["scope"] = None
        q = Query(body)
        sections = [s for s in dict.fromkeys(view.df["_section"]) if s]
        named = [s for s in sections if (label := re.sub(r"\([^)]*\)", "", strip_footnotes(str(s))).strip())
                 and tokens(label) and q.match(label)[0] == 1.0]
        if len(named) == 1:
            plan["rows"]["scope"] = named[0]
    if metric == "previous_difference":
        args["reverse_metric"] = bool(re.search(r"\b(fall|fell|drop|decrease)\b", low))
        plan["rows"]["label_kind"] = "year"
    args["abs"] = bool(re.search(r"\babsolute\b", low))
    minimum = extreme[1] in {"lowest", "smallest", "least", "minimum"}
    label_result = spec.kind in {"name", "list"}
    plan["op"] = ("argmin" if minimum else "argmax") if label_result else ("min" if minimum else "max")
    plan["columns"] = cols
    if spec.item_kinds:
        args["include_value"] = True
    return True


def series(view, idx, columns, args):
    metric = args["metric"]
    if (metric == "difference" and len(columns) != 2) or (metric == "previous_difference" and len(columns) != 1) or not columns:
        raise ValueError("derived metric operand count does not match its operation")
    frame = view.df.loc[idx, columns].apply(pd.to_numeric, errors="coerce")
    if metric == "previous_difference":
        # Adjacency is chronological, never worksheet order or a skipped year.
        order = sorted(idx, key=lambda i: int(view.df.at[i, "_label"]))
        values = frame.loc[order, columns[0]]
        years = pd.Series([int(view.df.at[i, "_label"]) for i in order], index=order)
        out = values.diff().where(years.diff() == 1)
    elif metric == "difference":
        out = frame.iloc[:, 0] - frame.iloc[:, 1]
    elif metric == "sum":
        out = frame.sum(axis=1, min_count=len(columns))
    else:
        raise ValueError(f"unsupported derived metric: {metric}")
    return -out if args.get("reverse_metric") else out
