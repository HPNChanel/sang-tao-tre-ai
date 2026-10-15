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
# Header fillers. 'total' is not one: it is often the column the question names.
_COL_FILLER = {"relat", "industri", "industry", "sector"}


def _col_part_match(q: Query, part: str, exclude=frozenset()):
    """Like Query.match, but filler words do not dilute a specific hit.

    'agriculture' therefore matches 'Agriculture and related industries'."""
    pt = tokens(part)
    if not pt:
        return 0.0, set()
    specific = [t for t in pt if t not in _COL_FILLER]
    use = specific or pt
    pos, hit = set(), 0
    for t in use:
        ps = q.positions(t, exclude)
        if ps:
            hit += 1
            pos |= ps
    return hit / len(use), pos


def _quoted_column_operands(view, text):
    """Resolve an explicitly quoted column list before greedy token coverage.

    Tokens shared by two labels belong to both operands. A generic operation
    word such as 'total' outside the list is not an extra operand.
    """
    phrases = re.findall(r"(?<!\w)(['\"`])(.+?)\1", text)
    if len(phrases) < 2:
        return None
    out = []
    for _, phrase in phrases:
        target = tokens(phrase, keep_stop=True)
        matches = [c for c in view.numeric_cols()
                   if target in (tokens(str(c), keep_stop=True),
                                 tokens(str(c).split(" | ")[-1], keep_stop=True))]
        if len(matches) != 1:
            return None
        out.append(matches[0])
    return out if len(set(out)) == len(out) else None


def match_columns(view: View, q: Query, exclude=frozenset()):
    """Greedy cover of query tokens by column keys (prefix of 'A | B | C' parts)."""
    cands = {}
    allcols = view.columns + ([view.label_name] if view.label_name else [])
    for ci, col in enumerate(allcols):
        parts = str(col).split(" | ")
        fr = [_col_part_match(q, p, exclude) for p in parts]
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


def _prefer_universe_age(df, q: Query, kept):
    """A bare 'Men' row under a section the question never names loses to 'Men, 16 years and over'.

    The longer label is the group total; the number in it is the table's age universe, not a
    different group. A parent the question does name (for example White) keeps the bare row."""
    universe = {}
    for i in range(len(df)):
        path = str(df["_row_path"].iat[i])
        leaf = strip_footnotes(path.split(" > ")[-1]).strip()
        m = re.fullmatch(r"(.+), \d+ years and over", leaf, re.I)
        if m:
            universe.setdefault(m.group(1).lower(), path)
    out = []
    for path, pos, i in kept:
        leaf = strip_footnotes(path.split(" > ")[-1]).strip()
        anc = tokens(" ".join(path.split(" > ")[:-1]))
        uni = universe.get(leaf.lower()) if len(tokens(leaf)) == 1 else None
        if uni and uni != path and not any(q.positions(t) for t in anc):
            out.append((uni, pos, i))
        else:
            out.append((path, pos, i))
    return out


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
    kept = _prefer_universe_age(df, q, kept)
    return [(p, pos) for p, pos, _ in kept], covered


# ---------------------------------------------------------------- planning
def strip_instruction(query, spec):
    """Drop answer-format text before operand, year, and N extraction.

    Numbers inside 'Answer with only ...', a parenthetical '(e.g. ...)',
    'for example ...', or the Vietnamese 'ví dụ' / 'chỉ trả lời' are examples
    of how to write the answer, not operands.
    """
    q = query
    q = re.sub(
        r"\((?:e\.g\.|eg\.?|for example|i\.e\.|ví\s*dụ|vi\s*du)[^)]*\)",
        " ", q, flags=re.I)
    # 'e.g.' ends on a dot, so it cannot demand a trailing word boundary.
    q = re.sub(
        r"(?:\bfor example\b|\be\.g\.|\beg\.?|\bví\s*dụ\b|\bvi\s*du\b)[^.;?!]*",
        " ", q, flags=re.I)
    if spec.instruction:
        q = q.replace(spec.instruction, " ")
    q = re.sub(
        r"\b(?:Provide|Answer|Respond|Chỉ\s+trả\s+lời|Chi\s+tra\s+loi)\b[^.?!]*[.?!]?",
        " ", q, flags=re.I)
    return q


def plan_question(query: str, tables) -> dict:
    return plan_with_confidence(query, tables)[0]


def plan_with_confidence(query: str, tables, strict=None):
    """-> (plan, confidence in [0,1], reasons). Vietnamese questions are keyword-translated first.
    strict=None -> conventions.strict_active() (off unless opted in); strict=True -> evidence accounting (42d00ea)."""
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
    original = query
    reasons = []
    vi_factor = 1.0
    hits = hits_base = 0
    if is_vietnamese(query):
        query, hits = translate(query, new_ops=True)
        _, hits_base = translate(original, new_ops=False)
        vi_factor = 0.9 if hits >= 2 else 0.6
        reasons.append(f"vi:{hits}")
    plan, info = _plan_core(query, tables, strict)
    # A new operation phrase only keeps its confidence when column, row, operand and N
    # each resolve once. Otherwise abstain, so a former blank stays blank.
    if _new_op_blocked(plan, info, original):
        return plan, 0.0, reasons + ["op:ambiguous"]
    if is_vietnamese(original) and not _new_op_committed(plan, info, original):
        vi_factor = 0.9 if hits_base >= 2 else 0.6
    if info.get("abstain"):
        return plan, 0.0, reasons + ["op:abstain"]
    blocked = _blank_reasons(original, query, plan, info)
    if blocked:
        return plan, 0.0, reasons + blocked
    conf, r2 = (_confidence if strict else _confidence_legacy)(query, plan, info)
    return plan, conf * vi_factor, reasons + r2


def _blank_reasons(original, translated, plan, info):
    """Confidence 0 when a remaining generic guard says this plan would be confidently wrong.

    Completeness is decided once, here, before either guard runs. A ranking that
    already has its measure column and its direction is a no-op, as is a single
    superlative (argmax/argmin, or nth place 1).
    """
    from . import blank_guards as G
    if G.ranking_ready(plan) or G.single_superlative(plan):
        return []
    texts = [original or "", translated or ""]
    reasons = []
    if G.shape_mismatch(plan, texts):
        reasons.append("blank:shape")
    if _nth_measure_missing(plan, original, translated):
        reasons.append("blank:nth-column")
    return reasons


def _nth_measure_missing(plan, original, translated):
    """Nth from place 2 onward, with no measure column. A set column is not re-checked."""
    from . import blank_guards as G
    if plan.get("op") != "nth":
        return False
    if not G.nth_signal(original, translated):
        return False
    args = plan.get("args") or {}
    try:
        n = int(args.get("n") or 0)
    except (TypeError, ValueError):
        return False
    if n < 2:
        return False
    return not args.get("by")


