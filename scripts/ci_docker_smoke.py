"""Check the contest Docker contract using only a generated workbook.

Run after installing requirements.txt on a host with Docker. The image stays
local to that host. Both container runs have no network and a 1 GiB memory cap.
"""
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

import openpyxl

from build_submission_package import create_package

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sangtaotreai-ci:smoke"
QUESTION = "How many people scored at least 60? Answer with only the integer."


def checked(*args, timeout=180):
    result = subprocess.run(
        args, check=False, capture_output=True, text=True, timeout=timeout,
    )
    print(result.stdout, end="")
    print(result.stderr, end="")
    result.check_returncode()
    return result


def snapshot(folder):
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*") if path.is_file()
    }


def make_input(folder):
    xlsx = folder / "xlsx"
    xlsx.mkdir(parents=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Birth", "Admission", "Score"])
    for name, score in (("Ada", 40), ("Bo", 65), ("Cy", 85)):
        sheet.append([name, dt.datetime(1995, 1, 1), dt.datetime(2015, 1, 1), score])
    workbook.save(xlsx / "people.xlsx")
    queries = [
        {"id": qid, "file_name": "people.xlsx", "query": QUESTION}
        for qid in ("ci-20", "ci-10")
    ]
    (folder / "queries.json").write_text(json.dumps(queries), encoding="utf-8")
    # Contest mode follows queries.json even if a differently ordered sample exists.
    (xlsx / "sample_submission.csv").write_text(
        "id,answer\nci-10,\nci-20,\n", encoding="utf-8",
    )


def main():
    if shutil.which("docker") is None:
        raise SystemExit("Docker is required on the verification host")
    with tempfile.TemporaryDirectory(prefix="contest-ci-") as temporary:
        work = Path(temporary)
        context = work / "build"
        context.mkdir()
        # Build the actual source package, extract it, then build that exact copy.
        archive_path = work / "PDU Team.zip"
        receipt = create_package(archive_path)
        print("SUBMISSION_PACKAGE=" + json.dumps(receipt, sort_keys=True), flush=True)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(context)
        checked("docker", "build", "--platform=linux/amd64", "-t", IMAGE,
                str(context), timeout=600)
        metadata = json.loads(checked("docker", "image", "inspect", IMAGE).stdout)[0]
        assert (metadata["Os"], metadata["Architecture"]) == ("linux", "amd64")
        assert metadata["Config"]["Entrypoint"] == ["python", "run.py"]

        inputs = work / "input"
        make_input(inputs)
        before = snapshot(inputs)
        outputs = []
        for label, key in (("no-key", ""), ("fake-key", "test-not-a-real-key")):
            out = work / label
            out.mkdir()
            result = checked(
                "docker", "run", "--rm", "--platform=linux/amd64",
                "--network=none", "--memory=1g", "--memory-swap=1g",
                "--cpus=2", "--pids-limit=128",
                "--mount", f"type=bind,src={inputs},dst=/input,readonly",
                "--mount", f"type=bind,src={out},dst=/output",
                IMAGE,
                "--xlsx-dir", "/input/xlsx",
                "--queries", "/input/queries.json",
                "--output", "/output/submission.csv",
                "--openrouter-key", key,
                "--openrouter-model", "qwen/qwen3.7-flash",
                "--openrouter-base-url", "https://openrouter.ai/api/v1",
            )
            output = out / "submission.csv"
            with output.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            assert rows == [["id", "answer"], ["ci-20", "2"], ["ci-10", "2"]], rows
            assert sorted(path.name for path in out.iterdir()) == ["submission.csv"]
            assert "ci-20" in result.stderr and "ci-10" in result.stderr
            assert snapshot(inputs) == before, "input files changed"
            outputs.append(output.read_bytes())
            print(f"PASS {label}: six arguments, exact CSV and ID order, stderr logs, input unchanged")
        assert outputs[0] == outputs[1], "fake-key and no-key results differ"
        print("PASS Docker: linux/amd64, ENTRYPOINT, network disabled, 1 GiB cap, no GPU requested")


if __name__ == "__main__":
    main()
