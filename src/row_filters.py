"""Bind explicit numeric predicates to their own clauses before selecting rows."""
import re

PREFIX = {"at least": ">=", "at most": "<=", "more than": ">", "greater than": ">",
          "less than": "<", "below": "<", "above": ">", "under": "<", "over": ">",
          "exceed": ">", "exceeds": ">", "exceeded": ">"}


def refine(plan, view, body, spec):
    from .planner import match_columns
    from .textmatch import Query
    low = body.lower()
    annual = bool(re.search(r"\bannual\b.*\b(?:year|quarter)\b", low))
    if annual:
        plan["rows"]["label_kind"] = "year"
    columns = view.numeric_cols()
    conds = []
    digit = re.search(r"\b(\d+)-digit\s+([\w -]+?)\s+code\b", low)
    unresolved = False
    for clause in re.split(r"\band\b|\bwhile\b", body, flags=re.I):
        matches = list(re.finditer(r"\b(" + "|".join(sorted(PREFIX, key=len, reverse=True))
                                  + r")\s+(-?\d[\d,]*(?:\.\d+)?)", clause, re.I))
        if not matches:
            continue
        m = matches[-1]
        text = clause[:m.start()]
        if digit:
            text = re.sub(re.escape(digit[0]), "", text, flags=re.I)
        found, _ = match_columns(view, Query(text))
        cs = [c for c, _ in found if c in columns]
        # A count is stored in a Total child; a wildcard would add rates to it.
        if not cs:
            for c, _ in found:
                if c.endswith(" | *"):
                    candidates = [x for x in columns if x.startswith(c[:-1])]
                    rate = bool(re.search(r"\b(rate|percent|percentage)\b", text, re.I))
                    cs += [x for x in candidates if (bool(re.search(r"percent|rate", x.split(" | ")[-1], re.I)) if rate
                           else x.split(" | ")[-1].lower() in {"total", "number", "count"})]
        if len(cs) == 1:
            conds.append({"column": cs[0], "op": PREFIX[m[1].lower()], "value": float(m[2].replace(",", ""))})
        else:
            unresolved = True
    if annual and re.search(r"\bhow many\b", low) and len(plan["columns"]) == 2 \
            and re.search(r"\b(higher|greater|lower|less) than\b", low):
        a, b = plan["columns"]
        conds = [{"column": a, "op": "<" if re.search(r"\b(lower|less) than\b", low) else ">",
                  "value": 0, "value_column": b}]
    if unresolved or not conds:
        return False
    if digit:
        hits, _ = match_columns(view, Query(digit[2] + " code"))
        if len(hits) == 1:
            conds.append({"column": hits[0][0], "op": "digits==", "value": int(digit[1])})
        else:
            return False
    r = plan["rows"]
    if spec.kind == "yesno":
        if len(conds) != 1 or not r["paths"]:
            return False
        # Select a named section before resolving a repeated leaf.
        q = Query(body)
        secs = [s for s in dict.fromkeys(view.df["_section"]) if s and q.match(str(s))[0] == 1.0]
        paths = [p for p in r["paths"] if not secs or any(p.startswith(str(s) + " > ") for s in secs)]
        if len(paths) != 1:
            return False
        r["paths"] = paths
        plan["op"] = "compare"
        plan["columns"] = [conds[0]["column"]]
        plan["args"] = {"cmp": conds[0]["op"], "value": conds[0]["value"]}
        r["where"] = []
        return True
    if not re.search(r"\b(?:which|identify|how many)\b", low):
        return False
    plan["op"] = "count" if re.search(r"\bhow many\b", low) else "select"
    plan["args"] = {}
    plan["columns"] = []
    r.update(paths=[], scope=None, where=conds)
    return True
