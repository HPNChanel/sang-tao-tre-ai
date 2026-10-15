"""Rule-based planner: question text + parsed tables -> plan dict (src/plan_schema.json).
General NLP heuristics only; no file/id/question-specific rules."""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from . import conventions as C
from .answer_format import parse_instruction
from .subtotals import detect_subtotals
from .textmatch import Query, tokens
from .xlsx_tables import strip_footnotes

ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
       "ninth": 9, "tenth": 10}
NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "twenty": 20}
SUP_MAX = r"highest|most|largest|greatest|maximum|biggest|latest|top|oldest"
SUP_MIN = r"lowest|least|fewest|smallest|minimum|earliest|youngest"
TOTAL_LABEL = re.compile(r"^(grand )?total\b|^all\b", re.I)


# ---------------------------------------------------------------- tables
class View:
    """One table (or same-schema tables stacked across sheets) prepared for planning."""

    def __init__(self, tables, idx):
        self.tables, self.idx = tables, idx
        dfs = []
        for t in tables:
            d = detect_subtotals(t.df)
            d["_sheet"] = t.sheet
            d["_label"] = d[t.label_col].astype(str).map(lambda s: strip_footnotes(s)) if t.label_col in d else d["_row_path"].str.split(" > ").str[-1]
            dfs.append(d)
        self.df = pd.concat(dfs, ignore_index=True)
        self.label_name = tables[0].label_col
        self.stacked = len(tables) > 1
        self.columns = [c for c in self.df.columns if not str(c).startswith("_") and c != self.label_name]
        self.title = " / ".join(dict.fromkeys(t.title for t in tables if t.title))
        self.meta = " ".join(m for t in tables for m in t.meta)

    def numeric_cols(self):
        return [c for c in self.columns if pd.api.types.is_numeric_dtype(self.df[c])]

    def date_cols(self):
        return [c for c in self.columns if self.df[c].map(lambda v: isinstance(v, (dt.date, dt.datetime))).any()]


def build_views(tables):
    views, used = [], set()
    for i, t in enumerate(tables):
        if i in used:
            continue
        group = [i]
        cols = list(t.df.columns)
        for j in range(i + 1, len(tables)):
            u = tables[j]
            if j not in used and u.sheet not in {tables[k].sheet for k in group} and list(u.df.columns) == cols:
                group.append(j)
        used |= set(group)
        views.append(View([tables[k] for k in group], group))
    return views


# ---------------------------------------------------------------- matching
def match_columns(view: View, q: Query, exclude=frozenset()):
    """Greedy cover of query tokens by column keys (prefix of 'A | B | C' parts)."""
    cands = {}
    allcols = view.columns + ([view.label_name] if view.label_name else [])
    for ci, col in enumerate(allcols):
        parts = str(col).split(" | ")
        fr = [q.match(p, exclude) for p in parts]
        strong = [i for i, (f, _) in enumerate(fr) if f >= 0.5]
        if not strong:
            continue
        L = max(strong) + 1
        score = sum(f for f, _ in fr[:L]) - 0.5 * sum(1 for f, _ in fr[:L] if f < 0.5)
        score += 0.1 * sum(len(pos) > 0 for f, pos in fr[:L] for _ in [0])
        score += 0.05 * sum(len(tokens(p)) * f for p, (f, _) in zip(parts[:L], fr[:L]))
        pos = set().union(*[p for _, p in fr[:L]])
        key = tuple(parts[:L])
        if key not in cands or cands[key][0] < score:
            cands[key] = (score, pos, ci)
    kept, covered = [], set()
    for key, (score, pos, ci) in sorted(cands.items(), key=lambda kv: (-kv[1][0], kv[1][2])):
        if score < 0.5 or not (pos - covered):
            continue
        kept.append((key, pos))
        covered |= pos
    # drop keys that are prefixes of other kept keys
    kept = [(k, p) for k, p in kept if not any(o != k and o[:len(k)] == k for o, _ in kept)]
    def _fu(k):
        others = set().union(*[o[1] for o in kept if o is not k]) if len(kept) > 1 else set()
        return min((k[1] - others) or k[1])
    keys = {id(k): _fu(k) for k in kept}
    kept.sort(key=lambda k: keys[id(k)])
    out = []
    for key, pos in kept:
        out.append((resolve_key(view, key), pos))
    # ranges like 'Jan-Apr' / 'Jan to Mar' between two single-part columns
    out = _expand_ranges(view, q, out)
    return out, covered


