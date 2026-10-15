"""Rule-based planner: question text + parsed tables -> plan dict (src/plan_schema.json).
General NLP heuristics only; no file/id/question-specific rules."""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from . import conventions as C
from .answer_format import parse_instruction
from .subtotals import detect_subtotals
from .textmatch import Query, tok_eq, tokens
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
    q = re.sub(r"\((?:e\.g\.|eg\.?|for example|i\.e\.)[^)]*\)", " ", query, flags=re.I)
    if spec.instruction:
        q = q.replace(spec.instruction, " ")
    q = re.sub(r"\b(Provide|Answer|Respond)\b[^.?!]*[.?!]?", " ", q)
    return q


def plan_question(query: str, tables) -> dict:
    return plan_with_confidence(query, tables)[0]


def plan_with_confidence(query: str, tables, strict=None):
    """-> (plan, confidence in [0,1], reasons). Vietnamese questions are keyword-translated first.
    strict=None -> conventions.strict_active() (on only with an LLM key); strict=True -> evidence accounting (42d00ea)."""
    from . import textmatch
    st = C.strict_active(strict)
    old = textmatch.STRICT_POSSESSIVE
    textmatch.STRICT_POSSESSIVE = st
    try:
        return _plan_with_confidence(query, tables, st)
    finally:
        textmatch.STRICT_POSSESSIVE = old


def _plan_with_confidence(query, tables, strict):
    from .vi import is_vietnamese, translate
    reasons = []
    vi_factor = 1.0
    if is_vietnamese(query):
        query, hits = translate(query)
        vi_factor = 0.9 if hits >= 2 else 0.6
        reasons.append(f"vi:{hits}")
    plan, info = _plan_core(query, tables, strict)
    conf, r2 = (_confidence if strict else _confidence_legacy)(query, plan, info)
    return plan, conf * vi_factor, reasons + r2


