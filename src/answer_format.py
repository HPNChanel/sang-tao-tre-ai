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
    item_kinds: list[str] = field(default_factory=list)
    sep: str = ", "
    order: list[str] = field(default_factory=list)
    instruction: str = ""
    prefix: str = ""
    suffix: str = ""


def _instr(q: str) -> str:
    sents = re.split(r"(?<=[.?!])\s+(?=[A-Z])", q.strip())
    for i, sentence in enumerate(sents):
        if re.match(r"(answer|provide|respond|give|return|use)\b", sentence, re.I) and re.search(
                r"\b(exactly|only|integer|decimal|number|name|label|separat\w*|join\w*)\b|[`'\"]|\banswer (with|as)\b|\byes or no\b",
                sentence, re.I):
            # Keep rounding/sign qualifications after the formatting sentence.
            # They are instructions, not additional operands or row labels.
            return " ".join(sents[i:])
    return ""


def parse_instruction(query: str) -> FormatSpec:
    ins = _instr(query)
    low = ins.lower()
    spec = FormatSpec(instruction=ins)
    separator = (re.search(r"(?:separated\s+by|joined(?:\s+(?:by|with))?|join\s+with)\s+[`'\"]([^`'\"]*)[`'\"]", ins, re.I)
                 or re.search(r"\buse(?:\s+exactly)?\s+[`'\"]([^`'\"]*)[`'\"]\s+(?:between|to separate)", ins, re.I))
    if separator:
        spec.sep = separator[1]
    literal = re.search(r"\b(?:return|answer|give)(?:\s+exactly)?\s+['\"]([^'\"]*)<[^>]+>([^'\"]*)['\"]", ins, re.I)
    if literal:
        spec.prefix, spec.suffix = literal.groups()
    elif re.search(r"(?:followed by|trailing)\s+['\"]?\s*%|\ba % sign\b", low) and not re.search(r"\bno\b[^.;]*%", low):
        spec.suffix = "%"
    else:
        suffix = re.search(r"followed by\s+['\"]([^'\"]+)['\"]", ins, re.I)
        if suffix:
            spec.suffix = suffix[1]
    dec = re.search(r"(\d+|" + "|".join(_WORDNUM) + r") decimal", low)
    d = None if not dec else int(dec.group(1)) if dec.group(1).isdigit() else _WORDNUM[dec.group(1)]
    cnt = re.search(r"\b(two|three|four|five|six|\d+) (integers|numbers|values|names|items|\w+ names)\b", low)
    order = re.search(r"order\s+[`'\"]([^`'\"]+)[`'\"]", ins) or re.search(r"as\s+`([^`]+)`", ins)
    if order:
        spec.order = [s.strip() for s in order.group(1).split(",")]
    if re.search(r"\byes or no\b|'yes' or 'no'|\"yes\" or \"no\"", low):
        spec.kind = "yesno"; return spec
    is_list = bool(cnt) or bool(separator) or "separated by" in low or "separating" in low or "names joined" in low or len(spec.order) > 1
    if is_list:
        spec.kind = "list"
        spec.count = (int(cnt.group(1)) if cnt and cnt.group(1).isdigit() else _WORDNUM.get(cnt.group(1)) if cnt else None) or (len(spec.order) or None)
        spec.decimals = d
        spec.item_kind = "decimal" if d is not None else "int" if re.search(r"integer", low) else "name"
        if len(spec.order) == 2 and re.search(r"\b(year|name|group|class|category|sheet)\b", spec.order[0], re.I):
            spec.item_kinds = ["name", "decimal" if d is not None else "int" if "integer" in low else "number"]
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


def _fmt_scalar(v, kind, d=None):
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
        if spec.item_kinds and len(vals) != len(spec.item_kinds):
            raise ValueError("projection result does not match requested fields")
        result = spec.sep.join(_fmt_scalar(v, spec.item_kinds[i] if spec.item_kinds else spec.item_kind, spec.decimals)
                               for i, v in enumerate(vals))
    else:
        result = _fmt_scalar(value, spec.kind, spec.decimals)
    return spec.prefix + result + spec.suffix