def _named_rank_scope(view, q, matched_rows):
    """The one group a row-rank must be limited to, or None, or 'ambiguous'.

    A section or a parent row the question names fully is that group. Two
    unrelated hits is a guess, so the caller blanks instead of ranking
    every row. No hit means the question did not name a subset.
    """
    df = view.df
    secs = [x for x in dict.fromkeys(df["_section"]) if x]
    sec_hit = [x for x in secs if q.match(x)[0] == 1.0]
    if len(sec_hit) > 1:
        return "ambiguous"
    paths = [str(p) for p in df["_row_path"]]
    parents = []
    for path, _pos in matched_rows:
        leaf = strip_footnotes(str(path).split(" > ")[-1])
        if q.match(leaf)[0] != 1.0:
            continue
        if any(p.startswith(path + " > ") for p in paths) and path not in parents:
            parents.append(path)
    if len(parents) > 1:
        parents.sort(key=len)
        tight = parents[-1]
        if not all(tight == p or tight.startswith(p + " > ") for p in parents):
            return "ambiguous"
        parents = [tight]
    if len(parents) == 1:
        parent = parents[0]
        if sec_hit and not (parent == sec_hit[0] or parent.startswith(sec_hit[0] + " > ")):
            return "ambiguous"
        return parent
    if len(sec_hit) == 1:
        return sec_hit[0]
    return None


