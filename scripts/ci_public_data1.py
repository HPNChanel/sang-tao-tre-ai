"""Download only pinned public files, verify Docker I/O, and measure peak RAM.

No contest data is stored in Git, uploaded as an artifact, or baked into the
image. An unmodified ENTRYPOINT run must match an instrumented run byte-for-byte.
Sample answers are compared as exact strings; this is not the hidden judge.
"""
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
import urllib.request
import uuid

from ci_docker_smoke import IMAGE, ROOT, snapshot
from ci_memory_probe import PREFIX

MANIFEST = ROOT / ".github" / "public-data1-manifest.json"
MEMORY_CAP = 1_000_000_000  # Decimal 1 GB, stricter than the earlier 1 GiB smoke.


def emit(label, record):
    print(label + "=" + json.dumps(record, sort_keys=True), flush=True)


def download_file(item, destination):
    relative = PurePosixPath(item["path"])
    allowed = str(relative) in {"queries.json", "sample_submission.csv"} or (
        len(relative.parts) == 2 and relative.parts[0] == "xlsx_files"
        and re.fullmatch(r"[A-Za-z0-9_-]+\.xlsx", relative.name)
    )
    if not allowed or not re.fullmatch(r"[A-Za-z0-9_-]+", item["file_id"]):
        raise ValueError("invalid public manifest entry")
    url = "https://drive.google.com/uc?export=download&id=" + item["file_id"]
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read(item["bytes"] + 1)
            if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError(f"public source changed or download failed: {relative}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["id", "answer"] or any(len(row) != 2 for row in rows):
        raise ValueError("invalid id,answer CSV")
    ids = [row[0] for row in rows[1:]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate output ID")
    return rows[1:]


def validate_output(output, ids):
    rows = csv_rows(output)
    if [row[0] for row in rows] != ids:
        raise ValueError("missing, extra, or reordered output IDs")
    if sorted(path.name for path in output.parent.iterdir()) != ["submission.csv"]:
        raise ValueError("unexpected file beside submission.csv")
    return rows


def sample_comparison(rows, sample):
    expected = dict(sample)
    if set(expected) != {row[0] for row in rows}:
        raise ValueError("public sample and queries have different IDs")
    comparable = [(qid, answer, expected[qid]) for qid, answer in rows if expected[qid].strip()]
    matches = [qid for qid, answer, target in comparable if answer.strip() == target.strip()]
    blanks = [qid for qid, answer, _ in comparable if not answer.strip()]
    mismatches = [
        qid for qid, answer, target in comparable
        if answer.strip() and answer.strip() != target.strip()
    ]
    return {
        "method": "trimmed exact string comparison against the public sample, not judge scoring",
        "sample_nonempty": len(comparable), "exact_matches": len(matches),
        "nonempty_mismatches": len(mismatches), "blank_answers": len(blanks),
        "mismatch_ids": mismatches, "blank_ids": blanks,
        "sample_empty_unscored": len(rows) - len(comparable),
    }


def docker_json(*args):
    return json.loads(subprocess.check_output(["docker", *args], text=True))


def run_container(inputs, out, measured, entrypoint):
    name = "public1-" + uuid.uuid4().hex
    args = [
        "--xlsx-dir", "/input/xlsx_files", "--queries", "/input/queries.json",
        "--output", "/output/submission.csv", "--openrouter-key", "",
        "--openrouter-model", "qwen/qwen3.7-flash",
        "--openrouter-base-url", "https://openrouter.ai/api/v1",
    ]
    create = [
        "docker", "create", "--name", name, "--platform=linux/amd64",
        "--network=none", f"--memory={MEMORY_CAP}", f"--memory-swap={MEMORY_CAP}",
        "--cpus=2", "--pids-limit=128",
        "--mount", f"type=bind,src={inputs},dst=/input,readonly",
        "--mount", f"type=bind,src={out},dst=/output",
    ]
    if measured:
        create += [
            "--mount", f"type=bind,src={ROOT / 'scripts' / 'ci_memory_probe.py'},dst=/ci_memory_probe.py,readonly",
            "--entrypoint", "python", IMAGE, "/ci_memory_probe.py", *entrypoint, *args,
        ]
    else:
        create += [IMAGE, *args]
    subprocess.run(create, check=True, stdout=subprocess.DEVNULL)
    try:
        config = docker_json("inspect", name)[0]
        host = config["HostConfig"]
        assert host["Memory"] == host["MemorySwap"] == MEMORY_CAP
        assert host["NetworkMode"] == "none" and not host.get("DeviceRequests")
        assert next(m for m in config["Mounts"] if m["Destination"] == "/input")["RW"] is False
        start = time.perf_counter()
        result = subprocess.run(
            ["docker", "start", "--attach", name], capture_output=True, text=True,
            encoding="utf-8", timeout=600,
        )
        elapsed = time.perf_counter() - start
        print(result.stdout, end="")
        print(result.stderr, end="")
        state = docker_json("inspect", name)[0]["State"]
        record = {
            "mode": "measured" if measured else "original-entrypoint",
            "container_exit_code": state["ExitCode"], "oom_killed": state["OOMKilled"],
            "wall_seconds_including_start": round(elapsed, 6),
            "memory_cap_bytes": host["Memory"], "network": host["NetworkMode"],
        }
        emit("PUBLIC1_CONTAINER", record)
        result.check_returncode()
        assert state["ExitCode"] == 0 and not state["OOMKilled"]
        assert "[timed-out] none" in result.stderr and "[deadline-skip] none" in result.stderr
        if measured:
            metrics = [json.loads(line[len(PREFIX):]) for line in result.stderr.splitlines() if line.startswith(PREFIX)]
            assert len(metrics) == 1, "missing or duplicate kernel memory metrics"
            metric = metrics[0]
            assert metric["solver_exit_code"] == 0
            # Linux may round the requested memory limit down to its page size.
            assert MEMORY_CAP - os.sysconf("SC_PAGE_SIZE") < metric["limit_bytes"] <= MEMORY_CAP
            assert 0 < metric["peak_bytes"] < metric["limit_bytes"]
            assert metric["swap_limit_bytes"] == metric["swap_current_bytes"] == 0
            assert {"max", "oom", "oom_kill"} <= metric["events_delta"].keys()
            assert all(metric["events_delta"][key] == 0 for key in ("max", "oom", "oom_kill"))
            assert metric["events_delta"].get("oom_group_kill", 0) == 0
            record["memory"] = metric
        return record
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=True, stdout=subprocess.DEVNULL)


def main():
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    entries = manifest["files"]
    assert len({item["path"] for item in entries}) == len(entries)
    image = docker_json("image", "inspect", IMAGE)[0]
    assert (image["Os"], image["Architecture"]) == ("linux", "amd64")
    entrypoint = image["Config"]["Entrypoint"]
    assert entrypoint == ["python", "run.py"]
    with tempfile.TemporaryDirectory(prefix="public1-ci-") as temporary:
        work = Path(temporary)
        inputs = work / "input"
        inputs.mkdir()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda item: download_file(item, inputs), entries))
        before = snapshot(inputs)
        assert before == {item["path"]: item["sha256"] for item in entries}
        queries = json.loads((inputs / "queries.json").read_text(encoding="utf-8-sig"))
        ids = [query["id"] for query in queries]
        assert ids and len(ids) == len(set(ids))
        workbook_names = {item["path"].removeprefix("xlsx_files/") for item in entries if item["path"].endswith(".xlsx")}
        assert {query["file_name"] for query in queries} == workbook_names
        evidence = {
            "source_folder": manifest["source_folder"],
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "image_id": image["Id"], "files": len(entries), "queries": len(ids),
            "workbooks": len(workbook_names), "download_bytes": sum(item["bytes"] for item in entries),
        }
        emit("PUBLIC1_SOURCE", evidence)
        contents = []
        records = []
        for measured in (False, True):
            out = work / ("measured" if measured else "plain")
            out.mkdir()
            record = run_container(inputs, out, measured, entrypoint)
            rows = validate_output(out / "submission.csv", ids)
            assert snapshot(inputs) == before, "input data changed"
            contents.append((out / "submission.csv").read_bytes())
            records.append(record)
        assert contents[0] == contents[1], "measurement changed the output"
        comparison = sample_comparison(rows, csv_rows(inputs / "sample_submission.csv"))
        evidence.update(
            runs=records, csv_sha256=hashlib.sha256(contents[0]).hexdigest(),
            rows=len(rows), nonempty_answers=sum(bool(answer.strip()) for _, answer in rows),
            outputs_identical=True, input_unchanged=True, sample_comparison=comparison,
        )
        emit("PUBLIC1_RESULT", evidence)
        if comparison["nonempty_mismatches"] or comparison["blank_answers"]:
            print("::warning::Public sample comparison is not perfect; see PUBLIC1_RESULT. Docker contract PASS is not answer-quality approval.")
        print("PASS Public data 1: original ENTRYPOINT, measured replay, full ID coverage, unchanged input, kernel peak recorded")


if __name__ == "__main__":
    main()