def _plan_core(query: str, tables, strict=False):
    spec = parse_instruction(query)
    body = strip_instruction(query, spec)
    body, excl_phrases = _split_exclusions(body)
    low = body.lower()
    q = Query(body)
    views = build_views(tables)

    # ---- choose view
    best = None
    vscores = []
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
        vscores.append((s, vi, bool(tm and s >= 10)))
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
    # a 'Total' row matched only by the word 'total' is the operation, not a row, when other rows are named
    if len(rows) > 1 and re.search(r"\btotal\b", low):
        rows = [r for r in rows if not TOTAL_LABEL.match(r[0].split(" > ")[-1])] or rows
    row_paths = [p for p, _ in rows]

    plan = {"version": 1,
            "table": {"index": view.idx[0], "sheet": None, "title_hint": None, "stack_sheets": view.stacked},
            "rows": {"paths": row_paths, "scope": None, "range": None, "leaf_only": False,
                     "exclude_subtotals": C.EXCLUDE_SUBTOTALS_IN_AGGREGATES, "where": []},
            "columns": cols, "op": "lookup", "args": {},
            "format": {"kind": spec.kind, "decimals": spec.decimals, "count": spec.count,
                       "item_kind": spec.item_kind, "sep": spec.sep, "order": spec.order,
                       "thousands": spec.thousands}}
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
    topn = None if topk else _top_n(low, view, spec, num_cols)
    if topn and topn[3]:
        cols = [topn[3]] + [c for c in cols if c != topn[3]]
        plan["columns"] = cols
    if spec.kind == "yesno":
        plan["op"] = "compare"
        args["cmp"] = "<" if re.search(r"\b(less|lower|smaller|fewer|below|under)\b", low) else ">"
        if re.search(r"\bfrom\b.+\bto\b", low) and not re.search(r"\bthan\b", low):
            # 'did X grow from A to B' compares B against A (first-mentioned value is A)
            if re.search(r"\b(grow|grew|grown|increase[sd]?|rise|rose|risen|go up|went up|climb(ed)?|gain(ed)?)\b", low):
                args["cmp"] = "<"
            elif re.search(r"\b(decline[sd]?|decrease[sd]?|fall|fell|fallen|drop(ped)?|go down|went down|shrink|shrank)\b", low):
                args["cmp"] = ">"
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
    elif topn and spec.kind == "list":
        plan["op"] = "rank"
        args["k"] = topn[0]
        args["order"] = "desc" if re.fullmatch(SUP_MAX, topn[1]) else "asc"
        plan["rows"]["paths"] = []
        if topn[2]:
            args["return"] = topn[2]
    elif re.search(r"\bshare\b|\bproportion\b|percentage of|what percent", low) and not _SHARE_TIE.search(low):
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
        if args.get("return") is None and spec.instruction:
            # 'Answer with only the last name' names the column to return
            ic, _ = match_columns(view, Query(spec.instruction))
            ic = [c for c, _ in ic if c not in num_cols and c != args.get("by")]
            if ic:
                args["return"] = ic[0]
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
    elif re.search(r"\bhow many\b|^\s*count\b", low):
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
    if plan["op"] in ("values", "lookup") and (which or spec.kind == "list") and not plan["rows"]["paths"]:
        eq = [w for w in _conditions(body, view, cols, num_cols) if w["op"] == "=="]
        if eq:
            # 'which X have/are tied at Y equal to N' -> every row meeting the condition
            plan["op"], plan["rows"]["where"], args["ties"], args["over"] = "argmax", eq, "all", "rows"
            plan["columns"] = [eq[0]["column"]]
    if excl_phrases and plan["op"] in ("sum", "mean", "max", "min", "argmax", "argmin", "count", "rank", "topk_agg", "nth"):
        for ph in excl_phrases:
            xr, _ = match_rows(view, Query(ph))
            for p, _ in xr[:1]:
                leaf = p.split(" > ")[-1]
                plan["rows"]["where"].append({"column": "_label", "op": "!=", "value": leaf})
                plan["rows"]["paths"] = [x for x in plan["rows"]["paths"] if x.split(" > ")[-1] != leaf]
    if plan["op"] in ("argmax", "argmin") and args.get("over") == "rows" and \
            re.search(r"\b(increase|growth|rise|gain|change|decrease|decline|drop|fall)\b", low):
        _set_delta(plan, view, low)
    if plan["op"] in ("argmax", "argmin") and args.get("over") == "rows" and spec.instruction and not args.get("return"):
        # 'Answer with the last names ...' -> return that column instead of the row label
        ic, _ = match_columns(view, Query(spec.instruction))
        ic = [c for c, _ in ic if c not in num_cols and c != view.label_name]
        if ic:
            args["return"] = ic[0]
    if plan["op"] in ("max", "min", "mean", "sum", "argmax", "argmin", "count", "rank") and not plan["rows"]["paths"] \
            and not plan["rows"].get("scope"):
        # a section named in the question ('of men', 'among women') scopes row aggregates
        secs = [x for x in dict.fromkeys(view.df["_section"]) if x]
        hit = [x for x in secs if q.match(x)[0] == 1.0]
        if len(hit) > 1:
            hit = [x for x in hit if not TOTAL_LABEL.match(str(x))] or hit
        if len(hit) == 1:
            plan["rows"]["scope"] = hit[0]
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
    return plan, {"view": view, "q": q, "rows": rows, "cols_cov": best_cov, "body": body,
                  "views": views, "vscores": vscores, "vi": vi, "spec": spec}


def _set_delta(plan, view, low):
    """'biggest increase from 2023 to 2024' -> argmax of (2024 - 2023) over sheets or columns named by period."""
    m = re.search(r"\b(?:from|between)\s+(\S+)\s+(?:to|and)\s+(\S+?)[?.,]?(?:\s|$)", low)
    if not m:
        return
    a, b = m.group(1).strip("?.,"), m.group(2).strip("?.,")
    if view.stacked:
        sheets = {str(s).lower(): str(s) for s in dict.fromkeys(view.df["_sheet"])}
        if a in sheets and b in sheets:
            plan["args"]["delta"] = [sheets[a], sheets[b]]
            plan["table"]["sheet"] = None
            if re.search(r"\b(decrease|decline|drop|fall)\b", low) and plan["op"] == "argmax":
                plan["op"] = "argmin"
        return
    ca = [c for c in view.numeric_cols() if re.search(rf"\b{re.escape(a)}\b", str(c).lower())]
    cb = [c for c in view.numeric_cols() if re.search(rf"\b{re.escape(b)}\b", str(c).lower())]
    if len(ca) == 1 and len(cb) == 1 and ca != cb:
        plan["args"]["delta"] = [ca[0], cb[0]]
        plan["columns"] = []
        if re.search(r"\b(decrease|decline|drop|fall)\b", low) and plan["op"] == "argmax":
            plan["op"] = "argmin"


