"""Verify the supplied public ZIP in ephemeral CI without committing its data."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

from ci_docker_smoke import IMAGE, ROOT, snapshot
from ci_public_data1 import csv_rows, docker_json, emit, run_container, sample_comparison, validate_output

MANIFEST = ROOT / ".github" / "public-data2-manifest.json"


def unpack_verified(raw, manifest, target):
    if len(raw) != manifest["zip_bytes"] or hashlib.sha256(raw).hexdigest() != manifest["zip_sha256"]:
        raise ValueError("Public data 2 ZIP differs from the supplied source")
    expected = {x["path"]: x for x in manifest["files"]}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if len(members) != len(expected) or {x.filename for x in members} != set(expected):
            raise ValueError("ZIP member set differs from manifest")
        for info in members:
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in str(name) or ":" in str(name):
                raise ValueError("unsafe ZIP path")
            record = expected[str(name)]
            if info.file_size != record["bytes"]:
                raise ValueError("unexpected uncompressed member size")
            content = archive.read(info)
            if hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise ValueError("ZIP member hash differs from manifest")
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def main():
    url = os.environ.get("PUBLIC2_URL", "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("PUBLIC2_URL must be an HTTPS download URL without credentials")
    if not (parsed.hostname == "drive.google.com" or parsed.hostname.endswith(".trycloudflare.com")):
        raise SystemExit("Public transfer host is not allowed")
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read(manifest["zip_bytes"] + 1)
    except Exception as exc:
        raise SystemExit("Public transfer failed: " + type(exc).__name__) from None
    image = docker_json("image", "inspect", IMAGE)[0]
    assert (image["Os"], image["Architecture"]) == ("linux", "amd64")
    entrypoint = image["Config"]["Entrypoint"]
    assert entrypoint == ["python", "run.py"]
    with tempfile.TemporaryDirectory(prefix="public2-ci-") as temporary:
        work = Path(temporary)
        source = work / "source"
        unpack_verified(raw, manifest, source)
        # The solver receives no answer file and no sample answers.
        inputs = work / "input"
        inputs.mkdir()
        shutil.copyfile(source / "queries.json", inputs / "queries.json")
        shutil.copytree(source / "xlsx_files", inputs / "xlsx_files")
        before = snapshot(inputs)
        queries = json.loads((inputs / "queries.json").read_text(encoding="utf-8-sig"))
        ids = [str(q["id"]) for q in queries]
        assert ids and len(set(ids)) == len(ids)
        sample = csv_rows(source / "sample_submission.csv")
        answers = json.loads((source / "answers.json").read_text(encoding="utf-8-sig"))
        assert dict(sample) == {str(x["id"]): str(x["answer"]) for x in answers}
        records, contents = [], []
        for measured in (False, True):
            out = work / ("measured" if measured else "plain")
            out.mkdir()
            records.append(run_container(inputs, out, measured, entrypoint, label="PUBLIC2"))
            rows = validate_output(out / "submission.csv", ids)
            assert snapshot(inputs) == before, "input data changed"
            contents.append((out / "submission.csv").read_bytes())
        assert contents[0] == contents[1], "measurement changed the output"
        comparison = sample_comparison(rows, sample)
        evidence = {"source": manifest["source"], "zip_sha256": manifest["zip_sha256"],
                    "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    "image_id": image["Id"], "queries": len(ids), "workbooks": len(list((inputs / "xlsx_files").iterdir())),
                    "runs": records, "csv_sha256": hashlib.sha256(contents[0]).hexdigest(),
                    "outputs_identical": True, "input_unchanged": True, "answer_files_mounted": False,
                    "sample_comparison": comparison}
        emit("PUBLIC2_RESULT", evidence)
        if comparison["nonempty_mismatches"] or comparison["blank_answers"]:
            print("::warning::Public data 2 comparison is not perfect; see PUBLIC2_RESULT. Contract PASS is not answer-quality approval.")
        print("PASS Public data 2: six arguments, original ENTRYPOINT, full IDs, unchanged input, kernel peak recorded")


if __name__ == "__main__":
    main()
