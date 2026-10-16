from test_derived_metric_extrema import run


def test_negative_qualifier_binds_within_each_operand(tmp_path):
    rows = [["Activity", "Paid tutors", "Not paid tutors", "Paid mentors"], ["Reading", 99, 7, 3], ["Resting", 1, 2, 4]]
    q = "Compare Reading for not paid tutors and paid mentors. Who spends more, and by how many hours? Answer as `group, difference`, where group is either `not paid tutors` or `paid mentors`, using exactly two decimal places."
    assert run(tmp_path, rows, q) == "not paid tutors, 4.00"
