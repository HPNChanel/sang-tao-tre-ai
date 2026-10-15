"""Generic blanks for plans that would otherwise be confident and wrong.

Signals come from the question wording (English or Vietnamese, digits or number
words). Nothing here is keyed to a file, an id, a column name, or a question.
"""
from __future__ import annotations

import re

_EN_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_VI_NUM = {
    "mot": 1, "hai": 2, "ba": 3, "bon": 4, "nam": 5, "sau": 6,
    "bay": 7, "tam": 8, "chin": 9, "muoi": 10,
}
_NUM = {**_EN_NUM, **_VI_NUM}

_SUP_DESC = (
    "largest|highest|most|greatest|biggest|oldest|latest|top"
    "|lon nhat|cao nhat|nhieu nhat|dong dau"
)
_SUP_ASC = (
    "smallest|lowest|least|fewest|youngest|earliest|bottom"
    "|nho nhat|thap nhat|it nhat|dong cuoi"
)
_SUP = _SUP_DESC + "|" + _SUP_ASC
_ORD_EN = (
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r"|\d+(?:st|nd|rd|th)"
)

_AGG = {"sum", "mean", "count", "max", "min", "topk_agg"}
_LABEL_OPS = {"rank", "argmax", "argmin"}


def fold_text(text: str) -> str:
    from .vi import fold
    return fold(text or "").lower()


def _kpat() -> str:
    words = "|".join(sorted(_NUM, key=len, reverse=True))
    return rf"(\d+|{words})"


def _kval(raw: str):
    if not raw:
        return None
    if str(raw).isdigit():
        if len(raw) == 4 and raw.startswith(("19", "20")):
            return None
        return int(raw)
    return _NUM.get(raw)


def _collect_ks(text: str) -> list[int]:
    """Top-N counts written in the question. 'at least 60' is not a count of items."""
    if not text:
        return []
    low = fold_text(text)
    kp = _kpat()
    found = []

    def add(raw, start, end):
        before = low[:start]
        after = low[end:]
        if re.search(r"\bat\s+(?:least|most)\s+$", before) or re.search(r"\bat\s+$", before):
            return
        # '20 or more … largest' and 'span 9 and over' are thresholds, not a row count.
        if re.match(r"\s+(?:or|and|to|years?|tuoi|aged)\b", after):
            return
        # 'năm 2024' is a calendar year. Bare 'năm' before a superlative is still five.
        if raw == "nam" and re.match(r"\s+(?:19|20)\d{2}\b", after):
            return
        k = _kval(raw)
        if k and k >= 2:
            found.append(k)

    for m in re.finditer(rf"\b(?:top|bottom)\s+{kp}\b", low):
        add(m.group(1), m.start(1), m.end(1))
    for m in re.finditer(rf"\b{kp}(?:\s+\w+){{0,6}}?\s+(?:{_SUP})\b", low):
        add(m.group(1), m.start(1), m.end(1))
    for m in re.finditer(rf"\b(?:{_SUP})\s+{kp}\b", low):
        add(m.group(1), m.start(1), m.end(1))
    for m in re.finditer(rf"\b{kp}\s+dong\s+(?:dau|cuoi)\b", low):
        add(m.group(1), m.start(1), m.end(1))
    for m in re.finditer(rf"\bdong\s+(?:dau|cuoi)\s+{kp}\b", low):
        add(m.group(1), m.start(1), m.end(1))
    return found


def topn_signal(texts) -> int | None | str:
    """The one N in a top-N signal, 'ambiguous' when several N disagree, else None."""
    ks = []
    for text in texts:
        ks.extend(_collect_ks(text or ""))
    uniq = list(dict.fromkeys(ks))
    if not uniq:
        return None
    if len(uniq) > 1:
        return "ambiguous"
    return uniq[0]


def direction(texts) -> str | None:
    """'asc' or 'desc' when the question names one sort direction."""
    votes = set()
    desc = re.compile(rf"\b(?:{_SUP_DESC}|descending|giam dan)\b")
    asc = re.compile(rf"\b(?:{_SUP_ASC}|ascending|tang dan)\b")
    for text in texts:
        if not text:
            continue
        low = fold_text(text)
        if re.search(r"\bfrom (?:highest|largest|most|greatest|biggest) to\b", low):
            votes.add("desc")
            low = re.sub(
                r"\bfrom (?:highest|largest|most|greatest|biggest) to "
                r"(?:lowest|smallest|least|fewest|highest|largest)\b",
                " ", low)
        if re.search(r"\bfrom (?:lowest|smallest|least|fewest) to\b", low):
            votes.add("asc")
            low = re.sub(
                r"\bfrom (?:lowest|smallest|least|fewest) to "
                r"(?:highest|largest|most|greatest|biggest|lowest|smallest)\b",
                " ", low)
        if desc.search(low):
            votes.add("desc")
        if asc.search(low):
            votes.add("asc")
    if len(votes) == 1:
        return votes.pop()
    return None