_SHARE_TIE = re.compile(r"\bshare (the|a) (same|top|highest|lowest|first|maximum|minimum)\b")


def _top_n(low, view, spec, num_cols):
    """'top 3 X by Y', 'the three highest scorers', 'name the 2 largest ...' -> (k, sup word, return col, by col)."""
    sup = SUP_MAX + "|" + SUP_MIN
    nw = "|".join(NUMW)
    m = re.search(rf"\b(?:top|bottom)\s+(\d+|{nw})\b", low) or re.search(rf"\b(?:the\s+)?(\d+|{nw})\s+({sup})\b", low)
    if not m:
        return None
    k = int(m.group(1)) if m.group(1).isdigit() else NUMW[m.group(1)]
    if k < 2:
        return None
    word = m.group(2) if m.lastindex >= 2 and m.group(2) else ("lowest" if "bottom" in m.group(0) else "highest")
    ret = by = None
    if spec.instruction:
        ic, _ = match_columns(view, Query(spec.instruction))
        ic = [c for c, _ in ic if c not in num_cols]
        ret = ic[0] if ic else None
    tail = Query(low[m.end():])
    bc, _ = match_columns(view, tail)
    bc = [c for c, _ in bc if c in num_cols]
    by = bc[0] if bc else None
    if by is None:
        # agent nouns: 'scorers' -> Score, 'earners' -> Earnings
        words = [re.sub(r"(ers|er|rs)$", "", w) for w in re.findall(r"[a-z]{4,}", low[m.end():])]
        hit = [c for c in num_cols for w in words if len(w) >= 4 and any(t.startswith(w) for t in tokens(str(c).split(" | ")[-1]))]
        by = hit[0] if hit else None
    return k, word, ret, by


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


_EXCL = re.compile(r"\b(?:excluding|except(?: for)?|not counting|other than|apart from|leaving out|without)\s+(?:the\s+)?([^,?.;]+?)(?=\s*(?:,|\?|\.|;|$|\bwhat\b|\bwhich\b|\bhow\b))", re.I)


def _split_exclusions(body):
    """'Excluding the 16 to 19 group, what ...' -> (body without the clause, ['16 to 19 group'])."""
    out = []
    def keep(m):
        if re.match(r"\s*(commas?|units?|decimals?|spaces?)\b", m.group(1), re.I):
            return m.group(0)
        out.append(m.group(1))
        return " "
    return _EXCL.sub(keep, body), out


def _label_spans(low, view):
    """Spans of the question that spell out a row label containing a number ('55 years and over'):
    such numbers name a row, they are not a filter threshold."""
    spans = []
    for lab in dict.fromkeys(view.df["_label"].astype(str)):
        lab = strip_footnotes(lab).lower().strip()
        if not re.search(r"\d", lab) or len(lab) < 4:
            continue
        for v in {lab, re.sub(r"\s+years?\b", "", lab)}:
            for m in re.finditer(re.escape(v), low):
                spans.append((m.start(), m.end()))
    return spans


def _in_spans(m, spans):
    return any(a <= m.start() < b or a < m.end() <= b for a, b in spans)


