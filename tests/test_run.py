import csv, json, os, subprocess, sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]


def round1_data_dir():
    """Round-1 public data: $AIPROJ_DATA_DIR, otherwise <repo>/data/Public data 1."""
    env = os.environ.get("AIPROJ_DATA_DIR")
    if env:
        return Path(env)
    return ROOT / "data" / "Public data 1"


def test_cli_end_to_end(tmp_path, monkeypatch):
    data = round1_data_dir()
    if not data.is_dir():
        pytest.skip("round-1 data missing; set AIPROJ_DATA_DIR or place it at data/Public data 1")
    out = tmp_path / "with space" / "submission.csv"
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    subprocess.run([sys.executable, str(ROOT / "run.py"), "--data", str(data), "--out", str(out)],
                   check=True, env=env, cwd="/")
    rows = list(csv.reader(open(out, encoding="utf-8")))
    sample = list(csv.reader(open(data / "sample_submission.csv", encoding="utf-8-sig")))
    assert rows[0] == sample[0]
    assert [r[0] for r in rows[1:]] == [r[0] for r in sample[1:]]
    logs = [json.loads(l) for l in open(out.parent / "run_log.jsonl")]
    assert len(logs) == 20
    for l in logs:
        assert set(l) == {"id", "tokens", "cost_usd"} and l["tokens"] == 0 and l["cost_usd"] == 0


def test_budget_cap():
    sys.path.insert(0, str(ROOT))
    from src.llm_client import Budget, COST_CAP
    b = Budget()
    assert COST_CAP < 0.10
    while b.allow(4000):
        b.charge(0, 0, 4000)
    assert b.spent <= COST_CAP
