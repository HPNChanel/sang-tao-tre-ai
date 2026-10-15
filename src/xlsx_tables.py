"""Parse messy statistical .xlsx sheets into clean pandas tables.

General heuristics only (no file/question special cases):
- title rows: a single non-empty cell above the real header (any column), or a
  full-width merge; a one-value row that covers every data column stays a header
  and is moved to `meta` instead of polluting every column name
- header rows: rows above data whose label column is empty or vertically merged,
  or all-text rows; a row of years (text allowed beside them, no measurements)
  directly above measurement rows, including under a parent header; merged cells
  are forward-filled and flattened 'Parent | Child'
- data rows: label + values; label-only rows are section rows -> `_row_path`
- a label-only row after the measurements, with no further data, is a note
- indentation (alignment.indent or leading spaces) builds the hierarchy
- a new header block after data starts a new table (multiple tables per sheet)
- notes/footnotes after the data go to `notes`
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, field

import openpyxl
import pandas as pd
from openpyxl.utils import get_column_letter

NA_TOKENS = {"", "-", "–", "—", "(na)", "na", "n/a", "(x)", "(z)", "(d)", "(s)", "...", "…", "x", "(b)"}
_SUP = "¹²³⁴⁵⁶⁷⁸⁹⁰"
_FOOT_RE = re.compile(r"(\s*\(\d{1,2}\))+\s*$|\s+\d{1,2}/\s*$|\s*\\\d+\\\s*$|[" + _SUP + r"]+\s*$|\s*\((p|r|e|p\)\s*\(r)\)\s*$|\s*\*+\s*$")
_NOTE_RE = re.compile(r"^\s*(footnotes?|note|notes|source|sources|\(\d+\)|\d+/|\*)", re.I)


_KEEP_HYPHEN = {"self", "non", "co", "ex", "part", "full", "year", "well", "high", "low",
                "long", "short", "semi", "multi", "anti", "post", "pre", "re", "mid", "first", "second", "third"}


def _dehyphen(m):
    w = m.group(1)
    return w + ("-" if w.lower() in _KEEP_HYPHEN else "")


def clean_text(s) -> str:
    if s is None:
        return ""
    s = str(s).replace("\u00a0", " ")
    s = re.sub(r"(\w+)-\n(?=[a-z])", _dehyphen, s)  # "Con-\nstruction" -> "Construction"
    s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_footnotes(s: str) -> str:
    prev = None
    while prev != s:
        prev, s = s, _FOOT_RE.sub("", s).strip()
    return s


def to_number(v):
    """Coerce a cell value to float; None if not numeric."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return None
    s = str(v).strip().replace("\u00a0", "")
    if s.lower() in NA_TOKENS:
        return None
    s = re.sub(r"(?<=\d)\(\d{1,2}\)$|\s+\d/$|[¹²³⁴⁵⁶⁷⁸⁹⁰]+$|\s*\([a-zA-Z]\)$|\*+$", "", s).strip()  # footnote markers
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


@dataclass
class Table:
    sheet: str
    cell_range: str
    title: str
    notes: list[str]
    meta: list[str]
    df: pd.DataFrame
    header_rows: list[int] = field(default_factory=list)
    label_col: str = ""
    # True when the caption/year-banner pass rewrote this table's header.
    title_skipped: bool = False

    def __repr__(self):
        return f"Table({self.sheet}!{self.cell_range}, {self.df.shape}, title={self.title[:40]!r})"