def _conditions(body, view, cols, num_cols):
    out = []
    low = body.lower()
    pre = "|".join(sorted(_COND_PRE, key=len, reverse=True))
    lab_spans = _label_spans(low, view)
    used = []
    for m in re.finditer(r"\bbetween\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)(\s+(?:inclusive|exclusive))?", low):
        a, b = m.group(1), m.group(2)
        if _in_spans(m, lab_spans) or (len(a) == 4 and len(b) == 4 and a[:2] in ("19", "20") and b[:2] in ("19", "20")):
            continue
        w = low[max(0, m.start() - 40): m.end() + 40]
        c = _condition_column(w, view, cols, num_cols)
        if c:
            ex = "exclusive" in (m.group(3) or "")
            out += [{"column": c, "op": ">" if ex else ">=", "value": float(a)}, {"column": c, "op": "<" if ex else "<=", "value": float(b)}]
            used.append((m.start(), m.end()))
    for m in re.finditer(r"\b(?:equal to|equals|exactly|of exactly)\s+(-?\d+(?:\.\d+)?)", low):
        if _in_spans(m, lab_spans):
            continue
        c = _condition_column(low[max(0, m.start() - 60): m.end()], view, cols, num_cols)
        if c:
            out.append({"column": c, "op": "==", "value": float(m.group(1))})
    for m in re.finditer(r"\b(born|admitted|hired|joined|started|founded)\s+(after|before|in)\s+((?:19|20)\d{2})\b", low):
        dc = [c for c in view.date_cols() if tokens(str(c)) and any(tok_eq(t, tokens(m.group(1))[0]) or t.startswith(tokens(m.group(1))[0][:4]) for t in tokens(str(c)))] or view.date_cols()
        if dc:
            out.append({"column": dc[0], "op": {"after": "year>", "before": "year<", "in": "year=="}[m.group(2)], "value": int(m.group(3))})
            used.append((m.start(), m.end()))
    lab_spans = lab_spans + used
    for m in re.finditer(rf"\b({pre})\s+(-?\d+(?:\.\d+)?)", low):
        if _in_spans(m, lab_spans):
            continue
        if m.group(1) == "over" and re.match(r"\s*(years?|and over)", low[m.end():]):
            continue
        w = low[max(0, m.start() - 40): m.end() + 20]
        c = _condition_column(w, view, cols, num_cols)
        after = re.split(r"\band\b|\bor\b|,|\?|\.(?!\d)", low[m.end():], maxsplit=1)[0]
        if len(tokens(after)) >= 1:
            ac, _ = match_columns(view, Query(after))
            ac = [x for x, _ in ac if x in num_cols]
            if len(ac) == 1:
                c = ac[0]  # 'more than 3000 <column words>': the column follows the number
        if c:
            op = _COND_PRE[m.group(1)]
            if re.search(r"\bnot\s+(?:be\s+|been\s+)?$", low[max(0, m.start() - 12): m.start()]):
                op = {">": "<=", ">=": "<", "<": ">=", "<=": ">"}[op]
            out.append({"column": c, "op": op, "value": float(m.group(2))})
    for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s+(?:[a-z]+\s+)?(?:or|and)\s+(higher|more|older|above|greater|over|up|lower|less|fewer|younger|below|under)\b", low):
        if _in_spans(m, lab_spans) or re.match(r"\s+than\b", low[m.end():]):
            continue
        op = ">=" if m.group(2) in ("higher", "more", "older", "above", "greater", "over", "up") else "<="
        w = low[max(0, m.start() - 40): m.end() + 10]
        c = _condition_column(w, view, cols, num_cols)
        if c and not (len(m.group(1)) == 4 and m.group(1).startswith(("19", "20")) and c == m.group(1)):
            out.append({"column": c, "op": op, "value": float(m.group(1))})
    y = re.search(r"\b(?:in|during|of)\s+((?:19|20)\d{2})\b", body)
    if y and view.date_cols() and not any(w["op"].startswith("year") for w in out):
        dc = [c for c in cols if c in view.date_cols()] or view.date_cols()
        out.append({"column": dc[0], "op": "year==", "value": int(y.group(1))})
    return out


OP_WORDS = set("""total sum combined average mean highest lowest most least fewest largest smallest maximum minimum
greater less more fewer higher lower exceed difference absolute change rank order descending ascending share
proportion percentage percent ratio rate which who what how many much second third first youngest oldest earliest latest
compare between across all both respectively value number answer""".split())


GENERIC = set("""occupation industri industry worker person people relat servic sector group total all categor
item count amount figur data dataset table sheet record entri row column number valu inform report statist infer
calculat final consider show list give find tell determin base accord provid name integ decimal place round
digit format sign unit comma separat space order without only exactly two three both respect
summ individu non overlapp detail tabl contain absolut differenc averag chang
had has have nam cua khong co la cac nhung va trong cho duoc theo nay do mot hai ba bao nhieu nao""".split())

