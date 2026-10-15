"""Execute a plan (src/plan_schema.json) against parsed tables -> answer string."""
from __future__ import annotations

import datetime as dt
import math
import sys
import re

import numpy as np
import pandas as pd

from . import conventions as C
from .answer_format import FormatSpec, format_answer
from .planner import TOTAL_LABEL, View, _label_range, build_views
from .textmatch import Query, tokens
from .xlsx_tables import strip_footnotes


class PlanError(Exception):
    pass


def _view_for(plan, tables):
    views = build_views(tables)
    idx = (plan.get("table") or {}).get("index") or 0
    for v in views:
        if idx in v.idx:
            return v
    return views[0]


def _resolve_rows(view, paths):
    df = view.df
    groups = []
    for p in paths:
        sheet = None
        if "!" in p and view.stacked:
            sheet, p = p.split("!", 1)
        m = df.index[(df["_row_path"] == p) & ((df["_sheet"] == sheet) if sheet else True)].tolist()
        if not m:
            leaf = strip_footnotes(p.split(" > ")[-1])
            m = df.index[df["_label"] == leaf].tolist()[:1]
        if not m:
            raise PlanError(f"row not found: {p}")
        groups.append(m)
    return groups


def _col_values(view, col, idx):
    df = view.df
    if col.endswith(" | *"):
        pre = col[:-4]
        kids = [c for c in view.numeric_cols() if str(c).startswith(pre + " | ")]
        return df.loc[idx, kids].sum(axis=1, min_count=1)
    if col == "*":
        nc = [c for c in view.numeric_cols()]
        return df.loc[idx, nc].sum(axis=1, min_count=1)
    if col not in df:
        raise PlanError(f"column not found: {col}")
    return df.loc[idx, col]


def _base_rows(view, plan):
    df = view.df
    r = plan.get("rows") or {}
    idx = list(df.index)
    t = plan.get("table") or {}
    if t.get("sheet"):
        idx = [i for i in idx if df.at[i, "_sheet"] == t["sheet"]]
    if r.get("scope"):
        sc = r["scope"]
        idx = [i for i in idx if str(df.at[i, "_row_path"]).startswith(sc + " > ")] or idx
    return idx


def _aggregate_rows(view, plan, idx):
    """rows used when aggregating over 'all rows'."""
    df = view.df
    r = plan.get("rows") or {}
    if r.get("range"):
        lo, hi = r["range"]
        idx = [i for i in idx if (rg := _label_range(df.at[i, "_label"])) and lo <= rg[0] and rg[1] <= hi]
    if r.get("exclude_subtotals", True) or r.get("leaf_only"):
        idx = [i for i in idx if not bool(df.at[i, "_is_subtotal"]) and not TOTAL_LABEL.search(str(df.at[i, "_label"]))]
    # several root groups (Total/Men/Women...) and no scope: keep first root subtree
    roots = [i for i in idx if df.at[i, "_parent_row"] is None and _has_children(df, i)]
    return idx


def _has_children(df, i):
    return (df["_parent_row"] == df.at[i, "_row"]).any()


def _apply_where(view, idx, where):
    df = view.df
    for w in where or []:
        col, op, val = w["column"], w["op"], w["value"]
        keep = []
        for i in idx:
            v = df.at[i, col]
            if op in ("year==", "year>", "year<"):
                ok = isinstance(v, (dt.date, dt.datetime)) and {"year==": v.year == int(val), "year>": v.year > int(val),
                                                                "year<": v.year < int(val)}[op]
            else:
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    continue
                ok = {">=": v >= val, ">": v > val, "<=": v <= val, "<": v < val, "==": v == val, "!=": v != val}[op]
            if ok:
                keep.append(i)
        idx = keep
    return idx


def _ages(view, idx):
    df = view.df
    dcols = view.date_cols()
    if dcols:
        birth = [c for c in dcols if re.search(r"birth|born|dob", str(c), re.I)] or dcols[:1]
        b = birth[0]
        ref = [c for c in dcols if c != b]
        if C.AGE_AT_ROW_REFERENCE_DATE and ref:
            return {i: (df.at[i, ref[0]] - df.at[i, b]).days / 365.2425 if df.at[i, b] and df.at[i, ref[0]] else np.nan for i in idx}
        return {i: (dt.datetime(2100, 1, 1) - df.at[i, b]).days / 365.2425 for i in idx}
    agec = [c for c in view.numeric_cols() if re.fullmatch(r"age", tokens(str(c).split(" | ")[-1], keep_stop=True)[0] if tokens(str(c), keep_stop=True) else "")]
    if agec:
        return {i: df.at[i, agec[0]] for i in idx}
    raise PlanError("no age information")


