"""Single place for data-handling conventions. Flip here, nowhere else."""
SKIP_HIDDEN_SHEETS = True      # hidden/veryHidden sheets are ignored
INCLUDE_HIDDEN_ROWS = True     # hidden rows still count as data
INCLUDE_HIDDEN_COLS = True     # hidden columns still count as data
DATE_OUTPUT_FORMAT = "%Y-%m-%d"  # dates in answers (cells may display DD/MM/YYYY)
EVALUATE_UNCACHED_FORMULAS = True  # compute =SUM(...), =H1*B9 when no cached value
PREFER_CACHED_FORMULA_VALUES = True  # if a cached value exists, trust it
ROUND_HALF_UP = True           # 2.345 -> 2.35 (not banker's rounding)

# --- Conventions implied by organizers' sample answers (validated on the 14 DEV ids only,
# --- never on holdout ids). They may change after round-2 data (16/10). ---

# (a) Age-based ordering ("youngest", "oldest", "second-oldest"): when a row has both a birth
#     date and a reference/event date (e.g. admission), age = reference_date - birth_date
#     ("age on their admission date"), not age as of today. False -> order by birth date only.
AGE_AT_ROW_REFERENCE_DATE = True

# (b) When several columns differ only by time period (year, quarter, month, fiscal vs calendar),
#     pick the column whose period tokens are mentioned in the question.
MATCH_COLUMN_TIME_PERIOD = True

# (c) When several columns share the matched name (e.g. 'Worked 1 to 34 hours | Total' and
#     '... | For economic reasons'), prefer the 'Total' child column.
PREFER_TOTAL_SUBCOLUMN = True

# Sum/count over rows exclude detected subtotal rows and 'Total'/'Grand total' rows.
EXCLUDE_SUBTOTALS_IN_AGGREGATES = True

# Ties in max/min/argmax/argmin: return ALL tied labels (table order, joined by the format's list
# separator) unless the wording/format asks for exactly one result (singular 'which <noun>',
# 'the ...', name/int format with no count) -> keep the first and log the tie to stderr.
TIES_RETURN_ALL = True

# Rule planner confidence below this -> code_planner.plan returns None (LLM fallback) instead of guessing.
PLAN_CONFIDENCE_THRESHOLD = 0.5
# Post-exec: blank the answer (-> fallback) if its shape contradicts the expected answer type.
POST_EXEC_TYPE_CHECK = True
