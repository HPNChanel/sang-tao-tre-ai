"""Build a deterministic source ZIP from a committed, allowlisted revision."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"Dockerfile", "requirements.txt", "run.py"}


def submission_readme(payload):
    docker = payload["Dockerfile"].decode("utf-8")
    policy = re.search(r"QWEN_REVIEW_POLICY=(fallback|selective)\b", docker)
    tokens = re.search(r"QWEN_MAX_OUTPUT_TOKENS=(600|1200)\b", docker)
    policy = policy[1] if policy else "fallback"
    tokens = tokens[1] if tokens else "600"
    return rf"""# PDU Team Excel question answering

The program answers natural-language questions from new Excel workbooks.
It writes UTF-8 CSV with exactly `id,answer`, one row per input ID in input
order. Answers contain only the final result; unresolved answers are empty.
Contest-mode diagnostics go to stderr, outside the CSV.

## Input and output

Supply your own paths through the CLI. No sample dataset or fixed host path is
required. The example layout is `input/queries.json`, `input/xlsx_files/*.xlsx`
and a writable `output/` directory. Each query object has `id`, `file_name` and
`query`; for example, `file_name` can be `workbook.xlsx`.

## Docker installation and run

Run from the extracted package directory with Docker installed. Network access
is needed to install dependencies during the build and to use Qwen at runtime.

```bash
docker build --platform=linux/amd64 -t pdu-team .
mkdir -p output
export OPENROUTER_API_KEY='YOUR_OPENROUTER_KEY'
docker run --rm --platform=linux/amd64 \
  --memory=1000000000 --memory-swap=1000000000 \
  --mount type=bind,src="$PWD/input",dst=/input,readonly \
  --mount type=bind,src="$PWD/output",dst=/output \
  pdu-team \
  --xlsx-dir /input/xlsx_files \
  --queries /input/queries.json \
  --output /output/submission.csv \
  --openrouter-key "$OPENROUTER_API_KEY" \
  --openrouter-model qwen/qwen3.7-flash \
  --openrouter-base-url https://openrouter.ai/api/v1
```

The entrypoint is `python run.py`; the six named arguments above are the contest
interface. Equal memory and memory-swap limits disable container swap.

## Direct Python installation and run

Use Python 3.11. Linux is the resource-acceptance platform; Windows does not
provide all the same process interruption and alarm mechanisms.

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p output
export QWEN_REVIEW_POLICY={policy}
export QWEN_MAX_OUTPUT_TOKENS={tokens}
export OPENROUTER_API_KEY='YOUR_OPENROUTER_KEY'
python run.py \
  --xlsx-dir ./input/xlsx_files \
  --queries ./input/queries.json \
  --output ./output/submission.csv \
  --openrouter-key "$OPENROUTER_API_KEY" \
  --openrouter-model qwen/qwen3.7-flash \
  --openrouter-base-url https://openrouter.ai/api/v1
```

## Configuration

The packaged profile is `{policy}` with at most `{tokens}` output tokens per
model call. The Dockerfile records its defaults; direct Python runs need the
exports shown above. `fallback` calls Qwen when deterministic planning cannot
answer; `selective` also reviews supported risk signals. Qwen proposes a JSON
plan, and Python validates references and executes the supported operations.

Supply credentials only at runtime. To run without API calls, replace the
`--openrouter-key` value with an empty string (`--openrouter-key ''`). No-key
mode is useful for installation checks; it does not reproduce Qwen-enabled
accuracy. Never place credentials or datasets inside the source ZIP.

Mount inputs read-only and the output directory writable. Any optional `--log`
output must be separate from `submission.csv`. `SOURCE_MANIFEST.json` pins the
committed runtime files and SHA-256 hashes. The delivery receipt records the
complete ZIP checksum. Documentation can change while those runtime bytes stay
identical. The package excludes datasets, golden answers, tests and local caches.
""".encode("utf-8")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def create_package(output, ref="HEAD"):
    commit = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    tracked = git("ls-tree", "-r", "--name-only", commit).decode().splitlines()
    names = sorted(name for name in tracked if name in ROOT_FILES or (
        name.startswith("src/") and PurePosixPath(name).suffix in {".py", ".json", ".txt"}
    ))
    if not ROOT_FILES <= set(names) or "src/plan_schema.json" not in names:
        raise ValueError("revision lacks required runtime files")
    payload = {name: git("show", commit + ":" + name) for name in names}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    manifest = {"schema_version": 1, "source_commit": commit, "files": hashes,
                "excluded": ["datasets", "answers", "credentials", "tests", "git metadata", "local caches"]}
    payload["SOURCE_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    payload["README.md"] = submission_readme(payload)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stored members avoid platform-dependent zlib output; source is far below 100 MB.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    raw = output.read_bytes()
    if len(raw) > 100_000_000:
        raise ValueError("submission exceeds the 100 MB limit")
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == set(payload)
        assert all(archive.read(name) == content for name, content in payload.items())
    return {"source_commit": commit, "zip_sha256": hashlib.sha256(raw).hexdigest(),
            "zip_bytes": len(raw), "runtime_files": len(names), "members": len(payload)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    print("SUBMISSION_PACKAGE=" + json.dumps(create_package(args.output, args.ref), sort_keys=True))


if __name__ == "__main__":
    main()
