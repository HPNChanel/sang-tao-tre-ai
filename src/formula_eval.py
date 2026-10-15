"""Tiny safe evaluator for common Excel formulas (no cached values case).
Supports + - * / ^, parentheses, cell refs (A1, $A$1, Sheet!A1), ranges in
SUM/AVERAGE/MIN/MAX/COUNT/ROUND/ABS. Returns None when unsupported."""
from __future__ import annotations

import math
import re

from openpyxl.utils import column_index_from_string, range_boundaries

_REF = r"(?:(?:'[^']+'|[A-Za-z0-9_]+)!)?\$?[A-Z]{1,3}\$?\d+"
_RANGE = re.compile(rf"({_REF}):(\$?[A-Z]{{1,3}}\$?\d+)")
_CELL = re.compile(_REF)
_FUNCS = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "ROUND", "ABS"}


class FormulaEvaluator:
    def __init__(self, wb_formula, wb_values):
        self.wf, self.wv = wb_formula, wb_values
        self.cache, self.stack = {}, set()

    def value(self, sheet, coord):
        key = (sheet, coord.replace("$", "").upper())
        if key in self.cache:
            return self.cache[key]
        if key in self.stack:
            return None
        try:
            cv = self.wv[sheet][key[1]].value
            fv = self.wf[sheet][key[1]].value
        except (KeyError, ValueError):
            return None
        if isinstance(fv, str) and fv.startswith("="):
            if cv is None:
                self.stack.add(key)
                try:
                    cv = self.evaluate(sheet, fv)
                finally:
                    self.stack.discard(key)
        else:
            cv = fv if cv is None else cv
        self.cache[key] = cv
        return cv

    def _split(self, sheet, ref):
        if "!" in ref:
            s, c = ref.rsplit("!", 1)
            return s.strip("'"), c
        return sheet, ref

    def _range_vals(self, sheet, a, b):
        s, a = self._split(sheet, a)
        c1, r1, c2, r2 = range_boundaries(f"{a}:{b}".replace("$", ""))
        from openpyxl.utils import get_column_letter
        out = []
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                v = self.value(s, f"{get_column_letter(c)}{r}")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append(float(v))
        return out

    def evaluate(self, sheet, formula):
        expr = formula[1:].strip()
        names = {}

        def rng(m):
            k = f"_r{len(names)}"
            names[k] = self._range_vals(sheet, m.group(1), m.group(2))
            return k
        expr = _RANGE.sub(rng, expr)

        def cell(m):
            k = f"_c{len(names)}"
            s, c = self._split(sheet, m.group(0))
            v = self.value(s, c)
            names[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
            return k
        expr = _CELL.sub(cell, expr)
        for fn in re.findall(r"([A-Z]+)\(", expr):
            if fn not in _FUNCS:
                return None
        expr = expr.replace("^", "**").replace(";", ",")
        if not re.fullmatch(r"[\w\s.+\-*/(),]*", expr):
            return None

        def flat(args):
            o = []
            for a in args:
                o.extend(a if isinstance(a, list) else [a])
            return o
        env = {"SUM": lambda *a: sum(flat(a)), "AVERAGE": lambda *a: (lambda f: sum(f) / len(f) if f else None)(flat(a)),
               "MIN": lambda *a: min(flat(a)), "MAX": lambda *a: max(flat(a)), "COUNT": lambda *a: len(flat(a)),
               "ROUND": lambda x, n=0: float(round(x, int(n))), "ABS": abs, "__builtins__": {}}
        env.update(names)
        try:
            v = eval(expr, env)  # restricted: only names above, validated charset
        except Exception:
            return None
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return v