OP_CUES = {
    "compare": r"\b(greater|more|higher|larger|exceed|exceeds|exceeded|less|lower|smaller|fewer|below|above|than)\b",
    "diff": r"how (many|much) (more|less|fewer|higher|lower)|\bdifference\b|\bdiffer\b|\bchange\b|\bgap\b|\bincrease\b|\bdecrease\b",
    "sum": r"\bsumm?ing\b|\bsum\b|\btotal\b|\bcombined\b|\baltogether\b|\bin all\b|\bacross all\b",
    "mean": r"\baverage\b|\bmean\b",
    "count": r"\bhow many\b|\bnumber of\b|\bcount\b",
    "rank": r"\brank\b|\bdescending\b|\bascending\b|\bfrom (most|highest|largest|greatest|biggest|least|fewest|lowest|smallest) to\b",
    "share": r"\bshare\b|\bproportion\b|percentage of|what percent",
    "ratio": r"/|\bratio\b|\brate\b",
    "argmax": r"\b(" + SUP_MAX + r")\b", "argmin": r"\b(" + SUP_MIN + r")\b",
    "max": r"\b(" + SUP_MAX + r")\b", "min": r"\b(" + SUP_MIN + r")\b",
    "nth": r"\b(" + SUP_MAX + "|" + SUP_MIN + r")\b", "topk_agg": r"\b(" + SUP_MAX + "|" + SUP_MIN + r")\b",
    "values": r".", "lookup": r".",
}


OP_STEMS = None


def _op_stems():
    global OP_STEMS
    if OP_STEMS is None:
        from .textmatch import stem
        OP_STEMS = {stem(w) for w in OP_WORDS} | set(OP_WORDS)
    return OP_STEMS


def _view_vocab(view):
    voc = set()
    for c in view.columns + [view.label_name or ""]:
        voc.update(tokens(str(c)))
    for l in view.df["_row_path"].astype(str):
        voc.update(tokens(l))
    voc.update(tokens(view.title)); voc.update(tokens(view.meta))
    for t in view.tables:
        voc.update(tokens(" ".join(t.notes)))
    voc.update(tokens(" ".join(map(str, dict.fromkeys(view.df["_sheet"])))))
    return voc


def _part_ok(q, part, quoted):
    f, _ = q.match(part)
    if f == 1.0:
        return True
    pt = tokens(part)
    hit = [t for t in pt if q.positions(t)]
    miss = [t for t in pt if not q.positions(t) and t not in GENERIC]
    if hit and not miss:
        return True
    for qq in quoted:  # quoted phrase fully inside the label
        qt = tokens(qq)
        if qt and all(any(t == u or (len(t) >= 5 and (t.startswith(u) or u.startswith(t))) for u in pt) for t in qt):
            return True
    return False


