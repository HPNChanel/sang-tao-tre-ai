from test_derived_metric_extrema import run
from src.vi import is_vietnamese, translate


def test_native_labels_survive_operation_translation(tmp_path):
    rows = [["Nhóm", "nữ", "nam"], ["Có bằng đại học", 12, 20], ["Có học trung học", 7, 10]]
    q = "Ở nhóm Có bằng đại học, của nữ bằng bao nhiêu phần trăm so với nam? Làm tròn đến một chữ số thập phân, chỉ trả lời bằng số."
    assert run(tmp_path, rows, q) == "60.0"


def test_french_diacritics_are_not_vietnamese_detection():
    assert not is_vietnamese("Parmi les élèves, répondez uniquement par le nombre.")
    translated, _ = translate("của nữ cao nhất", entities=["nữ"])
    assert "nữ" in translated and "women" not in translated


def test_entity_protection_does_not_replace_format_words():
    translated, _ = translate("Điểm của Nguyên là bao nhiêu? Chỉ trả lời bằng số nguyên.", entities=["Nguyên"])
    assert "Nguyên" in translated
    assert "Answer with only the integer" in translated
