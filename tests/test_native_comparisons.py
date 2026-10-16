from test_derived_metric_extrema import run


def test_chinese_comparison_grounds_labels_without_spaces(tmp_path):
    rows = [["地区", "早班", "晚班"], ["甲区", 15, 8], ["非甲区", 22, 12]]
    q = "对于晚班，甲区比非甲区低多少个百分点？只回答数字，保留一位小数。"
    assert run(tmp_path, rows, q) == "4.0"


def test_korean_percent_change_uses_baseline_and_not_stored_change(tmp_path):
    rows = [["Period", "Requests | number", "Requests | percent change from previous year"], ["2011/2012", 400, -1], ["2018/2019", 300, 7]]
    q = "2011/2012년 대비 2018/2019년에 Requests 수는 몇 퍼센트 감소했습니까? 소수점 둘째 자리까지 숫자만 답하십시오."
    assert run(tmp_path, rows, q) == "25.00"
