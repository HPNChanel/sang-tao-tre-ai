"""Parse the trailing 'Answer with only ...' instruction into a FormatSpec and format values."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


@dataclass
class FormatSpec:
    kind: str = "text"            # int | decimal | yesno | name | list | number | text
    decimals: int | None = None
    count: int | None = None       # list length
    item_kind: str | None = None   # for list: int | decimal | name
    sep: str = ", "
    order: list[str] = field(default_factory=list)
    instruction: str = ""
    thousands: bool = False        # '1,234' digit grouping requested


def _instr(q: str) -> str:
    m = re.search(r"(answer (with|as)|provide|respond|give)\b[^.?!]*(\.[0-9][^.?!]*)*[.?!]?\s*$", q, re.I)
    if m:
        # take from the LAST sentence that starts an instruction
        sents = re.split(r"(?<=[.?!])\s+(?=[A-Z])", q.strip())
        tail = [s for s in sents if re.match(r"(answer|provide|respond|give|return)\b", s, re.I)]
        if tail:
            return " ".join(tail)
    return ""


def parse_instruction(query: str) -> FormatSpec:
    ins = _instr(query)
    low = ins.lower()
    spec = FormatSpec(instruction=ins)
    dec = re.search(r"(\d+|" + "|".join(_WORDNUM) + r") decimal", low)
    d = None if not dec else int(dec.group(1)) if dec.group(1).isdigit() else _WORDNUM[dec.group(1)]
    cnt = re.search(r"\b(two|three|four|five|six|\d+) (integers|numbers|values|names|items|\w+ names)\b", low)
    order = re.search(r"order\s+[`'\"]([^`'\"]+)[`'\"]", ins) or re.search(r"as\s+`([^`]+)`", ins)
    if order:
        spec.order = [s.strip() for s in order.group(1).split(",")]
    spec.thousands = bool(re.search(r"(formatted|written) with commas|with (a )?(thousands?|comma) separators?|"
                                    r"comma as (the )?thousands? separator|with commas\b", low))
    if re.search(r"\byes or no\b|'yes' or 'no'|\"yes\" or \"no\"", low):
        spec.kind = "yesno"; return spec
    is_list = bool(cnt) or "separated by" in low or "separating" in low or len(spec.order) > 1
    if is_list:
        spec.kind = "list"
        spec.count = (int(cnt.group(1)) if cnt and cnt.group(1).isdigit() else _WORDNUM.get(cnt.group(1)) if cnt else None) or (len(spec.order) or None)
        spec.decimals = d
        spec.item_kind = "decimal" if d is not None else "int" if re.search(r"integer", low) else "name"
        return spec
    if d is not None:
        spec.kind, spec.decimals = "decimal", d
    elif re.search(r"\binteger\b", low):
        spec.kind = "int"
    elif re.search(r"\b(number|value|amount|percentage)\b", low):
        spec.kind = "number"
    elif re.search(r"\bname\b|\bcategory\b|\blabel\b|\bonly the\b", low):
        spec.kind = "name"
    return spec


def _round(x, d):
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)


def _fmt_scalar(v, kind, d=None, thousands=False):
    if thousands and kind in ("int", "number", "decimal"):
        f = float(v)
        if kind == "decimal":
            return f"{_round(f, d):,.{d}f}"
        return f"{int(_round(f, 0)):,}" if kind == "int" or f.is_integer() else f"{f:,}"
    if kind == "int":
        return str(int(_round(float(v), 0)))
    if kind == "decimal":
        return f"{_round(float(v), d):.{d}f}"
    if kind == "number":
        f = float(v)
        return str(int(f)) if f.is_integer() else f"{f:.10g}"
    if kind == "yesno":
        if isinstance(v, str):
            return "Yes" if v.strip().lower() in ("yes", "true", "y") else "No"
        return "Yes" if v else "No"
    return str(v).strip()


def format_answer(value, spec: FormatSpec) -> str:
    if spec.kind == "list":
        vals = list(value) if isinstance(value, (list, tuple)) else [value]
        return spec.sep.join(_fmt_scalar(v, spec.item_kind, spec.decimals) for v in vals)
    return _fmt_scalar(value, spec.kind, spec.decimals, spec.thousands)