def nth_signal(original: str, translated: str) -> bool:
    """Ordinal rank (2nd-largest, ranks third, thứ 3, lớn thứ hai). Not 'thứ tự'."""
    original = original or ""
    masked = re.sub(r"thứ\s+tự", " ", original, flags=re.I)
    folded = fold_text(masked)
    folded = re.sub(r"\bthu\s+tu\b", " ", folded)
    pieces = [folded, fold_text(translated or "")]
    ord_en = _ORD_EN
    sup = _SUP
    for low in pieces:
        if re.search(rf"\b(?:{ord_en})[- ](?:{sup})\b", low):
            return True
        if re.search(rf"\b(?:{sup})[- ](?:{ord_en})\b", low):
            return True
        if re.search(rf"\branks?\s+(?:{ord_en})\b", low):
            return True
        if re.search(r"\b(?:lon|cao|thap|nho|nhieu|it)\s+(?:nhat\s+)?thu\s+"
                     r"(?:nhat|hai|nhi|ba|nam|sau|bay|tam|chin|muoi|\d+)\b", low):
            return True
        if re.search(r"\bthu\s+(?:nhat|hai|nhi|ba|nam|sau|bay|tam|chin|muoi|\d+)\s+"
                     r"(?:lon|cao|thap|nho|nhieu|it)\b", low):
            return True
        if re.search(r"\b(?:xep|dung)\s+thu\s+(?:nhat|hai|nhi|ba|nam|sau|bay|tam|chin|muoi|\d+)\b", low):
            return True
        if re.search(r"\bhang\s+(?:nhat|nhi|ba|tu|nam)\b", low):
            return True
        if re.search(r"\bthu\s+\d+\b", low):
            return True
    if re.search(r"thứ\s+(?:nhất|hai|nhì|ba|tư|năm|sáu|bảy|tám|chín|mười|\d+)", original, re.I):
        return True
    return False


def asks_list(text: str, fmt: dict | None) -> bool:
    fmt = fmt or {}
    low = fold_text(text or "")
    if fmt.get("kind") == "list":
        return True
    if re.search(r"\b(names|labels|list)\b|separated by|comma-separated|comma separated|"
                 r"phan cach|liet ke|in that order|respectively", low):
        return True
    return False


def asks_single_number(text: str, fmt: dict | None) -> bool:
    """A single figure: how many / total / sum / bao nhiêu, and not a list."""
    fmt = fmt or {}
    if asks_list(text, fmt):
        return False
    if fmt.get("kind") in ("int", "decimal", "number"):
        return True
    low = fold_text(text or "")
    return bool(re.search(r"\b(how many|bao nhieu|tong|cong lai|total|sum|summing)\b", low))


def single_number(ans) -> bool:
    return re.fullmatch(r"-?\d+(\.\d+)?", str(ans).strip()) is not None


def item_count(ans, fmt: dict | None) -> int:
    fmt = fmt or {}
    a = str(ans).strip()
    sep = fmt.get("sep") or ", "
    if sep and sep in a:
        return len([p for p in a.split(sep) if p.strip()])
    if ", " in a:
        return len([p for p in a.split(", ") if p.strip()])
    return 1


def pair_demanded(fmt: dict | None, query: str | None) -> bool:
    """True when the question or the format asks for exactly two values."""
    fmt = fmt or {}
    if fmt.get("count") == 2:
        return True
    order = fmt.get("order") or []
    if len(order) == 2:
        return True
    if not query:
        return False
    if re.search(r"\b(two|2)\s+(integers|numbers|values|names|items)\b", query, re.I):
        return True
    if re.search(r"\bin that order\b|\brespectively\b", query, re.I):
        # A larger explicit N is a list, not a pair.
        if re.search(r"\b(three|four|five|six|seven|eight|nine|ten|[3-9]|\d{2,})\b", query, re.I):
            return False
        return True
    return False


def label_list(ans, fmt) -> bool:
    if item_count(ans, fmt) < 2:
        return False
    sep = (fmt or {}).get("sep") or ", "
    parts = [p.strip() for p in str(ans).split(sep) if p.strip()]
    return bool(parts) and all(not single_number(p) for p in parts)


def shape_mismatch(plan: dict, texts) -> bool:
    """List question answered by a numeric aggregate, or a single-number question answered by labels."""
    fmt = plan.get("format") or {}
    blob = " ".join(t for t in texts if t)
    op = plan.get("op")
    if asks_list(blob, fmt) and op in _AGG:
        return True
    if asks_single_number(blob, fmt) and op in _LABEL_OPS:
        return True
    return False