def resolve_key(view, key):
    allcols = view.columns + ([view.label_name] if view.label_name else [])
    name = " | ".join(key)
    if name in allcols:
        return name
    kids = [c for c in allcols if str(c).startswith(name + " | ")]
    if C.PREFER_TOTAL_SUBCOLUMN:
        tot = [c for c in kids if re.match(r"total\b", str(c)[len(name) + 3:], re.I)]
        if tot:
            return tot[0]
    if len(kids) == 1:
        return kids[0]
    return name + " | *"


def _expand_ranges(view, q, cols):
    names = [c for c, _ in cols]
    m = re.finditer(r"\b([A-Za-z]{3,9}|\d{4})\s*(?:-|–|to|through)\s*([A-Za-z]{3,9}|\d{4})\b", q.text)
    for mm in m:
        a, b = tokens(mm.group(1)), tokens(mm.group(2))
        if not a or not b:
            continue
        ia = [i for i, c in enumerate(view.columns) if tokens(c) == a]
        ib = [i for i, c in enumerate(view.columns) if tokens(c) == b]
        if ia and ib and ia[0] < ib[0]:
            span = view.columns[ia[0]: ib[0] + 1]
            keep = [c for c in names if c not in span and not _is_total_col(c)]
            # replace: range columns in order
            names = keep + span
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append((n, set()))
    return out if names != [c for c, _ in cols] else cols


def _is_total_col(c):
    return bool(re.match(r"(grand )?total\b", str(c).split(" | ")[-1], re.I))


def match_rows(view: View, q: Query, exclude=frozenset()):
    df = view.df
    cands = []
    for i in range(len(df)):
        path = str(df["_row_path"].iat[i]).split(" > ")
        leaf = strip_footnotes(path[-1])
        f, pos = q.match(leaf, exclude)
        nt = len(tokens(leaf))
        lt = tokens(leaf)
        head = leaf.split("(")[0]
        hf, hpos = q.match(head, exclude) if "(" in leaf else (0, set())
        if hf < 1.0 and any(t[0].isdigit() and not q.positions(t, exclude) for t in lt):
            continue
        if hf == 1.0 and f < 1.0:
            f, pos = 1.0, hpos
        elif not (f == 1.0 or (f >= 0.5 and nt >= 2 and lt and q.positions(lt[0], exclude))):
            continue
        anc = [q.match(a, exclude) for a in path[:-1]]
        sheet_f, sheet_pos = q.match(df["_sheet"].iat[i], exclude) if view.stacked else (0, set())
        score = 2 * f + 0.05 * nt * f + sum(a for a, _ in anc) + sheet_f
        apos = set().union(*[p for _, p in anc]) if anc else set()
        cands.append((score, i, pos | apos | sheet_pos, " > ".join(path)))
    kept, covered = [], set()
    for score, i, pos, path in sorted(cands, key=lambda c: (-c[0], c[1])):
        if not (pos - covered):
            continue
        kept.append((path, pos, i))
        covered |= pos
    kept = [k for k in kept if not any(o[0] != k[0] and o[0].startswith(k[0] + " > ") for o in kept)]
    # order by first position of the tokens each row uniquely covers
    def first_unique(k):
        others = set().union(*[o[1] for o in kept if o is not k]) if len(kept) > 1 else set()
        u = k[1] - others
        return min(u or k[1])
    fk = {id(k): first_unique(k) for k in kept}
    kept.sort(key=lambda k: fk[id(k)])
    return [(p, pos) for p, pos, _ in kept], covered


