from test_derived_metric_extrema import run
from src.xlsx_tables import to_number


def test_approximate_number_does_not_turn_measure_into_text(tmp_path):
    assert to_number("~0") == 0
    assert to_number("≈ 1.5") == 1.5
    assert to_number("<5") is None
    rows = [["Place", "Sales"], ["Hill", "~0"], ["Bay", 8], ["Lake", 6]]
    assert run(tmp_path, rows, "Which place has the highest Sales? Answer with only the name.") == "Bay"
