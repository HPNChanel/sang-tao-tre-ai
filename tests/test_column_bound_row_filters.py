from test_derived_metric_extrema import run


def test_compound_predicates_bind_each_column_and_parse_thousands(tmp_path):
    rows = [["Year", "Rate", "Count"], [2001, 3, 10000], [2002, 7, 12000], [2003, 9, 20000]]
    q = "Identify the year when Rate exceeded 5 and Count was below 15,000. Answer with only the year."
    assert run(tmp_path, rows, q) == "2002"


def test_annual_comparison_ignores_quarter_rows(tmp_path):
    rows = [["Period", "Exports", "Imports"], ["2001", 8, 3], ["Q1", 7, 1], ["2002", 4, 6], ["2003", 8, 7]]
    q = "Using annual rows labelled with a year, in how many years were Exports higher than Imports? Answer with only the integer."
    assert run(tmp_path, rows, q) == "2"


def test_identifier_width_is_a_filter_not_a_measure(tmp_path):
    rows = [["Industry", "Sector code", "Cases"], ["Alpha", 123, 7], ["Beta", 1234, 8], ["Gamma", 5678, 3], ["Delta", 9876, 9]]
    q = "How many industries with a 4-digit Sector code have Cases above 5? Answer with only the integer."
    assert run(tmp_path, rows, q) == "2"