def _plan_core(query: str, tables, strict=False):
    spec = parse_instruction(query)
    body = strip_instruction(query, spec)
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
    row_paths = [p for p, _ in rows]
    quoted_operands = _quoted_column_operands(view, body)
    if quoted_operands:
        cols = quoted_operands
    # 'in all ... and in <subset>' constrains one population. It does not add
    # the encompassing column to its subset. Explicit arithmetic/list requests
    # retain both operands, and multiple specific columns remain unresolved.
    intersected_column = False
    if len(cols) > 1 and re.search(r"\band in\b", low) and not re.search(
            r"\b(sum|total|combined|altogether|difference|compare|ratio|both|respectively)\b", low) \
            and spec.kind != "list":
        broad = [c for c in cols if re.match(r"all\b", str(c).split(" | ")[-1], re.I)]
        specific = [c for c in cols if c not in broad]
        if broad and len(specific) == 1 and all(_par(c) and _par(c) == _par(specific[0]) for c in broad):
            cols = specific
            intersected_column = True

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
    named_span = any(re.search(rf"\b{rm.group(1)}\b.*\b{rm.group(2)}\b", str(p).split(" > ")[-1]) for p in list(row_paths) + list(cols)) if rm else False
    if rm and not (len(rm.group(1)) == 4 and len(rm.group(2)) == 4) and not named_span:
        lo, hi = float(rm.group(1)), float(rm.group(2))
        if any(_label_range(l) for l in view.df["_label"]):
            plan["rows"]["range"] = [lo, hi]
            plan["rows"]["leaf_only"] = True
            plan["rows"]["paths"] = row_paths = [p for p in row_paths if (rg := _label_range(p.split(" > ")[-1])) and lo <= rg[0] and rg[1] <= hi]
    if re.search(r"non-overlapping|most detailed|individual", low):
        plan["rows"]["leaf_only"] = True

    # ---- operation
    # op_low folds Vietnamese operation phrases (with or without dấu) onto the English cues.
    op_low = _canon_op(low)
    sup_max, sup_min = re.search(rf"\b({SUP_MAX})\b", op_low), re.search(rf"\b({SUP_MIN})\b", op_low)
    which = re.search(r"\b(which|who|identify|name the|in what|list all|name all)\b", op_low)
    nth = _nth_match(op_low)
    topk = re.search(r"\b(?:among|of) the (\w+) (" + SUP_MAX + "|" + SUP_MIN + r")\b", op_low)
    # 'among the five oldest' is a subset aggregate; every other top/bottom-k is a ranked list
    topn = None if topk else _top_n(op_low, view, spec, num_cols, row_paths)
    if topn and topn["by"] and topn["by"] != "age" and topn["over"] == "rows":
        cols = [topn["by"]] + [c for c in cols if c != topn["by"]]
        plan["columns"] = cols
    if spec.kind == "yesno":
        plan["op"] = "compare"
        args["cmp"] = "<" if re.search(r"\b(less|lower|smaller|fewer|below|under)\b", low) else ">"
    elif topn and _commit_topn(query, spec, topn, view, body):
        plan["op"] = "rank"
        args["k"] = topn["k"]
        args["order"] = _topn_order(query, topn["word"])
        args["by"] = "age" if topn["word"] in ("oldest", "youngest") else topn["by"]
        if topn["over"] == "columns":
            args["over"] = "columns"
        else:
            plan["rows"]["paths"] = []
        if topn["ret"]:
            args["return"] = topn["ret"]
        elif topn["values"] and topn["by"] and topn["by"] != "age":
            args["return"] = topn["by"]
        fmt = plan["format"]
        # a lone integer/yes-no instruction is not a list; type check then abstains
        if fmt.get("kind") not in ("list", "int", "decimal", "number", "yesno"):
            fmt["kind"] = "list"
            fmt["sep"] = fmt.get("sep") or ", "
        if topn["values"]:
            fmt["item_kind"] = "number"
        elif not fmt.get("item_kind"):
            fmt["item_kind"] = "name"
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
        g, word = nth
        args["n"] = ORD.get(g) or (int(re.match(r"\d+", g).group()) if g else 1)
        _order_args(args, word, view, cols + label_cols)
        _prefer_measure_return(args, plan, op_low, view)
        plan["op"] = "nth"
    elif topk and (topk.group(1) in NUMW or topk.group(1).isdigit()):
        args["k"] = NUMW.get(topk.group(1)) or int(topk.group(1))
        _order_args(args, topk.group(2), view, cols)
        # 'among the k oldest' is the birth column; the later aggregate is a different column
        _order_birth_subset(args, topk.group(2), view)
        rest = op_low[topk.end():]
        args["agg"] = "max" if re.search(rf"\b({SUP_MAX})\b", rest) else "min" if re.search(rf"\b({SUP_MIN})\b", rest) else "mean" if re.search(r"average|mean", rest) else "sum"
        plan["op"] = "topk_agg"
    elif (sup_max or sup_min) and which and spec.kind == "name":
        plan["op"] = "argmax" if sup_max and not (sup_min and sup_min.start() < sup_max.start()) else "argmin"
        args["abs"] = bool(re.search(r"absolute", low))
        _choose_over(plan, view, rows, cols, low)
    elif re.search(_DIFF_SRC, op_low):
        plan["op"] = "diff"
        args["abs"] = bool(re.search(r"absolute|\bdifference between\b", low)) and not re.search(r"\bsigned\b", query.lower())
        args["signed_order"] = "second_minus_first" if re.search(r"\bchange\b|\bfrom\b.*\bto\b|increase|decrease", low) else "first_minus_second"
    elif re.search(r"\baverage\b|\bmean\b", low) and not any(re.search(r"\baverage\b|\bmean\b", c, re.I) for c in cols):
        plan["op"] = "mean"
    elif (sup_max or sup_min) and (which or (spec.kind == "name" and not re.search(r"\bwhat (was|is) the (highest|lowest)", low))):
        plan["op"] = "argmax" if sup_max and not (sup_min and sup_min.start() < sup_max.start()) else "argmin"
        args["abs"] = bool(re.search(r"absolute", low))
        _choose_over(plan, view, rows, cols, low)
    elif _value_superlative(sup_max, sup_min, plan, low):
        # 'score highest what is the value' is max of that column, including over a filtered subset
        earlier_min = bool(sup_min) and (not sup_max or sup_min.start() < sup_max.start())
        plan["op"] = "min" if earlier_min else "max"
        if not cols and view.date_cols():
            plan["columns"] = view.date_cols()[:1]
    elif re.search(r"\bsumm?ing\b|\bsum\b|\btotal\b|\bcombined\b|\baltogether\b|\bin all\b|\bacross all\b", low) and not re.search(r"^\s*how many\b(?!.*\b(total|sum|combined)\b)", low):
        plan["op"] = "sum"
    elif re.search(r"\bhow many\b", op_low):
        conds = _drop_label_filters(_conditions(body, view, cols, num_cols), row_paths, cols)
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
    # Output instructions can name a projection absent from the question body.
    # Resolve it separately so a context/date column does not become the answer.
    if plan["op"] in ("nth", "rank") and spec.instruction and args.get("over", "rows") == "rows":
        projected, _ = match_columns(view, Query(spec.instruction))
        projected = [c for c, _ in projected if c in view.df.columns]
        if len(projected) == 1:
            args["return"] = projected[0]
    if intersected_column and plan["op"] == "sum":
        # The 'in all' phrase was column context, not an aggregation instruction.
        plan["op"] = "lookup"
    if strict and plan["op"] in ("max", "min", "mean", "sum", "argmax", "argmin", "count", "rank") and not plan["rows"]["paths"] \
            and not plan["rows"].get("scope"):
        secs = [x for x in dict.fromkeys(view.df["_section"]) if x]
        hit = [x for x in secs if q.match(x)[0] == 1.0]
        if len(hit) == 1:
            plan["rows"]["scope"] = hit[0]
    if plan["op"] in ("max", "min", "mean", "sum", "argmax", "argmin", "topk_agg", "nth", "rank") and not plan["rows"]["where"]:
        conds = _conditions(body, view, plan["columns"], num_cols)
        if conds:
            plan["rows"]["where"] = conds
            cc = {w["column"] for w in conds}
            rest = [c for c in plan["columns"] if c not in cc]
            plan["columns"] = rest or plan["columns"]
            plan["rows"]["paths"] = [p for p in plan["rows"]["paths"]] if plan["op"] not in ("max", "min", "mean", "sum") else plan["rows"]["paths"]
    if plan["rows"]["where"]:
        # 'born in 1990' filters the birth column (date year or numeric year), not whichever date is listed first
        plan["rows"]["where"] = _aim_born_year(plan["rows"]["where"], view, low)
        # a number that only names the chosen row, scope, or sort column is not also a threshold
        band = " ".join([str(plan["rows"].get("scope") or ""), str(args.get("by") or "")]
                        + list(plan["rows"]["paths"]) + list(plan["columns"]))
        plan["rows"]["where"] = _drop_label_filters(plan["rows"]["where"], [], [band])
    ambiguous_rank_scope = False
    if plan["op"] == "rank":
        # '55 years and over' names a band already chosen as the sort column or the row, not a threshold.
        band = " ".join([str(args.get("by") or "")] + list(plan["rows"]["paths"]))
        plan["rows"]["where"] = [w for w in plan["rows"]["where"] if not _number_names_band(w.get("value"), band)]
        # 'Answer with the N names' sets format.count. Without k the rank slice keeps every row.
        if not args.get("k") and plan["format"].get("count"):
            args["k"] = plan["format"]["count"]
        # Rank the named group first, then cut to k. Clearing paths above drops that group.
        if args.get("over") != "columns" and not args.get("names") and not plan["rows"]["paths"] \
                and not plan["rows"].get("scope"):
            picked = _named_rank_scope(view, q, rows)
            if picked == "ambiguous":
                ambiguous_rank_scope = True
            elif picked:
                plan["rows"]["scope"] = picked
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
    _order_year_change_columns(plan, body)
    if plan["op"] in ("lookup", "values") and (len(plan["rows"]["paths"]) * max(1, len(plan["columns"])) > 1 or spec.kind == "list"):
        plan["op"] = "values"
    if plan["op"] == "values" and re.search(r"\b(pair|respectively|both)\b", op_low):
        fmt = plan["format"]
        if fmt.get("kind") not in ("list",):
            fmt["kind"] = "list"
            fmt["sep"] = fmt.get("sep") or ", "
        if not fmt.get("item_kind"):
            fmt["item_kind"] = "number"
    if plan["op"] in ("sum", "diff", "lookup", "values", "compare") and not plan["columns"] and re.search(r"across all|all \w+ combined|in all", low):
        plan["columns"] = ["*"]
    # An explicit horizontal mean uses individual cells, not a wildcard (which
    # the executor sums per row) or its implicit first-column fallback.
    if plan["op"] == "mean" and not plan["columns"] and len(plan["rows"]["paths"]) == 1 \
            and re.search(r"\bacross all\b", low):
        plan["columns"] = list(num_cols)
    abstain = _unhandled_op(op_low, plan)
    # A refused top-N (scalar number, or a count that is also a stored value) keeps
    # the older max/lookup. That leftover phrase is not an unparsed operation.
    if abstain and topn and plan["op"] in ("lookup", "values", "sum", "count") \
            and not _commit_topn(query, spec, topn, view, body):
        abstain = False
    fresh = _fresh_op(plan, low, op_low, topn, _topn_lists(query, spec))
    return plan, {"view": view, "q": q, "rows": rows, "cols_cov": best_cov, "body": body,
                  "ambiguous_rank_scope": ambiguous_rank_scope,
                  "views": views, "vscores": vscores, "vi": vi, "spec": spec, "abstain": abstain,
                  "fresh_op": fresh}


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
    ret = [c for c in cols if c != args.get("by") and not (args.get("by") == "age" and _birth_name(c))]
    args["return"] = ret[0] if ret else None


