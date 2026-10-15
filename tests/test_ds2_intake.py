"""Intake and answering on a workbook generated in the test.

No network, no contest pack, no checked-in spreadsheet.
"""
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run  # noqa: E402

COUNT_Q = "How many people scored at least 60? Answer with only the integer."
OLDEST_VI = "Người lớn tuổi nhất được bao nhiêu điểm? Chỉ trả lời bằng số nguyên."


def _workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Birth", "Admission", "Score"])
    for name, birth, admission, score in (
        ("An", 2000, 2020, 50),
        ("Binh", 1990, 2024, 70),
        ("Chi", 1995, 2015, 90),
    ):
        ws.append([name, dt.datetime(birth, 1, 1), dt.datetime(admission, 1, 1), score])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _write_queries(data: Path, queries) -> None:
    (data / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False), encoding="utf-8"
    )


def _run(data: Path, out: Path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert run.main(["--data", str(data), "--out", str(out)]) == 0
    with open(out, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def test_case_and_extension_still_answer(tmp_path, monkeypatch):
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "Records.XLSX")
    _write_queries(
        data,
        [
            {"id": "c1", "file_name": "records.xlsx", "query": COUNT_Q, "note": "ignore"},
            {"id": "c2", "file_name": "records.XLS", "query": COUNT_Q, "unused": {"n": 1}},
            {"id": "c3", "file_name": "RECORDS", "query": COUNT_Q},
        ],
    )
    rows = _run(data, tmp_path / "out" / "submission.csv", monkeypatch)
    assert [r[0] for r in rows[1:]] == ["c1", "c2", "c3"]
    assert [r[1] for r in rows[1:]] == ["2", "2", "2"]
    assert all("\n" not in r[1] and len(r[1]) < 20 for r in rows[1:])


def test_workbook_beside_queries_case_differs(tmp_path, monkeypatch):
    data = tmp_path / "pack"
    data.mkdir()
    _workbook(data / "People.xlsm")
    _write_queries(data, [{"id": "b1", "file_name": "people.xlsx", "query": COUNT_Q}])
    rows = _run(data, tmp_path / "submission.csv", monkeypatch)
    assert rows == [["id", "answer"], ["b1", "2"]]


def test_duplicate_ids_once_first_query_wins(tmp_path, monkeypatch):
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "book.xlsx")
    _write_queries(
        data,
        [
            {"id": "q1", "file_name": "book.xlsx", "query": COUNT_Q},
            {"id": "q2", "file_name": "book.xlsx", "query": COUNT_Q},
            {"id": "q1", "file_name": "missing.xlsx", "query": "second copy"},
        ],
    )
    rows = _run(data, tmp_path / "submission.csv", monkeypatch)
    assert rows == [["id", "answer"], ["q1", "2"], ["q2", "2"]]


def test_sample_order_and_duplicate_sample_ids(tmp_path, monkeypatch):
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "book.xlsx")
    _write_queries(
        data,
        [
            {"id": "q1", "file_name": "book.xlsx", "query": COUNT_Q, "gold": "leak"},
            {"id": "q2", "file_name": "book.xlsx", "query": OLDEST_VI},
        ],
    )
    sample = data / "sample_submission.csv"
    with open(sample, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "answer", "extra"])
        w.writerow(["q2", "placeholder", "x"])
        w.writerow(["q1", "", "y"])
        w.writerow(["q1", "again", "z"])
    rows = _run(data, tmp_path / "submission.csv", monkeypatch)
    assert rows[0] == ["id", "answer", "extra"]
    assert [r[0] for r in rows[1:]] == ["q2", "q1"]
    assert [r[1] for r in rows[1:]] == ["70", "2"]
    assert [r[2] for r in rows[1:]] == ["", ""]
    text = (tmp_path / "submission.csv").read_text(encoding="utf-8")
    assert "leak" not in text and "placeholder" not in text
    assert OLDEST_VI not in text
    assert all(len(r[1]) < 40 and "\n" not in r[1] for r in rows[1:])


def test_missing_file_emits_blank_row(tmp_path, monkeypatch):
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "book.xlsx")
    _write_queries(
        data,
        [
            {"id": "ok", "file_name": "book.xlsx", "query": COUNT_Q},
            {"id": "gone", "file_name": "no-such-workbook.xlsx", "query": COUNT_Q},
        ],
    )
    rows = _run(data, tmp_path / "submission.csv", monkeypatch)
    assert rows == [["id", "answer"], ["ok", "2"], ["gone", ""]]


def test_empty_queries_header_only(tmp_path, monkeypatch):
    data = tmp_path / "pack"
    data.mkdir()
    _write_queries(data, [])
    with open(data / "sample_submission.csv", "w", newline="", encoding="utf-8-sig") as f:
        f.write("id,answer\n")
    rows = _run(data, tmp_path / "submission.csv", monkeypatch)
    assert rows == [["id", "answer"]]


def test_folder_and_zip_same_answers(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = tmp_path / "pack"
    _workbook(data / "xlsx_files" / "book.xlsx")
    _write_queries(
        data,
        [
            {"id": "z1", "file_name": "book.xlsx", "query": COUNT_Q},
            {"id": "z2", "file_name": "book.xlsx", "query": OLDEST_VI},
        ],
    )
    with open(data / "sample_submission.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "answer"])
        w.writerow(["z2", ""])
        w.writerow(["z1", ""])
    folder_out = tmp_path / "folder"
    zip_out = tmp_path / "zipped"
    script = ROOT / "scripts" / "dry_run_dataset2.sh"
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    subprocess.run([str(script), str(data), str(folder_out)], check=True, env=env, cwd=str(ROOT))
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for path in data.rglob("*"):
            if path.is_file():
                zf.write(path, ("bundle/" + path.relative_to(data).as_posix()))
    subprocess.run([str(script), str(zpath), str(zip_out)], check=True, env=env, cwd=str(ROOT))
    assert (folder_out / "submission.csv").read_bytes() == (zip_out / "submission.csv").read_bytes()
    with open(folder_out / "submission.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows == [["id", "answer"], ["z2", "70"], ["z1", "2"]]
