"""The local harness may run batches concurrently without changing what it records."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import qwen_campaign as C  # noqa: E402

FAKE_RUN = '''import csv, json
from pathlib import Path
def answer_one(question, *positional, **keywords):
    return "a-" + str(question["id"])
def main(argv):
    args = dict(zip(argv[::2], argv[1::2]))
    queries = json.loads(Path(args["--queries"]).read_text(encoding="utf-8"))
    with open(args["--output"], "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "answer"])
        for question in queries:
            writer.writerow([question["id"], answer_one(question)])
    if any(question["query"] == "FAIL" for question in queries):
        raise SystemExit(3)
'''
FAKE_CLIENT = "MAX_TOKENS = 600\ndef llm_plan(*a, **k):\n    raise RuntimeError\ndef _post(body, timeout):\n    raise RuntimeError\n"
SHA = "a" * 40


def make_source(root):
    files = {"run.py": FAKE_RUN, "src/__init__.py": "", "src/llm_client.py": FAKE_CLIENT}
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    manifest = {"source_commit": SHA,
                "files": {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in files}}
    (root / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")


def make_dataset(root, fail_id=None):
    (root / "xlsx_files").mkdir(parents=True)
    queries = []
    for i in range(60):
        name = f"w{i // 5}.xlsx"
        (root / "xlsx_files" / name).write_bytes(name.encode())
        queries.append({"id": f"q{i:03}", "query": "FAIL" if f"q{i:03}" == fail_id else f"question {i}",
                        "file_name": name})
    (root / "queries.json").write_text(json.dumps(queries), encoding="utf-8")


def run(tmp_path, dataset, label, parallel):
    output = tmp_path / label
    command = [sys.executable, str(SCRIPTS / "run_local_evaluation.py"), "--control", str(tmp_path / "control"),
               "--source", str(tmp_path / "source"), "--source-sha", SHA, "--dataset", str(dataset),
               "--output", str(output), "--no-key", "--parallel", str(parallel)]
    return subprocess.run(command, capture_output=True, text=True), output


def setup(tmp_path, fail_id=None):
    (tmp_path / "source").mkdir()
    make_source(tmp_path / "source")
    make_dataset(tmp_path / "data", fail_id)
    C.Ledger.create(tmp_path / "control" / "ledger.json", "parallel-test")


def test_parallel_batches_match_sequential_receipts(tmp_path):
    setup(tmp_path)
    one, first = run(tmp_path, tmp_path / "data", "sequential", 1)
    three, second = run(tmp_path, tmp_path / "data", "parallel", 3)
    assert one.returncode == 0 and three.returncode == 0, one.stderr + three.stderr
    with (second / "submission.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["id", "answer"] and [r[0] for r in rows[1:]] == [f"q{i:03}" for i in range(60)]
    assert (first / "submission.csv").read_bytes() == (second / "submission.csv").read_bytes()
    a, b = (json.loads((x / "receipt.json").read_text()) for x in (first, second))
    assert [x["index"] for x in b["batches"]] == [0, 1, 2]
    assert [x["invocation"] for x in a["batches"]] == [x["invocation"] for x in b["batches"]]
    assert (a["parallel_batches"], b["parallel_batches"]) == (1, 3)
    assert len(C.Ledger(tmp_path / "control" / "ledger.json").state["invocations"]) == 3
    assert not (tmp_path / "control" / "ledger.json.lock").exists()


def test_failed_batch_stops_sequential_run_before_later_batches(tmp_path):
    setup(tmp_path, fail_id="q025")
    result, output = run(tmp_path, tmp_path / "data", "sequential", 1)
    assert result.returncode != 0 and "evaluation stopped" in result.stderr
    assert (output / "batch-000" / "receipt.json").exists() and (output / "batch-001" / "receipt.json").exists()
    assert not (output / "batch-002").exists() and not (output / "receipt.json").exists()


def test_failed_parallel_batch_withholds_final_receipt(tmp_path):
    setup(tmp_path, fail_id="q025")
    result, output = run(tmp_path, tmp_path / "data", "parallel", 3)
    assert result.returncode != 0 and "evaluation stopped" in result.stderr
    assert json.loads((output / "batch-001" / "receipt.json").read_text())["exit_code"] == 3
    assert not (output / "receipt.json").exists() and not (output / "submission.csv").exists()
    assert not (tmp_path / "control" / "ledger.json.lock").exists()