def _order(view, idx, args):
    by = args.get("by")
    if by == "age":
        key = _ages(view, idx)
    elif by:
        key = {i: view.df.at[i, by] for i in idx}
    else:
        nc = view.numeric_cols()
        key = {i: view.df.at[i, nc[0]] for i in idx}
    items = [i for i in idx if key[i] is not None and not (isinstance(key[i], float) and math.isnan(key[i]))]
    return sorted(items, key=lambda i: key[i], reverse=args.get("order") == "desc")


def _ret(view, i, col):
    if col and col in view.df and col != view.label_name:
        return view.df.at[i, col]
    return view.df.at[i, "_label"] if not col else strip_footnotes(str(view.df.at[i, col]))


def _drop_total_cols(view, cols, idx):
    """When several columns are combined and one equals the sum of the others, keep only that one."""
    real = [c for c in cols if c in view.df]
    if len(real) >= 3:
        for c in [x for x in real if re.match(r"(grand )?total\b", str(x).split(" | ")[-1], re.I)]:
            others = [o for o in real if o != c]
            s = view.df.loc[idx, others].sum(axis=1)
            if np.allclose(s.values, view.df.loc[idx, c].values, atol=0.6 * len(others)):
                return [c]
    return cols


def execute(plan, tables):
    view = _view_for(plan, tables)
    df = view.df
    op = plan["op"]
    args = plan.get("args") or {}
    cols = list(plan.get("columns") or [])
    r = plan.get("rows") or {}
    base = _base_rows(view, plan)
    groups = _resolve_rows(view, r.get("paths") or [])
    if (plan.get("table") or {}).get("sheet"):
        groups = [[i for i in g if i in base] or g for g in groups]

    if op in ("lookup", "values") and r.get("where"):
        # rows picked by a text-value condition ('Ho's admission date', 'obtained by An Ho')
        hit = _apply_where(view, base, r.get("where"))
        if groups:
            groups = [[i for i in g if i in hit] or g for g in groups]
        elif hit:
            groups = [hit[:1]]

    def _cell(c, g):
        s = _col_values(view, c, g)
        return s.iloc[0] if len(s) == 1 else s.sum(min_count=1)

    def first_row():
        cand = [i for i in base if df.loc[i, view.numeric_cols()].notna().any()] if view.numeric_cols() else base
        return [cand[0]]

    def all_rows_or_first():
        # no row mentioned: if the table starts with a total/subtotal row use it, else aggregate all rows
        fr = first_row()
        if bool(df.at[fr[0], "_is_subtotal"]) or TOTAL_LABEL.search(str(df.at[fr[0], "_label"])) or op in ("lookup", "values") and len(base) == 1:
            return fr
        agg = _aggregate_rows(view, plan, base)
        return agg if (re.search(r"total|all", " ".join(map(str, cols))) is None and op in ("diff", "compare", "ratio", "sum")) else fr

    def cell_list():
        """values in question order"""
        gs = groups or [all_rows_or_first()]
        cs = cols or ["*" if len(df.loc[gs[0], view.numeric_cols()].dropna(axis=1).columns) != 1 else
                      df.loc[gs[0], view.numeric_cols()].dropna(axis=1).columns[0]]
        if cs == ["*"] and len(view.numeric_cols()) > 1 and op in ("lookup", "values"):
            nn = df.loc[gs[0][0], view.numeric_cols()].dropna()
            cs = [nn.index[0]] if len(nn) else cs
        out = []
        if len(gs) >= len(cs):
            for g in gs:
                for c in cs:
                    out.append(_cell(c, g))
        else:
            for c in cs:
                for g in gs:
                    out.append(_cell(c, g))
        return out

    if op in ("lookup", "values"):
        vals = cell_list()
        if plan["format"]["kind"] == "date" or (vals and isinstance(vals[0], (dt.date, dt.datetime))):
            pass
        val = vals if (op == "values" or len(vals) > 1) else vals[0]
    elif op in ("compare", "diff", "ratio"):
        vals = cell_list()
        if len(vals) < 2:
            raise PlanError(f"{op} needs two values, got {vals}")
        a, b = float(vals[0]), float(vals[1])
        if op == "ratio" and b == 0:
            raise PlanError("ratio: division by zero")
        if op == "compare":
            val = {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b, "==": a == b}[args.get("cmp", ">")]
        elif op == "diff":
            d = (b - a) if args.get("signed_order") == "second_minus_first" else (a - b)
            val = abs(d) if args.get("abs") else d
        else:
            val = a / b * (100 if args.get("percent") else 1)
    elif op in ("sum", "mean", "max", "min", "count"):
        if groups:
            idx = [i for g in groups for i in g]
        else:
            idx = _aggregate_rows(view, plan, base)
            if r.get("scope") is None and not r.get("range") and not (plan.get("table") or {}).get("sheet"):
                idx = _first_root_scope(view, idx)
        idx = _apply_where(view, idx, r.get("where"))
        if op == "count":
            val = len(idx)
        else:
            cs = cols or (["*"] if op == "sum" and len(view.numeric_cols()) > 1 else view.numeric_cols()[:1])
            cs = _drop_total_cols(view, cs, idx)
            series = pd.concat([_col_values(view, c, idx) for c in cs], ignore_index=True)
            if series.map(lambda v: isinstance(v, (dt.date, dt.datetime))).any():
                s = [v for v in series if isinstance(v, (dt.date, dt.datetime))]
                val = max(s) if op == "max" else min(s)
            else:
                series = pd.to_numeric(series, errors="coerce")
                val = {"sum": series.sum(), "mean": series.mean(), "max": series.max(), "min": series.min()}[op]
    elif op in ("argmax", "argmin"):
        over = args.get("over", "rows")
        sign = 1 if op == "argmax" else -1
        tf = (lambda x: abs(x)) if args.get("abs") else (lambda x: x)
        if over == "columns":
            g = groups[0] if groups else first_row()
            cand = [c for c in view.numeric_cols() if not re.match(r"(grand )?total\b", str(c).split(" | ")[-1], re.I)]
            vals = {c: _col_values(view, c, g).sum(min_count=1) for c in cand}
            vals = {c: v for c, v in vals.items() if v is not None and not (isinstance(v, float) and math.isnan(v))}
            best = _pick(list(vals), lambda c: sign * tf(vals[c]), plan, "columns")
            val = [str(b).split(" | ")[-1] for b in best]
        elif over == "sheets":
            g = groups[0]
            leaf = df.at[g[0], "_label"]
            c = cols[0] if cols else view.numeric_cols()[0]
            per = {}
            for i in df.index[df["_label"] == leaf]:
                v = _col_values(view, c, [i]).iloc[0]
                per[df.at[i, "_sheet"]] = v
            val = _pick(list(per), lambda s: sign * tf(per[s]), plan, "sheets")
        elif args.get("delta"):
            # change between two periods (sheets of a stacked view, or two columns): later minus earlier
            a_, b_ = args["delta"]
            c = cols[0] if cols else view.numeric_cols()[0]
            if view.stacked and a_ in set(df["_sheet"].astype(str)):
                base_idx = df.index[df["_sheet"].astype(str) == a_]
                late = {df.at[i, "_label"]: _col_values(view, c, [i]).iloc[0] for i in df.index[df["_sheet"].astype(str) == b_]}
                vals = {df.at[i, "_label"]: late.get(df.at[i, "_label"]) - _col_values(view, c, [i]).iloc[0]
                        for i in base_idx if late.get(df.at[i, "_label"]) is not None}
            else:
                idx = _first_root_scope(view, _aggregate_rows(view, plan, base))
                vals = {df.at[i, "_label"]: _col_values(view, b_, [i]).iloc[0] - _col_values(view, a_, [i]).iloc[0] for i in idx}
            vals = {k: v for k, v in vals.items() if isinstance(v, (int, float, np.floating)) and not math.isnan(v)}
            val = _pick(list(vals), lambda k: sign * tf(vals[k]), plan, "rows")
        else:
            idx = _aggregate_rows(view, plan, base)
            idx = _first_root_scope(view, idx) if r.get("scope") is None else idx
            idx = _apply_where(view, idx, r.get("where"))
            c = cols[0] if cols else view.numeric_cols()[0]
            vals = {i: _col_values(view, c, [i]).iloc[0] for i in idx}
            vals = {i: (v.toordinal() if isinstance(v, (dt.date, dt.datetime)) else v) for i, v in vals.items()}
            vals = {i: v for i, v in vals.items() if isinstance(v, (int, float, np.floating)) and not math.isnan(v)}
            best = _pick(list(vals), lambda i: sign * tf(vals[i]), plan, "rows")
            val = [_ret(view, b, args.get("return")) for b in best]
        if isinstance(val, list) and len(val) == 1:
            val = val[0]
    elif op == "rank":
        c = cols[0] if cols else view.numeric_cols()[0]
        if not groups:
            idx = _apply_where(view, _aggregate_rows(view, plan, base), r.get("where"))
            groups = [[i] for i in idx]
        names = args.get("names") or [_ret(view, g[0], args.get("return")) for g in groups]
        vals = [_col_values(view, c, g).sum() for g in groups]
        order = sorted(range(len(vals)), key=lambda k: vals[k], reverse=args.get("order", "desc") == "desc")
        val = [names[k] for k in order][: args.get("k") or None]
    elif op in ("nth", "topk_agg"):
        idx = _aggregate_rows(view, plan, base)
        idx = _apply_where(view, idx, r.get("where"))
        ordered = _order(view, idx, args)
        if op == "nth":
            i = ordered[args.get("n", 1) - 1]
            val = _ret(view, i, args.get("return"))
        else:
            top = ordered[: args.get("k", 1)]
            col = args.get("return") or view.numeric_cols()[-1]
            s = pd.to_numeric(df.loc[top, col], errors="coerce")
            val = getattr(s, args.get("agg", "max"))()
    elif op == "share":
        g = groups[-1] if groups else None
        if g is None:
            raise PlanError("share needs a row")
        c = cols[0] if cols else view.numeric_cols()[0]
        part = _col_values(view, c, g).sum()
        path = df.at[g[0], "_row_path"].split(" > ")
        # whole: mentioned ancestor (scope) else root ancestor
        whole_path = r.get("scope") or path[0]
        w = df.index[df["_row_path"] == whole_path].tolist()
        if not w:
            par = df.at[g[0], "_parent_row"]
            w = df.index[df["_row"] == par].tolist()
        whole = _col_values(view, c, w[:1]).sum()
        if not whole or (isinstance(whole, float) and math.isnan(whole)):
            raise PlanError("share: zero or missing total")
        val = part / whole * (100 if args.get("percent", True) else 1)
    else:
        raise PlanError(f"unknown op {op}")
    return val