def _grid(ws):
    """Values grid with merged cells forward-filled; plus merge info."""
    maxr, maxc = ws.max_row, ws.max_column
    g = [[ws.cell(r, c).value for c in range(1, maxc + 1)] for r in range(1, maxr + 1)]
    indent = [[0] * maxc for _ in range(maxr)]
    for r in range(1, maxr + 1):
        for c in range(1, maxc + 1):
            cell = ws.cell(r, c)
            ind = int(cell.alignment.indent or 0) if cell.alignment else 0
            v = cell.value
            if isinstance(v, str):
                ind += (len(v) - len(v.lstrip(" "))) // 2 if v.startswith(" ") else 0
            indent[r - 1][c - 1] = ind
    vmerge = set()  # (r,c) covered by multi-row merge (0-based)
    hspan = {}  # (r,c) -> True if part of horizontal merge spanning
    for m in ws.merged_cells.ranges:
        v = g[m.min_row - 1][m.min_col - 1]
        for r in range(m.min_row, m.max_row + 1):
            for c in range(m.min_col, m.max_col + 1):
                g[r - 1][c - 1] = v
                if m.max_row > m.min_row:
                    vmerge.add((r - 1, c - 1))
                if m.max_col > m.min_col:
                    hspan[(r - 1, c - 1)] = (m.min_col - 1, m.max_col - 1)
    # resolve trivial formulas like '=H1'
    for r in range(maxr):
        for c in range(maxc):
            v = g[r][c]
            if isinstance(v, str) and re.fullmatch(r"=\$?[A-Z]{1,3}\$?\d+", v):
                try:
                    g[r][c] = ws[v[1:].replace("$", "")].value
                except Exception:
                    pass
    return g, indent, vmerge, hspan


