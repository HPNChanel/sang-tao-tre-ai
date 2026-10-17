"""Question-ranked, complete JSON pieces within the serialized request limit."""
import datetime as dt
import json
import math
import re
import unicodedata

from . import llm_client


def _fold(value):
    return " ".join(re.findall(r"\w+", "".join(c for c in unicodedata.normalize("NFKD", str(value).lower())
                                              if not unicodedata.combining(c)).replace("đ", "d")))


def _score(text, question):
    folded = _fold(text)
    if not folded:
        return 0
    return 12 * (f" {folded} " in f" {question} ") + len(set(folded.split()) & set(question.split()))


def _value(value):
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def build_context(query, tables, schema):
    """Keep question and schema intact; omit only whole, explicitly counted pieces.

    The original table index and all selected table headers remain exact. Row
    candidates favor named entities/ancestors and keep representative ends and
    subtotals. Python still executes predicates over the complete workbook.
    """
    context = {"tables": [], "tables_total": len(tables), "tables_omitted": len(tables),
               "notice": "Samples are incomplete when rows_omitted > 0. Use predicates over the full table; never infer absence from a sample."}

    def fits():
        # Leave room for bounded validator feedback on a repair attempt.
        return len(llm_client.request_body(query, _dump(context), schema)) <= llm_client.MAX_BODY_BYTES - 1024

    if not fits():
        raise ValueError("question and schema exceed request capacity")
    question = _fold(query)
    ranked = sorted(enumerate(tables), key=lambda it: (-5 * _score(it[1].sheet, question)
                    -3 * _score(it[1].title, question)
                    -sum(_score(c, question) for c in it[1].df.columns if not str(c).startswith("_")), it[0]))
    candidates = []
    for index, table in ranked:
        columns = [str(c) for c in table.df.columns if not str(c).startswith("_")]
        entry = {"index": index, "sheet": table.sheet, "range": table.cell_range,
                 "title": table.title, "label_column": table.label_col, "columns": columns,
                 "meta": [], "notes": [], "metadata_omitted": len(table.meta) + len(table.notes),
                 "rows_total": len(table.df), "rows_omitted": len(table.df), "rows": []}
        context["tables"].append(entry)
        context["tables_omitted"] -= 1
        if not fits():
            context["tables"].pop(); context["tables_omitted"] += 1
            continue
        for key, values in (("meta", table.meta), ("notes", table.notes)):
            for value in values:
                entry[key].append(str(value)); entry["metadata_omitted"] -= 1
                if not fits():
                    entry[key].pop(); entry["metadata_omitted"] += 1
        ranked_rows = []
        for position, (_, row) in enumerate(table.df.iterrows()):
            path = str(row.get("_row_path", row.get(table.label_col, "")))
            rank = _score(path, question) * 10 + _score(row.get("_section", ""), question) * 3
            if position in (0, len(table.df) - 1):
                rank += 2
            if bool(row.get("_is_subtotal", False)):
                rank += 1
            ranked_rows.append((-rank, position))
        # Bound rendering work independently from workbook size. A matched row
        # at the end of a large table outranks the representative first row.
        candidates.append((entry, table, sorted(ranked_rows)[:128]))
    # Round-robin prevents a large early table consuming every row sample.
    for rank in range(128):
        for entry, table, rows in candidates:
            if rank >= len(rows):
                continue
            position = rows[rank][1]
            row = table.df.iloc[position]
            item = {"path": str(row.get("_row_path", row.get(table.label_col, ""))),
                    "section": str(row.get("_section", "")), "subtotal": bool(row.get("_is_subtotal", False)),
                    "values": {c: _value(row[c]) for c in entry["columns"]}}
            entry["rows"].append(item); entry["rows_omitted"] -= 1
            if not fits():
                entry["rows"].pop(); entry["rows_omitted"] += 1
    return _dump(context)
