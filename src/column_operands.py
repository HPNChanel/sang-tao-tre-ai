"""Resolve independently qualified operands and explicitly stored totals."""
import re


def refine(plan, view, body, spec):
    from .textmatch import Query, tokens
    low = body.lower()
    if re.search(r"\b(as reported|not summed|reported total|stored total)\b", low):
        q = Query(body)
        parents = [c for c in view.numeric_cols() if " | " in c and c.split(" | ")[-1].lower() == "total"
                   and q.match(c.rsplit(" | ", 1)[0])[0] == 1.0]
        if len(parents) == 1:
            plan["columns"] = parents
            plan["op"] = "lookup"
            plan["args"] = {}
            return True
    alternatives = re.search(r"\beither\s+`([^`]+)`\s+or\s+`([^`]+)`", spec.instruction, re.I)
    if not alternatives or not re.search(r"\bwho\b.*\bmore\b.*\bby how many\b", low):
        return False
    cols = []
    for phrase in alternatives.groups():
        wanted = tokens(phrase, keep_stop=True)
        hits = []
        for c in view.numeric_cols():
            actual = tokens(c, keep_stop=True)
            if all(t in actual for t in wanted) and ("not" in wanted) == ("not" in actual):
                hits.append(c)
        if len(hits) > 1:
            q = Query(body)
            scores = {c: len(q.match(c)[1]) for c in hits}
            hits = [c for c in hits if scores[c] == max(scores.values())]
        if len(hits) != 1:
            return False
        cols += hits
    if len(set(cols)) != 2 or len(plan["rows"]["paths"]) != 1:
        return False
    plan["columns"] = cols
    plan["op"] = "winner_diff"
    plan["args"] = {"names": list(alternatives.groups())}
    return True
