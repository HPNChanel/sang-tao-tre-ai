# Table QA plan schema (v1)
Machine-readable: `src/plan_schema.json`. Produced by the rule planner (`src/planner.py`) and — later —
by an optional LLM fallback, which must return exactly this JSON (no prose). Executed by `src/executor.py`.

```json
{"version":1,
 "table":  {"index":0, "sheet":null, "title_hint":null, "stack_sheets":false},
 "rows":   {"paths":["Women > Civilian labor force"], "scope":null, "range":null,
            "leaf_only":false, "exclude_subtotals":true,
            "where":[{"column":"Score","op":">=","value":70}]},
 "columns":["Some college or associate degree | Some college, no degree"],
 "op":"diff",
 "args":{"abs":true},
 "format":{"kind":"int","decimals":null,"count":null,"item_kind":null,"sep":", ","order":[]}}
```

Ops (value = cells rows×columns after filters):
| op | meaning | key args |
|---|---|---|
| lookup | single cell (or the row's only numeric value) | – |
| values | list of cells in question order (rows then columns) | – |
| sum / mean / max / min | aggregate cells (subtotals excluded when aggregating rows) | – |
| diff | v1 − v2 | abs, signed_order |
| compare | v1 cmp v2 → Yes/No | cmp |
| ratio | v1 / v2 | percent |
| share | row / its mentioned ancestor (or parent) | percent |
| argmax / argmin | label of best row / column / sheet; ties -> all (table order) or first | over, abs, ties |
| rank | names ordered by value | order, names |
| nth | n-th row ordered by `by` (`age` = from dates), return column/label | n, order, by, return |
| topk_agg | take top-k rows by `by`, then agg over return column | k, order, by, agg, return |
| count | number of rows after filters (leaf_only for "most detailed") | – |

Rules: row/column names may be fuzzy; executor resolves them against the parsed table.
Conventions (hidden sheets, hidden rows, dates, age-at-reference-date, Total sub-column) live in `src/conventions.py`.