def _order_birth_subset(args, word, view):
    """Oldest/youngest as a multi-row subset is the birth column itself.

    A later max/min/sum/mean then reduces a different column over every selected row.
    A single nth keeps by='age' (age at the row's other date, unless that date is the answer).
    """
    if word not in ("oldest", "youngest"):
        return
    col = _birth_column(view)
    if not col:
        return
    args["by"] = col
    args["order"] = "asc" if word == "oldest" else "desc"
    if args.get("return") == col:
        args["return"] = None


def _value_superlative(sup_max, sup_min, plan, low):
    """Superlative asking for a number or a date, not for which row.

    Covers both 'what is the highest score' and 'score highest what is the value'."""
    if not (sup_max or sup_min) or re.search(r"\bhow many\b", low):
        return False
    if plan["format"]["kind"] in ("date", "int", "decimal", "number"):
        return True
    if re.search(r"\bwhat (?:was|is) the (?:highest|lowest|maximum|minimum|largest|smallest|earliest|latest)\b", low):
        return True
    return bool(re.search(r"\bwhat (?:was|is) the value\b", low))


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
    _append_year_filter(out, body.lower(), view, cols)
    return out


def _birth_name(name) -> bool:
    from .vi import fold
    return bool(re.search(r"birth|born|dob|nam sinh|ngay sinh", fold(str(name)), re.I))


def _birth_column(view):
    """Date of birth if present, otherwise a numeric birth-year column."""
    dates = [c for c in view.date_cols() if _birth_name(c)]
    if dates:
        return dates[0]
    nums = [c for c in view.numeric_cols() if _birth_name(c)]
    return nums[0] if nums else None


def _is_year_number(value) -> bool:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return False
    return 1900 <= n <= 2100


def _append_year_filter(out, low, view, cols):
    """'born in 1982' filters the birth column even when it is a plain number.

    A bare 'in 1982' still uses a date column, so a period column is unchanged."""
    born = re.search(r"\bborn\s+in\s+((?:19|20)\d{2})\b", low)
    if born:
        year = int(born.group(1))
        col = _birth_column(view)
        if col is not None:
            op = "year==" if col in view.date_cols() else "=="
            out.append({"column": col, "op": op, "value": year})
            return
        if view.date_cols():
            dc = [c for c in cols if c in view.date_cols()] or view.date_cols()
            out.append({"column": dc[0], "op": "year==", "value": year})
        return
    y = re.search(r"\b(?:in|during|of)\s+((?:19|20)\d{2})\b", low)
    if y and view.date_cols():
        dc = [c for c in cols if c in view.date_cols()] or view.date_cols()
        out.append({"column": dc[0], "op": "year==", "value": int(y.group(1))})


def _aim_born_year(where, view, low):
    """Point a year filter at the birth column when the question says born."""
    if not re.search(r"\bborn\b", low):
        return where
    col = _birth_column(view)
    if col is None:
        return where
    op = "year==" if col in view.date_cols() else "=="
    aimed = []
    for w in where:
        if w.get("op") in ("year==", "==") and _is_year_number(w.get("value")):
            aimed.append({"column": col, "op": op, "value": int(float(w["value"]))})
        else:
            aimed.append(w)
    return aimed


