"""Generic blanks for plans that would otherwise be confident and wrong.

Two checks remain: the answer type, and an nth rank (place 2 or later) that
has no measure column. A ranking that already has its measure column and its
direction is left alone. Nothing here is keyed to a file, an id, a column
name, or a question.
"""
from __future__ import annotations

import re

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


def nth_signal(original: str, translated: str) -> bool:
    """Ordinal rank (2nd-largest, ranks third, thứ 3, lớn thứ hai). Not 'thứ tự'."""
    original = original or ""
    masked = re.sub(r"thứ\s+tự", " ", original, flags=re.I)
    folded = fold_text(masked)
    folded = re.sub(r"\bthu\s+tu\b", " ", folded)
    pieces = [folded, fold_text(translated or "")]
    ord_en = _ORD_EN
    sup = _SUP
    # 'highest first' / 'lowest first' / 'oldest first' orders a list. It is not an nth rank.
    list_order = re.compile(
        r"\b(?:largest|highest|most|greatest|biggest|smallest|lowest|least|fewest|"
        r"oldest|youngest|earliest|latest)\s+first\b"
    )
    for low in pieces:
        low = list_order.sub(" ", low)
        low = re.sub(r"\bfirst\s+names?\b", " ", low)
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


def asks_for_label(text: str) -> bool:
    """Which/who/name. A column called Total in that question is not a request for a sum."""
    low = fold_text(text or "")
    if re.search(r"\b(how many|bao nhieu)\b", low):
        return False
    if re.search(r"\b(which|who|whom)\b", low):
        return True
    if re.search(r"\bonly the name\b|\btra loi ten\b|\bten nhom\b", low):
        return True
    return False


def asks_single_number(text: str, fmt: dict | None) -> bool:
    """A single figure: how many / total / sum / bao nhiêu, and not a list or a label."""
    fmt = fmt or {}
    if asks_list(text, fmt) or asks_for_label(text):
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


def single_superlative(plan: dict | None) -> bool:
    """N=1: argmax/argmin, or an nth plan whose place is first."""
    if not plan:
        return False
    op = plan.get("op")
    if op in ("argmax", "argmin"):
        return True
    if op == "nth":
        try:
            return int((plan.get("args") or {}).get("n") or 0) <= 1
        except (TypeError, ValueError):
            return False
    return False


def ranking_ready(plan: dict | None) -> bool:
    """Rank, measure column, and direction are already set.

    rank, nth, and topk_agg are ready when args.by is set and args.order is
    asc or desc. The count N is not required, and a set column is not matched
    against the question again. argmax and argmin are ready when a measure
    column is set; the operation is the direction.
    """
    if not plan:
        return False
    op = plan.get("op")
    args = plan.get("args") or {}
    if op in ("rank", "nth", "topk_agg"):
        return bool(args.get("by")) and args.get("order") in ("asc", "desc")
    if op in ("argmax", "argmin"):
        return any(c for c in (plan.get("columns") or []) if c)
    return False


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
