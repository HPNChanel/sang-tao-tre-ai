"""Lightweight, general text normalisation and fuzzy token matching."""
from __future__ import annotations

import re

MONTHS = {"january": "jan", "february": "feb", "march": "mar", "april": "apr", "june": "jun", "july": "jul",
          "august": "aug", "september": "sep", "sept": "sep", "october": "oct", "november": "nov", "december": "dec"}
STOP = set("""a an the of in on at to for from by with and or as is was were are be been being did does do what which who whom
whose how many much this that these those its it their there than then into per s not no only answer value values number
year years age aged ages all any each between across over during within same e g eg including include""".split())
_TOK = re.compile(r"[a-z]+|\d+(?:\.\d+)?")


def stem(w: str) -> str:
    w = MONTHS.get(w, w)
    if w[0].isdigit() or len(w) <= 3:
        return w
    for suf, rep, minlen in (("ies", "y", 5), ("ing", "", 6), ("ed", "", 5), ("es", "", 6), ("s", "", 4), ("al", "", 7)):
        if w.endswith(suf) and len(w) >= minlen and not (suf == "s" and w.endswith("ss")):
            w = w[: -len(suf)] + rep
            break
    if w.endswith("e") and len(w) > 5:
        w = w[:-1]
    return w


def tokens(s: str, keep_stop=False) -> list[str]:
    s = str(s).lower().replace("’", "'").replace("'s", "")
    out = []
    for t in _TOK.findall(s):
        if not keep_stop and t in STOP:
            continue
        out.append(stem(t))
    return out


def tok_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    if a[0].isdigit() or b[0].isdigit():
        return False
    return min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a))


class Query:
    def __init__(self, text: str):
        self.text = text
        raw = str(text).lower().replace("’", "'").replace("'s", "")
        self.toks = []  # (stem, position)
        for i, t in enumerate(_TOK.findall(raw)):
            if t in STOP:
                continue
            self.toks.append((stem(t), i))

    def positions(self, tok: str, exclude=frozenset()) -> set[int]:
        return {p for t, p in self.toks if p not in exclude and tok_eq(t, tok)}

    def match(self, phrase: str, exclude=frozenset()):
        """-> (fraction of phrase content tokens found, set of query positions used)."""
        pt = tokens(phrase)
        if not pt:
            return 0.0, set()
        pos, hit = set(), 0
        for t in pt:
            ps = self.positions(t, exclude)
            if ps:
                hit += 1
                pos |= ps
        return hit / len(pt), pos
