"""Detect subtotal rows in flat tables (no indentation info).

Rule (general): processing rows bottom-up within a section, row i is a subtotal
if a contiguous run of >=2 following "units" sums to it on most numeric columns,
where a unit is either a plain row or an already-detected subtotal together with
its whole span (so nested totals are skipped, not double counted).
Label cues ('Total', 'All ...', '... and over', 'less than') are recorded
separately in `_total_cue` and used only as a tie-breaker.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

_CUE = re.compile(r"^(total|all\b)|\btotal\b|\band over\b|\bor more\b|\band older\b", re.I)


def _matches(target, s, nterms, rtol=0.002):
    ok = 0
    cmp = 0
    for t, v in zip(target, s):
        if np.isnan(t) or np.isnan(v):
            continue
        cmp += 1
        tol = max(0.51 * nterms, abs(t) * rtol) if float(t).is_integer() else max(0.051 * nterms, abs(t) * rtol)
        if abs(t - v) <= tol:
            ok += 1
    return cmp, ok


def detect_subtotals(df: pd.DataFrame, value_cols=None, min_frac=0.6) -> pd.DataFrame:
    df = df.copy()
    if value_cols is None:
        value_cols = [c for c in df.columns if not str(c).startswith("_") and pd.api.types.is_numeric_dtype(df[c])]
    n = len(df)
    span_end = list(range(n))  # last row index covered by row i (itself if leaf)
    parent = [None] * n
    is_sub = [False] * n
    sec = df["_section"].tolist() if "_section" in df else [""] * n
    labels = df["_row_path"].astype(str).tolist() if "_row_path" in df else [""] * n
    cue = [bool(_CUE.search(l.split(" > ")[-1])) for l in labels]
    X = df[value_cols].to_numpy(dtype=float) if value_cols else np.zeros((n, 0))
    for i in range(n - 2, -1, -1):
        target = X[i]
        if np.all(np.isnan(target)):
            continue
        acc = np.zeros(len(value_cols)); nan_mask = np.zeros(len(value_cols), bool)
        j, units, best = i + 1, [], None
        while j < n and sec[j] == sec[i]:
            row = X[j]
            nan_mask |= np.isnan(row)
            acc = acc + np.nan_to_num(row)
            units.append(j)
            j = span_end[j] + 1
            if len(units) >= 2:
                s = np.where(nan_mask, np.nan, acc)
                cmp, ok = _matches(target, s, len(units))
                need = max(1, int(np.ceil(min_frac * cmp)))
                if cmp and ok >= need and ok >= min(2, cmp):
                    best = (units[:], j - 1)
                    break
            # stop early if every comparable column already exceeds target (all-positive data)
            if np.all((acc > np.abs(target) * 1.01 + 1) | np.isnan(target)) and np.all(np.nan_to_num(X[i:j]) >= 0):
                break
        if best and not cue[i]:
            # identities like a = b + (a - b) (e.g. balance rows) are not subtotals:
            # without a label cue, reject runs containing negative values
            kids = X[best[0]]
            if np.nanmin(kids) < 0:
                best = None
        if best:
            is_sub[i] = True
            span_end[i] = best[1]
            for u in best[0]:
                parent[u] = i
    df["_is_subtotal"] = is_sub
    df["_total_cue"] = cue
    df["_parent_row"] = [None if p is None else int(df["_row"].iloc[p]) if "_row" in df else p for p in parent]
    # rebuild paths with detected parents (only where no indentation info existed)
    if "_row_path" in df and "_level" in df and (df["_level"] == 0).all():
        idx = {k: v for k, v in enumerate(labels)}
        leaf = [l.split(" > ")[-1] for l in labels]
        new = []
        for k in range(n):
            chain, p = [leaf[k]], parent[k]
            while p is not None:
                chain.append(leaf[p]); p = parent[p]
            prefix = [sec[k]] if sec[k] else []
            new.append(" > ".join(prefix + chain[::-1]))
        df["_row_path"] = new
        df["_level"] = [len(p.split(" > ")) - (2 if sec[k] else 1) for k, p in enumerate(new)]
    return df
