"""Contest CLI: six arguments, CSV shape, stderr log, untouched input.

Docker is not required. tests/docker_smoke.sh is the image build and run.
"""
import csv
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "test-not-a-real-key"
COUNT_Q = "How many people scored at least 60? Answer with only the integer."


def _public_data_dir():
    env = os.environ.get("AIPROJ_DATA_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    candidate = ROOT / "data" / "Public data 1"
    if candidate.is_dir():
        return candidate
    return None


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


def _snapshot(root: Path):
    found = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        found[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _invoke(xlsx_dir, queries, output, key):
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    cmd = [
        sys.executable,
        str(ROOT / "run.py"),
        "--xlsx-dir",
        str(xlsx_dir),
        "--queries",
        str(queries),
        "--output",
        str(output),
        "--openrouter-key",
        key,
        "--openrouter-model",
        "qwen/qwen3.7-flash",
        "--openrouter-base-url",
        "https://openrouter.ai/api/v1",
    ]
    return subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=True, text=True)


def _assert_csv(path: Path, expected_ids):
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0] == ["id", "answer"]
    assert all(len(row) == 2 for row in rows)
    ids = [row[0] for row in rows[1:]]
    assert ids == expected_ids
    assert len(ids) == len(set(ids))
    assert not (path.parent / "run_log.jsonl").exists()


def test_six_args_csv_stderr_and_untouched_input(tmp_path):
    pack = tmp_path / "input"
    _workbook(pack / "xlsx" / "people.xlsx")
    queries = pack / "queries.json"
    queries.write_text(
        json.dumps(
            [
                {"id": "c1", "file_name": "people.xlsx", "query": COUNT_Q},
                {"id": "c1", "file_name": "people.xlsx", "query": "duplicate must be dropped"},
                {"id": "c2", "file_name": "people.xlsx", "query": COUNT_Q},
            ]
        ),
        encoding="utf-8",
    )
    before = _snapshot(pack)
    out_key = tmp_path / "with-key" / "submission.csv"
    out_plain = tmp_path / "no-key" / "submission.csv"
    keyed = _invoke(pack / "xlsx", queries, out_key, FAKE_KEY)
    plain = _invoke(pack / "xlsx", queries, out_plain, "")
    assert keyed.returncode == 0, keyed.stderr
    assert plain.returncode == 0, plain.stderr
    assert _snapshot(pack) == before
    _assert_csv(out_key, ["c1", "c2"])
    _assert_csv(out_plain, ["c1", "c2"])
    assert out_key.read_bytes() == out_plain.read_bytes()
    assert "c1" in keyed.stderr
    assert not (out_key.parent / "run_log.jsonl").exists()


def test_public_data_six_args(tmp_path):
    data = _public_data_dir()
    if data is None or not (data / "queries.json").is_file():
        pytest.skip("public data not present")
    before = _snapshot(data)
    out = tmp_path / "submission.csv"
    proc = _invoke(data, data / "queries.json", out, FAKE_KEY)
    assert proc.returncode == 0, proc.stderr
    assert _snapshot(data) == before
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["id", "answer"]
    ids = [row[0] for row in rows[1:]]
    assert ids and len(ids) == len(set(ids))
    assert all(len(row) == 2 for row in rows)
    assert not (out.parent / "run_log.jsonl").exists()


def test_docker_smoke_script_present():
    script = ROOT / "tests" / "docker_smoke.sh"
    text = script.read_text(encoding="utf-8")
    assert "docker build --platform=linux/amd64" in text
    assert "--xlsx-dir" in text and "--openrouter-key" in text