# ---------------------------------------------------------------- planning
def strip_instruction(query, spec):
    q = query
    if spec.instruction:
        q = q.replace(spec.instruction, " ")
    q = re.sub(r"\b(Provide|Answer|Respond)\b[^.?!]*[.?!]?", " ", q)
    return q


def plan_question(query: str, tables) -> dict:
    return plan_with_confidence(query, tables)[0]


def plan_with_confidence(query: str, tables):
    """-> (plan, confidence in [0,1], reasons). Vietnamese questions are keyword-translated first."""
    from .vi import is_vietnamese, translate
    reasons = []
    vi_factor = 1.0
    if is_vietnamese(query):
        query, hits = translate(query)
        vi_factor = 0.9 if hits >= 2 else 0.6
        reasons.append(f"vi:{hits}")
    plan, info = _plan_core(query, tables)
    conf, r2 = _confidence(query, plan, info)
    return plan, conf * vi_factor, reasons + r2


def _plan_core(query: str, tables):
    spec = parse_instruction(query)
    body = strip_instruction(query, spec)
    low = body.lower()
    q = Query(body)
    views = build_views(tables)

    # ---- choose view
    best = None
    for vi, v in enumerate(views):
        cols, ccov = match_columns(v, q)
        rows, rcov = match_rows(v, q, frozenset(ccov))
        s = len(ccov) + len(rcov)
        tm = re.search(r"\btable\s+([a-z0-9]{1,3})\b", low)
        if tm and re.search(rf"\btable\s+{re.escape(tm.group(1))}\b", v.title.lower()):
            s += 10
        tf, _ = q.match(v.title)
        s += 0.5 * tf
        if v.stacked:
            s += 0.5
        s += 0.01 * min(len(v.df), 50)
        if best is None or s > best[0]:
            best = (s, vi, v, cols, rows, set(ccov) | set(rcov))
    _, vi, view, cols, rows, best_cov = best
    # a 'Total' sub-column chosen by preference is dropped when a specific sibling is also chosen
    names = [c for c, _ in cols]
    def _par(c):
        return str(c).rsplit(" | ", 1)[0] if " | " in str(c) else None
    cols = [(c, p) for c, p in cols if not (_par(c) and re.match(r"total\b", str(c).rsplit(" | ", 1)[1], re.I)
                                            and any(o != c and _par(o) == _par(c) for o in names))]
    label_cols = [c for c, _ in cols if c == view.label_name]
    cols = [c for c, _ in cols if c != view.label_name]
    row_paths = [p for p, _ in rows]

    plan = {"version": 1,
            "table": {"index": view.idx[0], "sheet": None, "title_hint": None, "stack_sheets": view.stacked},
            "rows": {"paths": row_paths, "scope": None, "range": None, "leaf_only": False,
                     "exclude_subtotals": C.EXCLUDE_SUBTOTALS_IN_AGGREGATES, "where": []},
            "columns": cols, "op": "lookup", "args": {},
            "format": {"kind": spec.kind, "decimals": spec.decimals, "count": spec.count,
                       "item_kind": spec.item_kind, "sep": spec.sep, "order": spec.order}}
    if re.search(r"yyyy-mm-dd|\bdate\b.*format", query.lower()):
        plan["format"]["kind"] = "date"
    if view.stacked:
        for s in dict.fromkeys(view.df["_sheet"]):
            if re.search(rf"\b{re.escape(str(s).lower())}\b", low) and not re.search(r"\b(which|what) (year|sheet)", low):
                plan["table"]["sheet"] = s
    num_cols = view.numeric_cols()
    args = plan["args"]

    # numeric range filter for rows ('aged 25 to 64') when no row matched those numbers
    rm = re.search(r"\b(\d+)\s*(?:to|-|–)\s*(\d+)\b", body)
    if rm and not (len(rm.group(1)) == 4 and len(rm.group(2)) == 4) and not any(re.search(rf"\b{rm.group(1)}\b.*\b{rm.group(2)}\b", p.split(" > ")[-1]) for p in row_paths):
        lo, hi = float(rm.group(1)), float(rm.group(2))
        if any(_label_range(l) for l in view.df["_label"]):
            plan["rows"]["range"] = [lo, hi]
            plan["rows"]["leaf_only"] = True
            plan["rows"]["paths"] = row_paths = [p for p in row_paths if (rg := _label_range(p.split(" > ")[-1])) and lo <= rg[0] and rg[1] <= hi]
    if re.search(r"non-overlapping|most detailed|individual", low):
        plan["rows"]["leaf_only"] = True

    # ---- operation
    sup_max, sup_min = re.search(rf"\b({SUP_MAX})\b", low), re.search(rf"\b({SUP_MIN})\b", low)
    which = re.search(r"\b(which|who|identify|name the|in what|list all|name all)\b", low)
    nth = re.search(r"\b(" + "|".join(ORD) + r"|\d+(?:st|nd|rd|th))[- ](" + SUP_MAX + "|" + SUP_MIN + r")\b", low) or \
        re.search(r"\bthe ()(youngest|oldest)\b(?! \w+ (?:people|persons))", low)
    _unused = re.search(r"\b(" + "|".join(ORD) + r"|\d+(?:st|nd|rd|th))[- ](" + SUP_MAX + "|" + SUP_MIN + r")\b", low)
    topk = re.search(r"\b(?:among|of) the (\w+) (" + SUP_MAX + "|" + SUP_MIN + r")\b", low)
    if spec.kind == "yesno":
        plan["op"] = "compare"
        args["cmp"] = "<" if re.search(r"\b(less|lower|smaller|fewer|below|under)\b", low) else ">"
    elif re.search(r"\brank\b|\bdescending\b|\bascending\b|\bfrom (most|highest|largest|greatest|biggest|least|fewest|lowest|smallest) to (fewest|least|lowest|smallest|most|highest|largest|greatest|biggest)\b|\bin (descending|ascending) order\b", low):
        plan["op"] = "rank"
        asc = re.search(r"ascending|from (least|fewest|lowest|smallest) to", low)
        args["order"] = "asc" if asc else "desc"
        plan["rows"]["paths"] = []
        lm = re.search(r":\s*([^.?]+)", body)
        if lm:
            names = [n.strip() for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", lm.group(1)) if n.strip()]
            args["names"] = names
            paths = []
            for n in names:
                rr, _ = match_rows(view, Query(n))
                paths.append(rr[0][0] if rr else n)
            plan["rows"]["paths"] = paths
    elif re.search(r"\bshare\b|\bproportion\b|percentage of|what percent", low):
        plan["op"] = "share"
        args["percent"] = True
    elif re.search(r"\(([^()/]+)/([^()]+)\)|\bratio\b|\brate\b.*/", body):
        plan["op"] = "ratio"
        args["percent"] = bool(re.search(r"percent|rate", low))
        m = re.search(r"\(([^()/]+)/([^()]+)\)", body)
        if m:
            a, _ = match_columns(view, Query(m.group(1)))
            b, _ = match_columns(view, Query(m.group(2)))
            if a and b:
                plan["columns"] = [a[0][0], b[0][0]]
    elif nth:
        g = nth.group(1)
        args["n"] = ORD.get(g) or (int(re.match(r"\d+", g).group()) if g else 1)
        _order_args(args, nth.group(2), view, cols + label_cols)
        plan["op"] = "nth"
    elif topk and (topk.group(1) in NUMW or topk.group(1).isdigit()):
        args["k"] = NUMW.get(topk.group(1)) or int(topk.group(1))
        _order_args(args, topk.group(2), view, cols)
        rest = low[topk.end():]
        args["agg"] = "max" if re.search(rf"\b({SUP_MAX})\b", rest) else "min" if re.search(rf"\b({SUP_MIN})\b", rest) else "mean" if re.search(r"average|mean", rest) else "sum"
        plan["op"] = "topk_agg"
    elif (sup_max or sup_min) and which and spec.kind == "name":
        plan["op"] = "argmax" if sup_max and not (sup_min and sup_min.start() < sup_max.start()) else "argmin"
        args["abs"] = bool(re.search(r"absolute", low))
        _choose_over(plan, view, rows, cols, low)
    elif re.search(r"how (many|much) (more|less|fewer|higher|lower)|\bdifference\b|\bdiffer\b|\bchange\b|\bgap\b|\bincrease\b|\bdecrease\b", low):
        plan["op"] = "diff"
        args["abs"] = bool(re.search(r"absolute|\bdifference between\b", low)) and not re.search(r"\bsigned\b", query.lower())
        args["signed_order"] = "second_minus_first" if re.search(r"\bchange\b|\bfrom\b.*\bto\b|increase|decrease", low) else "first_minus_second"
    elif re.search(r"\baverage\b|\bmean\b", low) and not any(re.search(r"\baverage\b|\bmean\b", c, re.I) for c in cols):
        plan["op"] = "mean"
    elif (sup_max or sup_min) and (which or (spec.kind == "name" and not re.search(r"\bwhat (was|is) the (highest|lowest)", low))):
        plan["op"] = "argmax" if sup_max and not (sup_min and sup_min.start() < sup_max.start()) else "argmin"
        args["abs"] = bool(re.search(r"absolute", low))
        _choose_over(plan, view, rows, cols, low)
    elif (sup_max or sup_min) and plan["format"]["kind"] in ("date",) or ((sup_max or sup_min) and re.search(r"\bwhat (was|is) the (highest|lowest|maximum|minimum|largest|smallest|earliest|latest)", low)):
        plan["op"] = "max" if sup_max else "min"
        if not cols and view.date_cols():
            plan["columns"] = view.date_cols()[:1]
    elif re.search(r"\bsumm?ing\b|\bsum\b|\btotal\b|\bcombined\b|\baltogether\b|\bin all\b|\bacross all\b", low) and not re.search(r"^\s*how many\b(?!.*\b(total|sum|combined)\b)", low):
        plan["op"] = "sum"
    elif re.search(r"\bhow many\b", low):
        conds = _conditions(body, view, cols, num_cols)
        countish = bool(conds) or plan["rows"]["leaf_only"] or any(c not in num_cols for c in cols) or (
            cols == [] and not row_paths) or (view.label_name and q.match(view.label_name)[0] > 0 and not any(c in num_cols for c in cols))
        if countish:
            plan["op"] = "count"
            plan["rows"]["where"] = conds
            plan["columns"] = [c for c in cols if c in num_cols and not conds]
            if plan["rows"]["where"]:
                plan["columns"] = []
        else:
            plan["op"] = "lookup"
    elif spec.kind == "list":
        plan["op"] = "values"
    else:
        plan["op"] = "lookup"
    if plan["op"] in ("max", "min", "mean", "sum", "argmax", "argmin", "topk_agg", "nth") and not plan["rows"]["where"]:
        conds = _conditions(body, view, plan["columns"], num_cols)
        if conds:
            plan["rows"]["where"] = conds
            cc = {w["column"] for w in conds}
            rest = [c for c in plan["columns"] if c not in cc]
            plan["columns"] = rest or plan["columns"]
            plan["rows"]["paths"] = [p for p in plan["rows"]["paths"]] if plan["op"] not in ("max", "min", "mean", "sum") else plan["rows"]["paths"]
    if view.stacked and plan["op"] in ("compare", "diff", "values", "ratio") and len(set(plan["rows"]["paths"])) == 1:
        sheets = []
        for sname in dict.fromkeys(view.df["_sheet"]):
            m = re.search(rf"\b{re.escape(str(sname).lower())}\b", low)
            if m:
                sheets.append((m.start(), sname))
        if len(sheets) >= 2:
            p0 = plan["rows"]["paths"][0]
            plan["rows"]["paths"] = [f"{sn}!{p0}" for _, sn in sorted(sheets)]
            plan["table"]["sheet"] = None
    if plan["op"] in ("sum", "mean", "diff", "compare", "ratio", "share", "argmax", "argmin", "rank"):
        plan["columns"] = [c for c in plan["columns"] if c in num_cols or c.endswith(" | *") or c == "*"]
    if plan["op"] in ("lookup", "values") and (len(plan["rows"]["paths"]) * max(1, len(plan["columns"])) > 1 or spec.kind == "list"):
        plan["op"] = "values"
    if plan["op"] in ("sum", "diff", "lookup", "values", "compare") and not plan["columns"] and re.search(r"across all|all \w+ combined|in all", low):
        plan["columns"] = ["*"]
    return plan, {"view": view, "q": q, "rows": rows, "cols_cov": best_cov, "body": body}


def _label_range(label):
    m = re.search(r"(\d+)\s*(?:to|-|–)\s*(\d+)", str(label))
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _order_args(args, word, view, cols):
    if word in ("youngest", "oldest"):
        args["by"] = "age"
        args["order"] = "desc" if word == "oldest" else "asc"
    else:
        num = [c for c in cols if c in view.numeric_cols()]
        args["by"] = num[0] if num else None
        args["order"] = "desc" if re.fullmatch(SUP_MAX, word) else "asc"
    ret = [c for c in cols if c != args.get("by") and not (args.get("by") == "age" and re.search(r"\b(age|birth|born)\b", str(c), re.I))]
    args["return"] = ret[0] if ret else None


def _tie_mode(low, fmt):
    """'all' unless the wording/format demands exactly one result."""
    if not C.TIES_RETURN_ALL:
        return "first"
    if fmt.get("kind") == "list":
        return "all"
    if re.search(r"\b(which ones|list all|name all|all (the )?\w+ (that|which|with))\b", low):
        return "all"
    if re.search(r"\bwhich one\b|\bthe (single|only)\b", low):
        return "first"
    m = re.search(r"\b(?:which|what|identify the|name the)\s+(\w+)(?:\s+(\w+))?", low)
    if not m:
        return "all"
    words = [w for w in m.groups() if w]
    plural = any(w.endswith("s") and not w.endswith(("ss", "us", "is")) and w not in ("was", "has", "does", "its")
                 for w in words)
    return "all" if plural else "first"


def _choose_over(plan, view, rows, cols, low):
    args = plan["args"]
    args["ties"] = _tie_mode(low, plan["format"])
    if view.stacked and len(rows) == 1 and re.search(r"\b(which|what|in which) (year|sheet|period)\b", low):
        args["over"] = "sheets"
    elif len(rows) == 1 and not [c for c in cols if c != view.label_name]:
        args["over"] = "columns"
    else:
        args["over"] = "rows"
        plan["columns"] = [c for c in cols if c != view.label_name]
        if args["over"] == "rows" and len(rows) == 1 and plan["columns"]:
            # the matched row is context (scope), e.g. a province, not the answer
            plan["rows"]["scope"] = rows[0][0]
            plan["rows"]["paths"] = []
        elif args["over"] == "rows":
            plan["rows"]["paths"] = []


_COND_PRE = {"at least": ">=", "no less than": ">=", "more than": ">", "greater than": ">", "over": ">", "above": ">",
             "older than": ">", "at most": "<=", "no more than": "<=", "less than": "<", "fewer than": "<",
             "below": "<", "under": "<", "younger than": "<"}


def _condition_column(window, view, cols, num_cols):
    if re.search(r"\b(aged?|old|older|young|younger|years? old)\b", window):
        agec = [c for c in num_cols if re.search(r"\bage\b", str(c), re.I)]
        if agec:
            return agec[0]
    m, _ = match_columns(view, Query(window))
    m = [c for c, _ in m if c in num_cols]
    if m:
        return m[0]
    c = [x for x in cols if x in num_cols]
    return (c or num_cols[-1:] or [None])[0]


def _conditions(body, view, cols, num_cols):
    out = []
    low = body.lower()
    pre = "|".join(sorted(_COND_PRE, key=len, reverse=True))
    for m in re.finditer(rf"\b({pre})\s+(-?\d+(?:\.\d+)?)", low):
        if m.group(1) == "over" and re.match(r"\s*(years?|and over)", low[m.end():]):
            continue
        w = low[max(0, m.start() - 40): m.end() + 20]
        c = _condition_column(w, view, cols, num_cols)
        if c:
            out.append({"column": c, "op": _COND_PRE[m.group(1)], "value": float(m.group(2))})
    for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s+(?:[a-z]+\s+)?(?:or|and)\s+(higher|more|older|above|greater|over|up|lower|less|fewer|younger|below|under)\b", low):
        op = ">=" if m.group(2) in ("higher", "more", "older", "above", "greater", "over", "up") else "<="
        w = low[max(0, m.start() - 40): m.end() + 10]
        c = _condition_column(w, view, cols, num_cols)
        if c and not (len(m.group(1)) == 4 and m.group(1).startswith(("19", "20")) and c == m.group(1)):
            out.append({"column": c, "op": op, "value": float(m.group(1))})
    y = re.search(r"\b(?:in|during|of)\s+((?:19|20)\d{2})\b", body)
    if y and view.date_cols():
        dc = [c for c in cols if c in view.date_cols()] or view.date_cols()
        out.append({"column": dc[0], "op": "year==", "value": int(y.group(1))})
    return out


OP_WORDS = set("""total sum combined average mean highest lowest most least fewest largest smallest maximum minimum
greater less more fewer higher lower exceed difference absolute change rank order descending ascending share
proportion percentage percent ratio rate which who what how many much second third first youngest oldest earliest latest
compare between across all both respectively value number answer""".split())


def _confidence(query, plan, info):
    from .answer_type import compatible, expected_type, plan_type
    view, q = info["view"], info["q"]
    reasons, c = [], 1.0
    exp = expected_type(query, plan["format"])
    got = plan_type(plan, view)
    if not compatible(exp, got):
        return 0.0, [f"type:{exp}!={got}"]
    op, cols, rows = plan["op"], plan["columns"], plan["rows"]
    nnum = len(view.numeric_cols())
    nrows = len(rows["paths"])
    if op in ("lookup", "values"):
        if not nrows and not cols:
            c *= 0.3; reasons.append("lookup:no-match")
        elif not cols and nnum > 1:
            c *= 0.6; reasons.append("lookup:no-col")
    if op in ("compare", "diff", "ratio"):
        if max(nrows, 1) * max(len(cols), 1) < 2 and not any(str(x).endswith("*") for x in cols):
            c *= 0.3; reasons.append(f"{op}:<2 values")
    if op in ("argmax", "argmin") and (plan["args"].get("over") == "rows") and not cols and nnum > 1:
        c *= 0.5; reasons.append("arg:no-col")
    if op in ("mean", "max", "min") and not cols and nnum > 1:
        c *= 0.6; reasons.append("agg:no-col")
    # how much of the question's content is explained by matched names
    from .textmatch import tokens as _t
    content = [p for t, p in q.toks if t not in OP_WORDS and not t.isdigit() or t.isdigit()]
    cov = info.get("cols_cov") or set()
    if content:
        ratio = len([p for p in content if p in cov]) / len(content)
        if ratio < 0.2:
            c *= 0.6; reasons.append(f"coverage:{ratio:.2f}")
    return c, reasons
