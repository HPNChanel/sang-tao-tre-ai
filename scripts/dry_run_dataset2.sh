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
# The measured span is run.py plus zip packaging. The checker runs after that.
# Measurement uses /usr/bin/time -v (Maximum resident set size) when that
# binary exists. Otherwise the same span is timed in process and peak RSS
# comes from resource.getrusage(RUSAGE_SELF), with waited children included
# so the zip step is not dropped. Peak RSS is printed in MB next to the
# contest 1 GB limit (1024 MB, matching the kibibyte unit time reports).
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

# Shared driver: run.py then zip. stdout is unused. stderr is the solver log.
run_pack() {
  env -u OPENROUTER_API_KEY python3 - "$data" "$OUTPUT" <<'PY'
import subprocess
import sys
import zipfile
from pathlib import Path

data, output = sys.argv[1], Path(sys.argv[2])
csv_path = output / "submission.csv"
zip_path = output / "submission.zip"
subprocess.check_call(
    [sys.executable, "run.py", "--data", data, "--out", str(csv_path)]
)
if zip_path.exists():
    zip_path.unlink()
# stdlib only: member name is submission.csv at the archive root
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zf.write(csv_path, arcname="submission.csv")
PY
}

if [[ -x /usr/bin/time ]]; then
  export data OUTPUT
  export -f run_pack
  LC_ALL=C /usr/bin/time -v -o "$work/timev.txt" bash -c 'set -euo pipefail; run_pack'
  parsed=$(python3 - "$work/timev.txt" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
em = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", text)
rm = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
if not em or not rm:
    sys.exit("time -v output missing elapsed or maximum resident set size")
bits = em.group(1).split(":")
if len(bits) == 3:
    sec = int(bits[0]) * 3600 + int(bits[1]) * 60 + float(bits[2])
elif len(bits) == 2:
    sec = int(bits[0]) * 60 + float(bits[1])
else:
    sec = float(bits[0])
print(f"{sec:.3f} {rm.group(1)}")
PY
)
else
  parsed=$(env -u OPENROUTER_API_KEY python3 - "$data" "$OUTPUT" <<'PY'
import resource
import sys
import time
import zipfile
from pathlib import Path

data, output = sys.argv[1], Path(sys.argv[2])
csv_path = output / "submission.csv"
zip_path = output / "submission.zip"
sys.path.insert(0, ".")
import run

t0 = time.perf_counter()
rc = run.main(["--data", data, "--out", str(csv_path)])
if rc:
    sys.exit(rc)
if zip_path.exists():
    zip_path.unlink()
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zf.write(csv_path, arcname="submission.csv")
elapsed = time.perf_counter() - t0
self_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
child_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
print(f"{elapsed:.3f} {max(self_kb, child_kb)}")
PY
)
fi

seconds=${parsed%% *}
rss_kb=${parsed##* }
python3 - "$seconds" "$rss_kb" <<'PY'
import sys

seconds = float(sys.argv[1])
kb = int(sys.argv[2])
mb = kb / 1024.0
limit = 1024.0
print(f"run+package wall_clock_s={seconds:.3f}")
print(f"run+package peak_rss_mb={mb:.1f}")
print(f"run+package rss_limit_mb={limit:.0f}")
print(f"run+package rss_headroom_mb={limit - mb:.1f}")
PY
"$ROOT/scripts/check_zip.sh" "$OUTPUT/submission.zip"