def _number_names_band(value, text):
    """True when a comparison value is the number inside an age-band label, not a cutoff."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return False
    return bool(re.search(rf"\b{n}\b", str(text)))


def _topn_lists(query, spec):
    """A top/bottom-k question that wants the k items, not one aggregate of them."""
    if spec.kind in ("int", "decimal", "number", "yesno"):
        return False
    if spec.kind == "list":
        return True
    q = query.lower()
    return bool(re.search(r"\b(list|who|which|name the|name all)\b", q) or "separated by" in q)


def _topn_order(query, word):
    q = query.lower()
    if re.search(r"\bascending\b|lowest first|smallest first|fewest first|earliest first", q):
        return "asc"
    if re.search(r"\bdescending\b|highest first|largest first|most first|latest first", q):
        return "desc"
    if word == "oldest":
        return "desc"
    if word == "youngest":
        return "asc"
    return "desc" if word and re.fullmatch(SUP_MAX, word) else "asc"


def _canon_op(low: str) -> str:
    """English operation cues, plus folded Vietnamese (dấu or not) that translation left behind."""
    from .vi import _CARD, _SUP_VI, _ord_en, fold
    s = fold(low).lower()

    def sup_ord(m):
        return _ord_en(m.group(2)) + " " + _SUP_VI[m.group(1)]

    s = re.sub(r"\b(cao|thap|lon|nho|nhieu|it)\s+(?:nhat\s+)?thu\s+(nhat|hai|nhi|ba|tu|nam|sau|bay|tam|chin|muoi|\d+)\b", sup_ord, s)
    s = re.sub(r"\bhang\s+(nhat|nhi|ba|tu|nam|sau|bay|tam|chin|muoi)\b", lambda m: _ord_en(m.group(1)) + " highest", s)
    s = re.sub(r"\b(?:dung|xep)\s+thu\s+(nhat|hai|nhi|ba|tu|nam|sau|bay|tam|chin|muoi|\d+)\b", lambda m: "ranks " + _ord_en(m.group(1)), s)
    s = re.sub(r"\bhon\b(.{0,50}?)\bbao nhieu\b", r" how many more \1 ", s)
    s = re.sub(r"\bchenh nhau\b|\bkhac nhau\b|\bkhac biet\b|\bhieu giua\b|\bhieu cua\b", " difference between ", s)
    s = re.sub(r"\btru\b", " minus ", s)
    s = re.sub(r"\blan luot\b", " respectively ", s)
    s = re.sub(r"\bca hai\b", " both ", s)
    s = re.sub(r"\b(top|bottom)\s+(mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\b", lambda m: m.group(1) + " " + _CARD[m.group(2)], s)
    s = re.sub(r"\b(mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\s+((?:cao|thap|lon|nho|nhieu|it)\s+nhat)\b",
               lambda m: _CARD[m.group(1)] + " " + m.group(2), s)
    ord_en = "|".join(sorted(ORD, key=len, reverse=True)) + r"|\d+(?:st|nd|rd|th)"
    s = re.sub(rf"\b(cao|thap|lon|nho|nhieu|it|{SUP_MAX}|{SUP_MIN})\s+({ord_en})\b",
               lambda m: _ord_en(m.group(2)) + " " + _SUP_VI.get(m.group(1), m.group(1)), s)
    s = re.sub(r"\bcao nhat\b|\blon nhat\b|\bnhieu nhat\b", " highest ", s)
    s = re.sub(r"\bthap nhat\b|\bit nhat\b|\bnho nhat\b", " lowest ", s)
    return re.sub(r"\s+", " ", s).strip()


def _nth_match(low):
    """(ordinal word, superlative word) or None. Either order, plus 'ranks second'."""
    ord_alt = "|".join(sorted(ORD, key=len, reverse=True)) + r"|\d+(?:st|nd|rd|th)"
    sup_alt = SUP_MAX + "|" + SUP_MIN
    m = re.search(rf"\b({ord_alt})[- ]({sup_alt})\b", low)
    if m:
        return m.group(1), m.group(2)
    m = re.search(rf"\branks?\s+({ord_alt})\b", low)
    if m:
        sup_min = re.search(rf"\b({SUP_MIN})\b", low)
        sup_max = re.search(rf"\b({SUP_MAX})\b", low)
        word = "lowest" if sup_min and (not sup_max or sup_min.start() < sup_max.start()) else "highest"
        return m.group(1), word
    m = re.search(rf"\b({ord_alt})\s+(scores?|values?|numbers?|amounts?)\b", low)
    if m and not re.search(rf"\b({sup_alt})\b", low):
        return m.group(1), "highest"
    m = re.search(r"\bthe ()(youngest|oldest)\b(?! \w+ (?:people|persons))", low)
    if m:
        return "", m.group(2)
    return None


def _prefer_measure_return(args, plan, low, view):
    """'the second highest score' is the number. 'which person' stays the label.

    A sort key that is not a real column (an age ordering, for example) cannot be
    the returned measure. Any column that is the sort key can, including one whose
    name happens to be a sort sentinel.
    """
    by = args.get("by")
    if not by or by not in view.df.columns:
        return
    asks_label = re.search(r"\b(who|whom|which|whose|name the|person|people|names)\b", low)
    kind = plan["format"]["kind"]
    if kind in ("int", "decimal", "number", "date") and not asks_label:
        args["return"] = by
    elif kind == "text" and not asks_label and re.search(r"\b(scores?|values?|numbers?|amounts?)\b", low):
        args["return"] = by


def _label_phrases(view):
    seen, out = set(), []
    cols = list(view.columns)
    labels = list(view.df["_label"].astype(str)) if "_label" in view.df else []
    for lab in cols + labels:
        key = str(lab)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _covered_by_label(low, start, end, view):
    """True when this number sits inside a row or column label the question quotes."""
    from .vi import fold
    text = fold(low).lower()
    if text != low:
        return False
    for lab in _label_phrases(view):
        parts = re.findall(r"[a-z]+|\d+(?:\.\d+)?", fold(str(lab)).lower())
        if len(parts) < 2 or not any(p[:1].isdigit() for p in parts):
            continue
        pat = r"\b" + r"\W+".join(re.escape(p) for p in parts) + r"\b"
        for m in re.finditer(pat, text):
            if m.start() <= start and m.end() >= end:
                return True
    return False


def _k_marked(low, start, end, raw, kind, view):
    """A number is k only when the wording counts items, not when it names a label."""
    if _covered_by_label(low, start, end, view):
        return False
    if kind == "edge":
        return True
    after = low[end:]
    if re.match(r"\s+(?:years?|tuoi|ages?|aged|to|-|–|or|and)\b", after):
        return False
    before = low[:start]
    if kind == "rev" or re.search(r"\b(?:top|bottom|which|what|list|the)\s+$", before):
        return True
    if not str(raw).isdigit():
        return True
    sup = SUP_MAX + "|" + SUP_MIN
    unit = r"(?:scores?|values?|numbers?|amounts?|people|persons?|names?|items?|groups?|industr\w*)"
    if re.match(rf"\s+(?:{sup})\b", after) or re.match(rf"\s+{unit}\s+(?:{sup})\b", after):
        return True
    return False


def _drop_label_filters(where, paths, cols):
    """Drop a threshold whose number is already inside a chosen row or column label."""
    blob = " ".join(str(x) for x in list(paths) + list(cols))
    kept = []
    for w in where:
        if w.get("op") == "year==":
            kept.append(w)
            continue
        if _number_names_band(w.get("value"), blob):
            continue
        kept.append(w)
    return kept


_DIFF_LEGACY = (
    r"how (?:many|much) (?:more|less|fewer|higher|lower)"
    r"|\bdifference\b|\bdiffer\b|\bchange\b|\bgap\b|\bincrease\b|\bdecrease\b"
)
_DIFF_SRC = (
    r"how (?:many|much) (?:more|less|fewer|higher|lower|greater|bigger|smaller)\b"
    r"|how (?:many|much) (?:is|was|were|are|did|does|do)\b.{0,40}?\b(?:more|higher|greater|bigger|less|lower|smaller|fewer)\b"
    r"|by how much"
    r"|\bdifference\b|\bdiffer\b|\bchange\b|\bgap\b|\bincrease\b|\bdecrease\b"
    r"|\bexceed(?:s|ed)?\b|\bminus\b|\bsubtract(?:ed|ing)?\b"
    r"|\bchenh(?: lech| nhau)?\b|\bhieu so\b|\bkhac nhau\b|\bkhac biet\b"
)
# Phrases this PR added. A hit is not enough: the column, row, operand and N must each be unique.
_NEW_OP_CUE_RE = re.compile(
    r"\b(cao|thap|lon|nho|nhieu|it)\s+(?:nhat\s+)?thu\s+(?:nhat|hai|nhi|ba|tu|nam|sau|bay|tam|chin|muoi|\d+)\b"
    r"|\bhang\s+(?:nhat|nhi|ba|tu|nam|sau|bay|tam|chin|muoi)\b"
    r"|\b(?:dung|xep)\s+thu\s+(?:nhat|hai|nhi|ba|tu|nam|sau|bay|tam|chin|muoi|\d+)\b"
    r"|\bhon\b.{0,50}?\bbao nhieu\b"
    r"|\bchenh nhau\b|\bkhac nhau\b|\bkhac biet\b|\bhieu giua\b|\bhieu cua\b"
    r"|\btru\b|\blan luot\b|\bca hai\b"
    r"|\bcap\s+(?:diem|gia tri|so lieu|ket qua)\b"
    r"|\b(?:top|bottom)\s+(?:mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\b"
    r"|\b(?:mot|hai|ba|bon|nam|sau|bay|tam|chin|muoi)\s+(?:cao|thap|lon|nho|nhieu|it)\s+nhat\b"
    r"|\branks?\s+(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\b"
    r"|by how much|\bexceed(?:s|ed)?\b|\bminus\b|\bsubtract(?:ed|ing)?\b"
    r"|how (?:many|much) (?:greater|bigger|smaller)\b"
    r"|\b(?:highest|lowest|largest|smallest|most|least)\s+(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\b"
    r"|\brespectively\b|\bpair of\b"
)


def _unhandled_op(op_low, plan):
    """Lookup/sum/count chosen while a more specific operation is still sitting in the wording."""
    op = plan["op"]
    if op not in ("lookup", "values", "sum", "count"):
        return False
    ord_alt = "|".join(sorted(ORD, key=len, reverse=True)) + r"|\d+(?:st|nd|rd|th)"
    sup_alt = SUP_MAX + "|" + SUP_MIN
    if re.search(rf"\b({ord_alt})[- ]({sup_alt})\b", op_low) or re.search(rf"\b({sup_alt})[- ]({ord_alt})\b", op_low):
        return True
    if re.search(rf"\branks?\s+({ord_alt})\b", op_low):
        return True
    if re.search(r"\bby how much\b|\bminus\b|\bexceed(?:s|ed)?\b|\bdifference between\b|\bgap between\b", op_low):
        return True
    nw = "|".join(sorted(NUMW, key=len, reverse=True))
    if re.search(rf"\b(?:top|bottom)\s+(?:\d+|{nw})\b", op_low):
        return True
    if re.search(rf"\b(?:\d+|{nw})\s+(?:{sup_alt})\b", op_low):
        return True
    # 'highest three', but not the threshold 'at least 60' / 'at most 3'
    if re.search(rf"(?<!at )(?:{sup_alt})\s+(?:\d+|{nw})\b", op_low):
        return True
    cells = len(plan["rows"]["paths"]) * max(1, len(plan["columns"]))
    if re.search(r"\b(pair|respectively)\b", op_low) and cells < 2:
        return True
    return False


def _legacy_nth(low):
    m = re.search(r"\b(" + "|".join(ORD) + r"|\d+(?:st|nd|rd|th))[- ](" + SUP_MAX + "|" + SUP_MIN + r")\b", low)
    if m:
        return True
    return bool(re.search(r"\bthe ()(youngest|oldest)\b(?! \w+ (?:people|persons))", low))


def _fresh_op(plan, low, op_low, topn, lists):
    """True when this plan used an operation path that main did not have."""
    op = plan["op"]
    if op == "rank" and topn and (not lists or topn.get("via") == "rev"):
        return True
    if op == "nth" and not _legacy_nth(low):
        return True
    if op == "diff" and not re.search(_DIFF_LEGACY, low):
        return True
    if op == "values" and re.search(r"\b(pair|respectively|both)\b", op_low):
        return True
    return False


def _fold_q(text):
    from .vi import fold
    return fold(text).lower()


def _new_op_keyword_hit(original):
    return bool(_NEW_OP_CUE_RE.search(_fold_q(original)))


def _schema_tokens(view):
    labs = list(view.columns)
    if "_label" in view.df:
        labs.extend(str(x) for x in view.df["_label"])
    out = set()
    for lab in labs:
        out.update(re.findall(r"[a-z0-9]+", _fold_q(lab)))
    return out


# Only the operation words themselves. Text that sits between 'hơn' and 'bao nhiêu'
# names an operand, not the operation, and must not count as a header collision.
_CUE_WORDS = {
    "cao", "thap", "lon", "nho", "nhieu", "it", "thu", "hang", "dung", "xep",
    "hon", "chenh", "khac", "biet", "hieu", "tru", "luot", "cap", "nhat", "nhi",
    "top", "bottom", "rank", "ranks", "exceed", "exceeds", "exceeded", "minus",
    "subtract", "subtracted", "subtracting", "respectively", "highest", "lowest",
    "largest", "smallest", "most", "least", "pair", "greater", "bigger", "smaller",
}


def _cue_tokens(original):
    text = _fold_q(original)
    out = set()
    for m in _NEW_OP_CUE_RE.finditer(text):
        out.update(re.findall(r"[a-z0-9]+", m.group(0)))
    return {t for t in out if t in _CUE_WORDS}


def _cue_in_schema(original, view):
    """The operation phrase is also a header or a row label, so it may name that field."""
    return bool(_cue_tokens(original) & _schema_tokens(view))


def _named_numeric(body, view):
    found, _ = match_columns(view, Query(body))
    num = [c for c in view.numeric_cols() if not _is_total_col(c)]
    return [c for c, _ in found if c in num]


def _commit_topn(query, spec, topn, view, body):
    """Take a ranked list only when that reading is the one the question asks for.

    A lone integer or number is the later max/min, not a list. A top-N shape main
    already ranked is kept. A new shape (reversed 'highest 3', or a count with no
    list wording) is kept only when one measure is named and the count is not also
    a stored value; otherwise the older reading stands.
    """
    if spec.kind in ("int", "decimal", "number"):
        return False
    lists = _topn_lists(query, spec)
    fresh = (not lists) or topn.get("via") == "rev"
    if not fresh:
        return True
    by = "age" if topn.get("word") in ("oldest", "youngest") else topn.get("by")
    fake = {"op": "rank", "args": {"k": topn.get("k"), "by": by},
            "columns": [by] if by and by in view.df.columns else [], "rows": {"paths": []}}
    if _digit_could_be_value(fake, view, query):
        return False
    return _measure_unique(fake, {"view": view, "body": body})


def _measure_unique(plan, info):
    """One measure column, or exactly one column the question names."""
    view = info["view"]
    num = [c for c in view.numeric_cols() if not _is_total_col(c)]
    by = plan["args"].get("by")
    # A sort key that is not a column is already chosen. It is one reading when the
    # table has a single date column or a birth column; several dates and no birth
    # column are still ambiguous.
    if by and by not in view.df.columns:
        return len(view.date_cols()) == 1 or _birth_column(view) is not None
    if len(num) <= 1:
        return True
    named = _named_numeric(info.get("body") or "", view)
    if len(named) != 1:
        return False
    if by in (None, named[0]):
        return True
    cols = [c for c in plan["columns"] if c in num]
    return cols == [named[0]] or by == named[0]


def _order_year_change_columns(plan, body):
    """Put YEAR_A then YEAR_B so a signed change is value(YEAR_B) - value(YEAR_A)."""
    if plan["op"] != "diff" or plan["args"].get("signed_order") != "second_minus_first":
        return
    m = re.search(r"\bfrom\s+((?:19|20)\d{2})\s+to\s+((?:19|20)\d{2})\b", body or "", re.I)
    if not m:
        return
    y1, y2 = m.group(1), m.group(2)
    located = {}
    for c in plan["columns"]:
        y = _col_year(c)
        if y:
            located[y] = c
    if set(located) == {y1, y2} and len(plan["columns"]) == 2:
        plan["columns"] = [located[y1], located[y2]]


def _col_year(col):
    """The single 4-digit year in a column header, if it has exactly one.

    A wildcard leaf (``Parent | 2018 | *``) is not a year. The year is the
    header level above that leaf.
    """
    parts = str(col).split(" | ")
    if parts and parts[-1] == "*":
        parts = parts[:-1]
    exact = [p.strip() for p in parts if re.fullmatch(r"(?:19|20)\d{2}", p.strip())]
    if len(exact) == 1:
        return exact[0]
    found = []
    for part in parts:
        found.extend(re.findall(r"(?:19|20)\d{2}", part))
    return found[0] if len(found) == 1 else None


def _signed_year_change(plan, info):
    """'change from YEAR_A to YEAR_B' over one group is one signed difference.

    The two columns are the two periods of the same aggregate, value(YEAR_B)
    minus value(YEAR_A). They are not two competing measures.
    """
    if plan.get("op") != "diff" or plan["args"].get("abs"):
        return False
    if plan["args"].get("signed_order") != "second_minus_first":
        return False
    body = info.get("body") or ""
    m = re.search(r"\bfrom\s+((?:19|20)\d{2})\s+to\s+((?:19|20)\d{2})\b", body, re.I)
    if not m:
        return False
    years = {m.group(1), m.group(2)}
    cols = plan["columns"]
    got = [_col_year(c) for c in cols]
    if len(cols) != 2 or set(got) != years:
        return False
    rows = plan["rows"]
    if len(rows["paths"]) == 1:
        return True
    return not rows["paths"] and bool(rows.get("scope") or rows.get("where"))


def _digit_could_be_value(plan, view, original):
    """A digit used as N also occurs as a stored value, so it may not be a count."""
    k = plan["args"].get("k")
    if k is None:
        return False
    try:
        k = int(k)
    except (TypeError, ValueError):
        return False
    bare = strip_instruction(original, parse_instruction(original))
    if not re.search(rf"\b{k}\b", _fold_q(bare)):
        return False
    for col in view.numeric_cols():
        for v in view.df[col]:
            try:
                if float(v) == float(k):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _subset_missing(plan, original):
    """The wording asks for a group, and no row subset was resolved."""
    if plan["op"] not in ("rank", "topk_agg", "nth"):
        return False
    if plan["op"] == "topk_agg" and plan["args"].get("by") and plan["args"].get("k"):
        return False
    if not re.search(r"\b(among|within|inside|subset|nhom)\b", _fold_q(original)):
        return False
    rows = plan["rows"]
    return not (rows["paths"] or rows.get("scope") or rows.get("where") or rows.get("range"))


def _wildcard_parent_groups(col, view):
    """Parent header paths of the numeric columns a ``*`` ref sums.

    ``None`` when ``col`` is a real column. A wildcard is one operand only
    when every child shares one parent path (one sub-column group). Several
    parent paths means the pattern covers more than one group.
    """
    name = str(col)
    num = [c for c in view.numeric_cols() if not _is_total_col(c)]
    if name == "*":
        kids = list(num)
    elif name.endswith(" | *"):
        pre = name[:-4]
        kids = [c for c in num if str(c).startswith(pre + " | ")]
    else:
        return None
    groups = set()
    for kid in kids:
        parts = str(kid).split(" | ")
        groups.add(" | ".join(parts[:-1]) if len(parts) > 1 else "")
    return groups


def _operands_unique(plan, info):
    view = info["view"]
    op = plan["op"]
    num = [c for c in view.numeric_cols() if not _is_total_col(c)]
    cols = [c for c in plan["columns"] if c in num or str(c).endswith(" | *") or c == "*"]
    paths = plan["rows"]["paths"]
    if op == "diff":
        if _signed_year_change(plan, info):
            return True
        # A 'Parent | Year | *' ref sums one sub-column group. It is one operand.
        # A ref whose children sit under two parent headers is still ambiguous.
        wild = []
        for c in cols:
            groups = _wildcard_parent_groups(c, view)
            if groups is None:
                continue
            if len(groups) != 1:
                return False
            wild.append(c)
        pair_rows = len(paths) == 2 and len(cols) == 1
        pair_cols = len(paths) == 1 and len(cols) == 2
        # No row is required when the two operands are already two header groups.
        if len(cols) == 2 and len(wild) == 2 and len(paths) <= 1:
            return True
        return (pair_rows or pair_cols) and _measure_unique(plan, info)
    if op == "nth":
        return bool(plan["args"].get("n")) and _measure_unique(plan, info) and (
            plan["args"].get("by") or len(num) == 1)
    if op == "rank":
        return bool(plan["args"].get("k")) and _measure_unique(plan, info)
    if op == "topk_agg":
        return bool(plan["args"].get("k") and plan["args"].get("by")) and _measure_unique(plan, info)
    if op == "values":
        cells = len(paths) * max(1, len(plan["columns"]))
        return cells == 2 and _measure_unique(plan, info)
    return False


def _new_op_resolved(plan, info, original):
    if _cue_in_schema(original, info["view"]):
        return False
    if _digit_could_be_value(plan, info["view"], original):
        return False
    if _subset_missing(plan, original):
        return False
    return _operands_unique(plan, info)


def _guarded_op(plan, info, original):
    op = plan["op"]
    if op in ("nth", "rank", "diff", "topk_agg"):
        return True
    if op == "values" and re.search(r"\b(pair|respectively|both)\b", _fold_q(original) + " " + _fold_q(info.get("body") or "")):
        return True
    return False


def _legacy_body(original):
    """Wording main would see: the old keyword table, format examples removed."""
    from .vi import is_vietnamese, translate
    text = original
    if is_vietnamese(text):
        text, _ = translate(text, new_ops=False)
    return strip_instruction(text, parse_instruction(text)).lower()


_MAIN_RANK_RE = re.compile(
    r"\brank\b|\bdescending\b|\bascending\b"
    r"|\bfrom (?:most|highest|largest|greatest|biggest|least|fewest|lowest|smallest) to "
    r"(?:fewest|least|lowest|smallest|most|highest|largest|greatest|biggest)\b"
    r"|\bin (?:descending|ascending) order\b"
)


def _main_already_selects(plan, original):
    """True when main's own cue already chooses this operation.

    The guard is only for operations this branch added. A diff main emits
    from 'change' or 'difference' keeps main's confidence, including when a
    newer phrase such as 'by how much' is also in the question.
    """
    low = _legacy_body(original)
    op = plan["op"]
    if op == "diff":
        return bool(re.search(_DIFF_LEGACY, low))
    if op == "nth":
        return _legacy_nth(low)
    if op == "rank":
        return bool(_MAIN_RANK_RE.search(low))
    if op == "topk_agg":
        return bool(re.search(r"\b(?:among|of) the \w+ (?:" + SUP_MAX + "|" + SUP_MIN + r")\b", low))
    return False


def _new_op_blocked(plan, info, original):
    if _main_already_selects(plan, original):
        return False
    triggered = _new_op_keyword_hit(original) or info.get("fresh_op")
    if not triggered or not _guarded_op(plan, info, original):
        return False
    return not _new_op_resolved(plan, info, original)


def _new_op_committed(plan, info, original):
    triggered = _new_op_keyword_hit(original) or info.get("fresh_op")
    return bool(triggered and _guarded_op(plan, info, original) and _new_op_resolved(plan, info, original))


def _top_n(low, view, spec, num_cols, row_paths):
    """'top 3 by score', 'the three highest', 'bottom 2', 'which 3 were admitted earliest'.

    -> dict(k, word, ret, by, over, values) or None. k is a digit or a number word
    (Vietnamese 'ba'/'năm'/'mười' are canonicalised first). A number inside a quoted
    row or column label is not k.
    """
    from .vi import fold
    low = fold(low).lower()
    nw = "|".join(sorted(NUMW, key=len, reverse=True))
    kpat = rf"(\d+|{nw})"
    sup = SUP_MAX + "|" + SUP_MIN
    cands = []
    for m in re.finditer(rf"\b(?:top|bottom)\s+{kpat}\b", low):
        cands.append((m, "edge", m.start(1), m.end(1), m.group(1), "lowest" if m.group(0).split()[0] == "bottom" else "highest"))
    for m in re.finditer(rf"\b(?:the\s+)?{kpat}(?:\s+\w+){{0,6}}?\s+({sup})\b", low):
        cands.append((m, "spread", m.start(1), m.end(1), m.group(1), m.group(2)))
    for m in re.finditer(rf"\b({sup})\s+(\d+|{nw})\b", low):
        if re.search(r"\bat\s+$", low[:m.start()]):
            continue  # 'at least 60' is a threshold, not a count of items
        cands.append((m, "rev", m.start(2), m.end(2), m.group(2), m.group(1)))
    chosen = None
    via = None
    for m, kind, start, end, raw, word in cands:
        if raw.isdigit() and len(raw) == 4 and raw.startswith(("19", "20")):
            continue
        k = int(raw) if raw.isdigit() else NUMW.get(raw)
        if not k or k < 2:
            continue
        if not _k_marked(low, start, end, raw, kind, view):
            continue
        chosen = (k, word, m)
        via = kind
        break
    if not chosen:
        return None
    k, word, m = chosen
    dates = view.date_cols()
    if word in ("oldest", "youngest"):
        by, over = "age", "rows"
    else:
        pool = dates if word in ("earliest", "latest") else list(dict.fromkeys(num_cols + dates))
        by = _measure_column(low, m, view, pool)
        many = len([c for c in num_cols if not _is_total_col(c)]) >= 2
        over = "columns" if len(row_paths) == 1 and many and not by else "rows"
    ret = None
    if spec.instruction:
        ic, _ = match_columns(view, Query(spec.instruction))
        ic = [c for c, _ in ic if c not in num_cols and c not in dates]
        ret = ic[0] if ic else None
    values = _wants_measure_values(low)
    return {"k": k, "word": word, "ret": ret, "by": by, "over": over, "values": values, "via": via}


def _measure_column(low, match, view, pool):
    """Column the k items are ordered by: 'by score', a named measure, or scorer~score."""
    by_m = re.search(r"\bby\s+([a-z][a-z ]{2,40}?)(?:\b|[?.!,])", low)
    if by_m:
        hit = _pool_match(view, by_m.group(1), pool)
        if hit:
            return hit[0]
    window = low[max(0, match.start() - 30): match.end() + 80]
    hit = _pool_match(view, window, pool)
    if hit:
        return hit[0]
    words = [re.sub(r"(ers|er|ors|or|rs)$", "", w) for w in re.findall(r"[a-z]{4,}", window)]
    stems = [w[:4] for w in re.findall(r"[a-z]{5,}", low) if len(w) >= 5]
    for c in pool:
        leaf = tokens(str(c).split(" | ")[-1])
        if any(len(w) >= 4 and any(t.startswith(w) or w.startswith(t) for t in leaf) for w in words):
            return c
        if any(len(t) >= 4 and t[:4] in stems for t in leaf):
            return c
    return None


def _pool_match(view, text, pool):
    found, _ = match_columns(view, Query(text))
    return [c for c, _ in found if c in pool]


def _wants_measure_values(low):
    """'top 3 scores' / '3 lowest scores' / 'three highest scores' lists the measure."""
    nw = "|".join(sorted(NUMW, key=len, reverse=True))
    sup = SUP_MAX + "|" + SUP_MIN
    unit = r"(scores?|values?|numbers?|amounts?)"
    if re.search(rf"\b(?:top|bottom)\s+(?:\d+|{nw})\s+{unit}\b", low):
        return True
    if re.search(rf"\b(?:\d+|{nw})\s+{unit}\s+(?:{sup})\b", low):
        return True
    if re.search(rf"\b(?:\d+|{nw})\s+(?:{sup})\s+{unit}\b", low):
        return True
    return bool(re.search(rf"\b(?:{sup})\s+(?:\d+|{nw})\s+{unit}\b", low))


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
    "compare": r"\b(greater|more|higher|larger|exceed|exceeds|exceeded|less|lower|smaller|fewer|below|above|than|lon hon|nho hon|cao hon|thap hon|so sanh)\b",
    "diff": _DIFF_SRC,
    "sum": r"\bsumm?ing\b|\bsum\b|\btotal\b|\bcombined\b|\baltogether\b|\bin all\b|\bacross all\b|\btong\b|\bcong lai\b",
    "mean": r"\baverage\b|\bmean\b|\btrung binh\b|\bbinh quan\b",
    "count": r"\bhow many\b|\bnumber of\b|\bcount\b|\bbao nhieu\b|\bso luong\b",
    "rank": r"\brank\b|\bdescending\b|\bascending\b|\btop\b|\bbottom\b|\bxep hang\b|\bfrom (most|highest|largest|greatest|biggest|least|fewest|lowest|smallest) to\b",
    "share": r"\bshare\b|\bproportion\b|percentage of|what percent|\bty le\b|\bti le\b|\bty trong\b",
    "ratio": r"/|\bratio\b|\brate\b",
    "argmax": r"\b(" + SUP_MAX + r")\b", "argmin": r"\b(" + SUP_MIN + r")\b",
    "max": r"\b(" + SUP_MAX + r")\b", "min": r"\b(" + SUP_MIN + r")\b",
    "nth": r"\b(" + SUP_MAX + "|" + SUP_MIN + r"|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th)|thu\s+\d+)\b",
    "topk_agg": r"\b(" + SUP_MAX + "|" + SUP_MIN + r")\b",
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
    if info.get("ambiguous_rank_scope"):
        return 0.0, ["rank:ambiguous scope"]
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
    if info.get("ambiguous_rank_scope"):
        return 0.0, ["rank:ambiguous scope"]
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
