#!/usr/bin/env bash
# Run dataset 2 end to end on the pure code path, then package the answer zip.
#
# Usage:
#   scripts/dry_run_dataset2.sh INPUT OUTPUT_DIR
#
# INPUT is a data directory, or a .zip of one. The directory must contain
# queries.json. Each query's file_name is resolved by run.py under
# xlsx_files/ first, then next to queries.json. sample_submission.csv is
# optional; when present, run.py copies its header and id order.
#
# OUTPUT_DIR receives submission.csv, run_log.jsonl, and submission.zip.
#
# The timed span is run.py plus zip packaging. The checker runs after that.
# OPENROUTER_API_KEY is removed from the child environment so the solver
# stays on the code path.
#
# A synthetic pack for rehearsal (fixed seed, not contest data):
#   python3 scripts/gen_fake_dataset2.py --out DIR
#   scripts/dry_run_dataset2.sh DIR OUTPUT_DIR
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: scripts/dry_run_dataset2.sh INPUT OUTPUT_DIR" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd)
INPUT=$(realpath "$1")
OUTPUT=$(realpath -m "$2")
mkdir -p "$OUTPUT"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

data=$(python3 - "$INPUT" "$work" <<'PY'
import sys
import zipfile
from pathlib import Path

src = Path(sys.argv[1])
work = Path(sys.argv[2])

def pick(root: Path) -> Path:
    found = [p for p in root.rglob("queries.json") if p.is_file()]
    if not found:
        sys.exit("input has no queries.json")
    found.sort(key=lambda p: (len(p.relative_to(root).parts), str(p.relative_to(root))))
    return found[0].parent

if src.is_dir():
    print(pick(src))
    sys.exit(0)
if src.suffix.lower() != ".zip":
    sys.exit("INPUT must be a directory or a .zip")

dest = work / "unz"
dest.mkdir()
with zipfile.ZipFile(src) as zf:
    root = dest.resolve()
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or p.startswith("/") or ":" in p for p in parts):
            sys.exit(f"unsafe zip member: {name}")
        target = root.joinpath(*parts).resolve()
        if root != target and root not in target.parents:
            sys.exit(f"unsafe zip member: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(info))
print(pick(dest))
PY
)

cd "$ROOT"
seconds=$(
  env -u OPENROUTER_API_KEY python3 - "$data" "$OUTPUT" <<'PY'
import subprocess
import sys
import time
from pathlib import Path

data, output = sys.argv[1], Path(sys.argv[2])
csv_path = output / "submission.csv"
zip_path = output / "submission.zip"
t0 = time.perf_counter()
subprocess.check_call(
    [sys.executable, "run.py", "--data", data, "--out", str(csv_path)]
)
if zip_path.exists():
    zip_path.unlink()
subprocess.check_call(["zip", "-q", "-X", "-j", str(zip_path), str(csv_path)])
print(f"{time.perf_counter() - t0:.3f}")
PY
)

echo "run+package wall_clock_s=${seconds}"
"$ROOT/scripts/check_zip.sh" "$OUTPUT/submission.zip"