def evidence(query, plan, info):
    """List of (slot, how) with how in {'exact','cue','convention','guess:<why>'}."""
    view, q = info["view"], info["q"]
    body, low = info["body"], info["body"].lower()
    quoted = re.findall(r"['\"“‘]([^'\"”’]{2,})['\"”’]", body)
    ev = []
    allcols = view.columns
    # --- table
    vs = sorted(info["vscores"], reverse=True)
    if len(vs) > 1 and not vs[0][2] and vs[1][0] >= vs[0][0] - 1.0 and not view.stacked:
        ev.append(("table", "guess:several similar tables"))
    else:
        ev.append(("table", "exact"))
    # --- columns
    cols = plan["columns"]
    num = view.numeric_cols()
    needs_col = plan["op"] in ("lookup", "values", "compare", "diff", "sum", "mean", "max", "min", "rank", "ratio", "share") or (
        plan["op"] in ("argmax", "argmin") and plan["args"].get("over") == "rows")
    if needs_col and not cols and len(num) > 1:
        single = False
        if len(plan["rows"]["paths"]) == 1:
            r_ = view.df[view.df["_row_path"] == plan["rows"]["paths"][0]]
            single = len(r_) > 0 and int(r_[num].iloc[0].notna().sum()) == 1
        if not single:
            ev.append(("columns", "guess:no column named"))
    def colscore(c):
        ps = str(c).split(" | ")
        return (sum(1 if _part_ok(q, p, quoted) else -1 for p in ps),
                sum(1 for p in ps for t in tokens(p) if q.positions(t)))

    def beats(o, c):
        (oa, ob), (ca, cb) = colscore(o), colscore(c)
        return oa > ca or (oa == ca and ob >= cb)
    # columns spanned by an explicit range in the question ('Jan-Apr')
    ranged = set()
    for mm in re.finditer(r"\b([A-Za-z]{3,9}|\d{4})\s*(?:-|–|to|through)\s*([A-Za-z]{3,9}|\d{4})\b", body):
        a, b = tokens(mm.group(1)), tokens(mm.group(2))
        ia = [i for i, c in enumerate(allcols) if a and tokens(c) == a]
        ib = [i for i, c in enumerate(allcols) if b and tokens(c) == b]
        if ia and ib and ia[0] < ib[0]:
            ranged |= set(allcols[ia[0]: ib[0] + 1])
    if needs_col and not cols and len(num) > 1 and len(paths_rows := plan["rows"]["paths"]) == 1:
        pass
    for c in cols:
        if c == "*":
            ev.append((c, "cue" if re.search(r"across all|\ball\b|combined|total|in all", low) else "guess:all columns"))
            continue
        if c in ranged:
            ev.append((c, "exact"))
            continue
        parts = str(c)[:-4].split(" | ") if str(c).endswith(" | *") else str(c).split(" | ")
        okp = [_part_ok(q, p, quoted) for p in parts]
        leaf_ok = okp[-1] or (C.PREFER_TOTAL_SUBCOLUMN and re.match(r"total\b", parts[-1], re.I) and len(parts) > 1 and okp[-2])
        if not leaf_ok:
            ev.append((c, "guess:column name not in question"))
            continue
        # parents: allowed partial when the leaf is unique among columns
        same_leaf = [o for o in allcols if str(o).split(" | ")[-1] == parts[-1] and o != c]
        if not all(okp[:-1]) and same_leaf:
            ev.append((c, "guess:ambiguous parent header"))
            continue
        # another unrelated column matching at least as well
        pre = " | ".join(parts[:-1])
        sc = colscore(c)
        rivals = [o for o in allcols if o not in cols and o != c and not (pre and str(o).startswith(pre + " | "))
                  and beats(o, c) and sc[0] > 0 and _part_ok(q, str(o).split(" | ")[-1], quoted)]
        if c in num:
            rivals = [o for o in rivals if o in num]
        rivals = [o for o in rivals if not (re.match(r"(grand )?total\b", str(o).split(" | ")[-1], re.I) and len(cols) > 1)]
        rivals = [o for o in rivals if not any(str(o).startswith(str(k)[:-4]) for k in cols if str(k).endswith(" | *"))]
        if rivals:
            ev.append((c, f"guess:similar column {rivals[0]!r}"))
        else:
            ev.append((c, "exact"))
    # --- rows
    df = view.df
    paths = plan["rows"]["paths"]
    names = plan["args"].get("names") or []
    for k, p in enumerate(paths):
        pp = p.split("!", 1)[1] if ("!" in p and view.stacked) else p
        parts = pp.split(" > ")
        leaf = strip_footnotes(parts[-1])
        f, _ = q.match(leaf)
        head = leaf.split("(")[0]
        ok = f == 1.0 or ("(" in leaf and q.match(head)[0] == 1.0) or _part_ok(q, leaf, quoted + names)
        if not ok:
            ev.append((pp, "guess:row label not in question"))
            continue
        same = set(df.loc[df["_label"] == leaf, "_row_path"]) - {pp}
        if same:
            anc_hit = any(q.match(a)[0] > 0 for a in parts[:-1])
            totalish = bool(parts[:-1]) and re.match(r"(total|all)\b", parts[0], re.I) is not None
            if not anc_hit and not totalish:
                ev.append((pp, "guess:same label in several groups"))
                continue
            if not anc_hit and totalish:
                ev.append((pp, "convention"))
                continue
        ev.append((pp, "exact"))
    if plan["op"] in ("lookup", "values", "compare", "diff", "ratio") and not paths and len(df) > 1:
        first = df["_label"].iloc[0]
        if re.match(r"(total|all|grand total)\b", str(first), re.I) or df["_is_subtotal"].iloc[0]:
            ev.append(("rows", "convention"))
        elif re.search(r"\btotal\b|\ball\b|across", low):
            ev.append(("rows", "cue"))
        else:
            ev.append(("rows", "guess:no row named"))
    if plan["op"] in ("sum", "mean", "max", "min", "count", "argmax", "argmin", "rank") and not paths and not plan["rows"]["where"] \
            and not plan["rows"]["range"] and not plan["rows"].get("scope") and (df["_is_subtotal"].sum() > 1):
        if not re.search(r"\ball\b|across|every|each|total|table|contain", low):
            ev.append(("rows", "guess:implicit row scope"))
    secs = [x for x in dict.fromkeys(df["_section"]) if x]
    if plan["op"] in ("sum", "mean", "max", "min", "count", "argmax", "argmin", "rank", "nth", "topk_agg") and len(secs) >= 2 \
            and not paths and not plan["rows"].get("scope") and not plan["rows"]["range"] \
            and not re.search(r"\ball\b|across|every|each|total|table", low):
        ev.append(("rows", "guess:several sections, none named"))
    if re.search(r"\b(rate|ratio|per cent|percentage|percent|share|proportion|per capita|average)\b", low) and \
            plan["op"] not in ("ratio", "share", "mean") and not any(
                re.search(r"\b(rate|ratio|percent|%|share|average|mean)", str(c), re.I) for c in cols):
        ev.append(("op", "guess:rate/ratio asked but no such column or op"))
    # --- op
    op = plan["op"]
    cue = OP_CUES.get(op, r".")
    if op == "compare":
        ev.append(("op", "cue" if re.search(cue, low) else "guess:yes/no without comparison"))
    elif op in ("lookup", "values"):
        ev.append(("op", "cue"))
    else:
        ev.append(("op", "cue" if re.search(cue, low) else f"guess:{op} without cue"))
    # --- numbers in question
    used = " ".join(map(str, cols + paths + [view.title, view.meta] + list(dict.fromkeys(df["_sheet"]))))
    used += " " + json_dump(plan)
    body_n = re.sub(r"\((?:e\.g\.|eg|for example|i\.e\.)[^)]*\)", " ", body, flags=re.I)
    for m in re.finditer(r"\d+(?:\.\d+)?", body_n):
        n = m.group()
        if not re.search(rf"(?<![\d.]){re.escape(n)}(?:\.0)?(?![\d])", used):
            ev.append((n, "guess:number in question not used"))
    # --- content words
    voc = _view_vocab(view)
    unexpl = []
    for t, pos in q.toks:
        if t.isdigit() or t in _op_stems() or t in GENERIC or len(t) <= 2:
            continue
        if any(t == v or (len(t) >= 5 and len(v) >= 5 and (t.startswith(v) or v.startswith(t))) for v in voc):
            continue
        unexpl.append(t)
    if len(unexpl) >= 3:
        ev.append(("words", f"guess:unexplained words {unexpl[:4]}"))
    elif unexpl:
        ev.append(("words", f"weak:{unexpl}"))
    return ev


def json_dump(plan):
    import json
    return json.dumps(plan, default=str)


def _confidence(query, plan, info):
    from .answer_type import compatible, expected_type, plan_type
    exp = expected_type(query, plan["format"])
    got = plan_type(plan, info["view"])
    if not compatible(exp, got):
        return 0.0, [f"type:{exp}!={got}"]
    ev = evidence(query, plan, info)
    guesses = [f"{s}:{h}" for s, h in ev if h.startswith("guess")]
    c = 1.0
    if guesses:
        c = min(c, C.GUESS_CAP)
    c *= 0.9 ** sum(1 for _, h in ev if h.startswith("weak"))
    return c, guesses + [f"{s}:{h}" for s, h in ev if h.startswith("weak")]


def _confidence_legacy(query, plan, info):
    """b8f1082 confidence (default when no LLM key): kept verbatim for byte-identical output."""
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
    content = [p for t, p in q.toks if t not in OP_WORDS and not t.isdigit() or t.isdigit()]
    cov = info.get("cols_cov") or set()
    if content:
        ratio = len([p for p in content if p in cov]) / len(content)
        if ratio < 0.2:
            c *= 0.6; reasons.append(f"coverage:{ratio:.2f}")
    return c, reasons