def _empty(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _is_value(v):
    return to_number(v) is not None or isinstance(v, (_dt.date, _dt.datetime))


def _is_year(v):
    n = to_number(v)
    return n is not None and float(n).is_integer() and 1900 <= n <= 2100


def load_tables(path, sheets=None) -> list[Table]:
    from . import conventions as C
    from .formula_eval import FormulaEvaluator
    wb_v = openpyxl.load_workbook(path, data_only=True)
    wb_f = openpyxl.load_workbook(path, data_only=False)
    ev = FormulaEvaluator(wb_f, wb_v)
    out = []
    for name in wb_v.sheetnames:
        if sheets and name not in sheets:
            continue
        ws = wb_v[name]
        if C.SKIP_HIDDEN_SHEETS and ws.sheet_state != "visible":
            continue
        if ws.max_row == 0 or ws.max_column == 0:
            continue
        g, indent, vmerge, hspan = _grid(ws)
        if C.EVALUATE_UNCACHED_FORMULAS:
            wf = wb_f[name]
            for row in wf.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.startswith("="):
                        r, c = cell.row - 1, cell.column - 1
                        if r < len(g) and c < len(g[r]) and (g[r][c] is None or not C.PREFER_CACHED_FORMULA_VALUES):
                            g[r][c] = ev.value(name, cell.coordinate)
        if not C.INCLUDE_HIDDEN_ROWS:
            for rr, d in ws.row_dimensions.items():
                if d.hidden and rr - 1 < len(g):
                    g[rr - 1] = [None] * len(g[rr - 1])
        out.extend(_split_tables(name, g, indent, vmerge, hspan))
    return out


def _row_kind(g, r, vmerge, ncols, lc):
    row = g[r]
    nonempty = [c for c in range(ncols) if not _empty(row[c])]
    if not nonempty:
        return "blank"
    label = row[lc]
    others = [c for c in nonempty if c > lc]
    vals = [row[c] for c in others]
    distinct = {clean_text(row[c]) for c in nonempty}
    if not others or (len(distinct) == 1 and len(nonempty) > 1 and lc in nonempty):
        s = clean_text(row[nonempty[0]])
        if _NOTE_RE.match(s) or len(s) > 90:
            return "note"
        return "section"
    has_val = any(_is_value(v) for v in vals)
    label_empty = _empty(label) or ((r, lc) in vmerge and r > 0 and g[r - 1][lc] == label
                                    and (not has_val or all(_is_year(v) or isinstance(v, str) for v in vals)))
    if label_empty:
        return "header"
    if has_val:
        # all-year rows with a label are still header-ish (e.g. 'Characteristic | 2023')
        if len(vals) >= 2 and all(_is_year(v) for v in vals) and len(set(map(str, vals))) == 1:
            return "header"
        prev = next((g[k] for k in range(r - 1, -1, -1) if any(not _empty(x) for x in g[k])), None)
        prev_blank = r == 0 or all(_empty(x) for x in g[r - 1])
        prev_title = prev is not None and len({clean_text(x) for x in prev if not _empty(x)}) == 1
        if all(_is_year(v) for v in vals) and isinstance(label, str) and (len(vals) >= 2 or any(isinstance(v, str) for v in vals)) and (
                prev_blank or prev is None or prev_title or any(isinstance(v, str) for v in vals)):
            return "header"
        return "data"
    return "header" if all(isinstance(v, str) for v in vals) else "data"


def _label_col(g, ncols):
    # leftmost column with mostly text in rows that also contain numbers
    best, score = 0, -1
    for c in range(min(ncols, 3)):
        s = sum(1 for row in g if isinstance(row[c], str) and row[c].strip()
                and any(_is_value(v) for v in row[c + 1:]))
        if s > score:
            best, score = c, s
    return best


def _is_quantity(v):
    """A measurement. Years are column banners, not measurements."""
    return to_number(v) is not None and not _is_year(v)


def _filled_cols(row, ncols):
    return [c for c in range(ncols) if not _empty(row[c])]


def _neighbor_filled(g, kinds, r, ncols, step):
    k = r + step
    while 0 <= k < len(kinds):
        if kinds[k] != "blank" and _filled_cols(g[k], ncols):
            return k
        k += step
    return None


def _caption_kind(text):
    s = clean_text(text)
    if _NOTE_RE.match(s) or len(s) > 90:
        return "note"
    return "section"


def _preamble(kinds, r):
    """True when r belongs to the rows above a table's data (a blank row starts a new table)."""
    saw_blank = False
    for k in range(r - 1, -1, -1):
        if kinds[k] == "blank":
            saw_blank = True
            continue
        if kinds[k] == "data":
            return saw_blank
    return True


def _following_value_cols(g, kinds, r, ncols, lc):
    cols = set()
    seen = False
    for k in range(r + 1, len(g)):
        if kinds[k] == "blank":
            if seen:
                break
            continue
        if kinds[k] in ("section", "note") and not seen:
            continue
        if kinds[k] not in ("header", "data"):
            break
        seen = True
        for c in range(ncols):
            if c != lc and not _empty(g[k][c]):
                cols.add(c)
        if kinds[k] == "data":
            break
    return cols


def _banner_header(g, kinds, r, ncols):
    """Year/text row directly above measurements: a header, not the first data row.

    The row above must not already be a year header (those following rows are data).
    """
    if kinds[r] != "data":
        return False
    nonempty = _filled_cols(g[r], ncols)
    years = []
    for c in nonempty:
        v = g[r][c]
        if _is_quantity(v):
            return False
        if _is_year(v):
            years.append(c)
        elif not isinstance(v, str):
            return False
    if len(years) < 2:
        return False
    prev = _neighbor_filled(g, kinds, r, ncols, -1)
    if prev is not None and kinds[prev] == "data":
        return False
    if prev is not None and kinds[prev] == "header" and sum(1 for c in years if _is_year(g[prev][c])) >= 2:
        return False
    nxt = _neighbor_filled(g, kinds, r, ncols, 1)
    if nxt is None:
        return False
    hit = sum(1 for c in years if _is_quantity(g[nxt][c]))
    return hit >= 2 and hit * 2 >= len(years)


def _refine_row_kinds(g, kinds, ncols, lc):
    """Captions above the real header are not headers; a year banner above measurements is.

    One non-empty cell above another header is a caption, unless that cell is the only
    data column (it is the header of a one-column table, or a parent of that column).
    """
    kinds = list(kinds)
    for r in range(len(kinds)):
        if _banner_header(g, kinds, r, ncols):
            kinds[r] = "header"
    for r in range(len(kinds)):
        if kinds[r] != "header" or not _preamble(kinds, r):
            continue
        nonempty = _filled_cols(g[r], ncols)
        if len(nonempty) != 1:
            continue
        nxt = _neighbor_filled(g, kinds, r, ncols, 1)
        if nxt is None or kinds[nxt] != "header":
            continue
        cols = _following_value_cols(g, kinds, r, ncols, lc)
        if cols and all(not _empty(g[r][c]) for c in cols) and len({clean_text(g[r][c]) for c in cols}) == 1:
            continue
        kinds[r] = _caption_kind(g[r][nonempty[0]])
    return kinds


def _data_before_next_header(kinds, r):
    for k in kinds[r + 1:]:
        if k == "data":
            return True
        if k == "header":
            return False
    return False


def _has_text_label(g, rows, lc):
    return any(isinstance(g[b][lc], str) and str(g[b][lc]).strip() for b in rows)


def _split_tables(sheet, g, indent, vmerge, hspan):
    nrows = len(g)
    ncols = max((len(r) for r in g), default=0)
    # drop fully empty trailing columns
    while ncols and all(_empty(row[ncols - 1]) for row in g):
        ncols -= 1
    if not ncols:
        return []
    # pure index column like 'Indent Level' containing small ints: detect, use as indent
    lc = _label_col(g, ncols)
    raw_kinds = [_row_kind(g, r, vmerge, ncols, lc) for r in range(nrows)]
    kinds = _refine_row_kinds(g, raw_kinds, ncols, lc)
    # first header block: rows before first data that are 'header' or the row
    # immediately preceding data with all-text values
    tables = []
    r = 0
    title_lines, pending_notes = [], []
    region_start = 0
    while r < nrows:
        if not title_lines:
            region_start = r
        # gather title/notes until header or data
        while r < nrows and kinds[r] in ("blank", "note", "section") and not _starts_header(kinds, r):
            if kinds[r] != "blank":
                title_lines.append(clean_text(g[r][lc] if not _empty(g[r][lc]) else next(v for v in g[r] if not _empty(v))))
            r += 1
        if r >= nrows:
            break
        hdr = []
        while r < nrows and kinds[r] in ("header", "blank"):
            if kinds[r] == "header":
                hdr.append(r)
            r += 1
        if not hdr and r < nrows and kinds[r] == "data":
            # data with no header -> previous all-text row?
            pass
        body = []
        while r < nrows and kinds[r] in ("data", "section", "blank", "note"):
            if kinds[r] == "section":
                nxt = next((k for k in kinds[r + 1:] if k != "blank"), None)
                # a new header starts another table; a label with no data under it is a trailing note
                if nxt == "header" or not _data_before_next_header(kinds, r):
                    break
            if kinds[r] == "note":
                # a note followed by more data is inline; otherwise stop
                nxt = next((k for k in kinds[r + 1:] if k != "blank"), None)
                if nxt not in ("data", "section"):
                    break
            body.append(r)
            r += 1
        notes = []
        while r < nrows and kinds[r] in ("note", "blank", "section") and not _starts_header(kinds, r):
            if kinds[r] == "section" and next((k for k in kinds[r + 1:] if k != "blank"), None) == "header":
                break
            if kinds[r] != "blank":
                notes.append(clean_text(" ".join(str(v) for v in dict.fromkeys(g[r]) if not _empty(v))))
            r += 1
        if any(kinds[b] == "data" for b in body):
            # The new path rewrote a caption above the header, or a year banner that was data.
            skipped = any(raw_kinds[i] != kinds[i] for i in range(region_start, r))
            tables.append(_build(sheet, g, indent, hspan, hdr, body, lc, ncols, title_lines, notes, skipped))
            title_lines = []
        else:
            title_lines += [clean_text(g[b][lc]) for b in body if not _empty(g[b][lc])]
    return tables


def _starts_header(kinds, r):
    return kinds[r] == "header"


def _build(sheet, g, indent, hspan, hdr, body, lc, ncols, title_lines, notes, title_skipped=False):
    data_rows = [b for b in body if any(not _empty(g[b][c]) for c in range(ncols))]
    # no text labels anywhere: the corner cell is a value (a year), not a row name
    lc_is_value = not _has_text_label(g, list(hdr) + list(data_rows), lc)
    data_cols = [c for c in range(ncols) if (lc_is_value or c != lc) and any(not _empty(g[b][c]) for b in data_rows)]
    meta, used_hdr = [], []
    for h in hdr:
        vals = [g[h][c] for c in data_cols]
        distinct = {clean_text(v) for v in vals if not _empty(v)}
        if len(distinct) == 1 and all(not _empty(v) for v in vals) and len(data_cols) > 2:
            meta.append(distinct.pop())
        else:
            used_hdr.append(h)
    if lc_is_value:
        label_name = "label"
    else:
        # the corner of a year banner is not a second level of the row label
        # when another header row already names that column
        def _year_banner(h):
            vals = [g[h][c] for c in data_cols if not _empty(g[h][c])]
            return len(vals) >= 2 and all(_is_year(v) for v in vals)

        parts = [(h, clean_text(g[h][lc])) for h in hdr if not _empty(g[h][lc])]
        if any(not _year_banner(h) for h, _ in parts):
            parts = [(h, t) for h, t in parts if not _year_banner(h)]
        label_name = " | ".join(dict.fromkeys(t for _, t in parts)) or "label"
    names = []
    for c in data_cols:
        parts = []
        for h in used_hdr:
            v = g[h][c]
            if _empty(v):
                continue
            t = strip_footnotes(clean_text(v if not isinstance(v, float) or not v.is_integer() else int(v)))
            if not parts or parts[-1] != t:
                parts.append(t)
        names.append(" | ".join(parts) or get_column_letter(c + 1))
    seen = {}
    for i, n in enumerate(names):
        if n in seen or n == label_name:
            seen[n] = seen.get(n, 1) + 1
            names[i] = f"{n} ({seen[n]})"
        else:
            seen[n] = 1
    # rows
    stack: list[tuple[int, str]] = []
    section = None
    recs = []
    base_ind = min((indent[b][lc] for b in data_rows), default=0)
    for b in data_rows:
        has_values = any(not _empty(g[b][c]) for c in data_cols)
        if lc_is_value:
            label = ""
        else:
            raw = g[b][lc]
            label = strip_footnotes(clean_text(raw))
        lvl = indent[b][lc] - base_ind
        if not has_values:
            # section heading: becomes top-level parent
            section = label
            stack = []
            continue
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        path = ([section] if section else []) + [s for _, s in stack] + [label]
        stack.append((lvl, label))
        rec = {"_row": b + 1, "_section": section or "", "_level": lvl,
               "_row_path": " > ".join(path), label_name: label}
        for c, n in zip(data_cols, names):
            v = g[b][c]
            num = to_number(v)
            rec[n] = num if num is not None else (
                None if _empty(v) or (isinstance(v, str) and v.strip().lower() in NA_TOKENS) else v)
        recs.append(rec)
    df = pd.DataFrame(recs)
    for n in names:  # make numeric columns numeric
        if n in df and df[n].map(lambda x: x is None or isinstance(x, float)).all():
            df[n] = pd.to_numeric(df[n])
    r0 = (hdr[0] if hdr else data_rows[0]) + 1
    r1 = data_rows[-1] + 1
    rng = f"{get_column_letter(min([lc] + data_cols) + 1)}{r0}:{get_column_letter(max(data_cols + [lc]) + 1)}{r1}"
    return Table(sheet, rng, " / ".join(title_lines), notes, meta, df,
                 [h + 1 for h in hdr], label_name, bool(title_skipped))


def summarize(tables: list[Table], max_chars: int = 4000, sample_rows: int = 3) -> str:
    out = []
    for i, t in enumerate(tables):
        cols = [c for c in t.df.columns if not c.startswith("_")]
        s = [f"## Table {i} sheet={t.sheet} range={t.cell_range} rows={len(t.df)}"]
        if t.title:
            s.append(f"title: {t.title[:200]}")
        if t.meta:
            s.append(f"meta: {'; '.join(t.meta)}")
        s.append("columns: " + "; ".join(cols))
        if "_row_path" in t.df:
            paths = t.df["_row_path"].tolist()
            s.append("rows: " + "; ".join(paths))
        if t.notes:
            s.append("notes: " + " ".join(t.notes)[:400])
        out.append("\n".join(s))
    txt = "\n\n".join(out)
    if len(txt) > max_chars:
        txt = txt[: max_chars - 15] + "\n…[truncated]"
    return txt
