# Table QA plan schema (v1)

`format.prefix` and `format.suffix` are optional literal output decorations.
Their default is the empty string, so existing v1 plans keep their behavior.
Use them only when the question explicitly requests that literal text, such as
`suffix: "%"` or `suffix: " pp"`. They do not perform percentage arithmetic;
that remains the operation's responsibility. Numeric type checks still validate
the computed payload after requiring the exact decorations.

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
| select | labels of rows satisfying all predicates | – |
| spread | maximum minus minimum of selected numeric cells | – |
| percent_change | `(v2-v1)/v1*100`, v1 is the baseline | decrease reverses the numerator |
| winner_diff | winning operand name and absolute gap; ties abstain | two names aligned with the two operands |

Composed extrema first compute one value per row with `args.metric`: `sum`,
`difference` (first column minus second), or `previous_difference` (current
minus the previous consecutive year, sorted by the row label). Missing operands
do not become zero. `reverse_metric` changes the sign. `over="column_groups"`
instead compares named two-column pairs from `metric_groups` at one exact row.

`argmax`/`argmin` also support `over="sections"` for a named leaf repeated across
section groups. `include_value=true` returns the winning label and its numeric
value; `return` can select a different column at that row. A heterogeneous list
uses `format.item_kinds`, one kind per element; its length must match the result.

`rows.exclude_paths` excludes exact named rows. `rows.label_kind="year"` selects
four-digit annual labels. A `where` predicate can set `value_column` to compare
two columns at each row, or use `op="digits=="` for an identifier's digit count.
`compare` may use `args.value` as an explicit scalar right operand.

Rules: row/column names may be fuzzy; executor resolves them against the parsed table.
Conventions (hidden sheets, hidden rows, dates, age-at-reference-date, Total sub-column) live in `src/conventions.py`.