def _pick(cands, score, plan, what):
    """best key(s) by score in table order, honoring args.ties"""
    best = max(score(k) for k in cands)
    tied = [k for k in cands if math.isclose(score(k), best, rel_tol=1e-9, abs_tol=1e-9)]
    if len(tied) > 1:
        mode = (plan.get("args") or {}).get("ties", "first" if not C.TIES_RETURN_ALL else "all")
        if mode == "first":
            print(f"[tie] {what}: {len(tied)} tied, keeping first", file=sys.stderr)
            return tied[:1]
        return tied
    return tied


def _first_root_scope(view, idx):
    """If rows form several parallel root groups (e.g. Total / Men / Women), keep the first group."""
    df = view.df
    roots = [i for i in df.index if df.at[i, "_is_subtotal"] and pd.isna(df.at[i, "_parent_row"])]
    if len(roots) >= 2:
        r0 = df.at[roots[0], "_row"]
        sub = set()
        frontier = {r0}
        while frontier:
            kids = set(df.loc[df["_parent_row"].isin(frontier), "_row"])
            sub |= kids
            frontier = kids
        sel = [i for i in idx if df.at[i, "_row"] in sub]
        sec = df.at[roots[0], "_section"]
        return sel or idx
    return idx


def to_spec(fmt: dict) -> FormatSpec:
    return FormatSpec(kind=fmt.get("kind", "text"), decimals=fmt.get("decimals"), count=fmt.get("count"),
                      item_kind=fmt.get("item_kind"), sep=fmt.get("sep", ", "), order=fmt.get("order") or [],
                      thousands=bool(fmt.get("thousands")))


def answer(plan, tables) -> str:
    val = execute(plan, tables)
    spec = to_spec(plan["format"])
    if isinstance(val, (dt.date, dt.datetime)):
        return val.strftime(C.DATE_OUTPUT_FORMAT)
    if isinstance(val, (bool, np.bool_)) and spec.kind != "yesno":
        spec.kind = "yesno"
    if isinstance(val, str) and spec.kind in ("int", "decimal", "number"):
        spec.kind = "name"
    if isinstance(val, (int, float, np.floating, np.integer)) and spec.kind in ("name", "text"):
        f = float(val)
        return str(int(f)) if f.is_integer() else f"{f:.10g}"
    if isinstance(val, list) and spec.kind != "list":
        spec = FormatSpec(kind="list", sep=spec.sep or ", ",
                          item_kind="name" if isinstance(val[0], str) else "int" if spec.kind == "int" else "number")
    return format_answer(val, spec)
