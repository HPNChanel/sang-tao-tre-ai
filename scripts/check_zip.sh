#!/usr/bin/env bash
# Validate a submission zip against the contract run.py writes.
#
# Usage: scripts/check_zip.sh SUBMISSION.zip
#
# Zip structure:
#   submission.csv is the only member, stored at the archive root
#   (no parent folder, no extra files).
# Required file: submission.csv
# Answer CSV columns: the header starts with id,answer.
#   Further columns are allowed. Every data row has the same width
#   as the header. The id column is non-empty and unique. The answer
#   column may be blank. Encoding is UTF-8; a leading BOM is accepted.
#
# Prints OK and exits 0 when the zip passes.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: check_zip.sh SUBMISSION.zip" >&2
  exit 2
fi

python3 - "$1" <<'PY'
import csv
import io
import sys
import zipfile
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    print(f"not a file: {path.name}", file=sys.stderr)
    sys.exit(1)
try:
    zf = zipfile.ZipFile(path)
except zipfile.BadZipFile as exc:
    print(f"bad zip: {exc}", file=sys.stderr)
    sys.exit(1)

with zf:
    names = zf.namelist()
    if names != ["submission.csv"]:
        print(
            "zip must contain only submission.csv at the archive root, "
            f"found {names}",
            file=sys.stderr,
        )
        sys.exit(1)
    info = zf.getinfo("submission.csv")
    if info.is_dir():
        print("submission.csv is a directory entry", file=sys.stderr)
        sys.exit(1)
    raw = zf.read("submission.csv")

if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError as exc:
    print(f"submission.csv is not utf-8: {exc}", file=sys.stderr)
    sys.exit(1)

rows = list(csv.reader(io.StringIO(text)))
if not rows:
    print("submission.csv is empty", file=sys.stderr)
    sys.exit(1)
header = rows[0]
if len(header) < 2 or header[0] != "id" or header[1] != "answer":
    print(f"header must start with id,answer, found {header}", file=sys.stderr)
    sys.exit(1)
data = rows[1:]
if not data:
    print("submission.csv has no data rows", file=sys.stderr)
    sys.exit(1)
seen = set()
width = len(header)
for i, row in enumerate(data, start=2):
    if len(row) != width:
        print(f"row {i} has {len(row)} columns, header has {width}", file=sys.stderr)
        sys.exit(1)
    qid = row[0]
    if not qid:
        print(f"row {i} has an empty id", file=sys.stderr)
        sys.exit(1)
    if qid in seen:
        print(f"duplicate id {qid}", file=sys.stderr)
        sys.exit(1)
    seen.add(qid)

print("OK")
PY
