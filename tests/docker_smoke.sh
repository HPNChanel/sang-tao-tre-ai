#!/usr/bin/env bash
# Docker half of the submission-adapter check.
# Used when the test process has no Docker daemon.
# Builds linux/amd64 and runs the six contest arguments with a fake key.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DATA=${1:-"$ROOT/data/Public data 1"}
FAKE_KEY=${FAKE_KEY:-test-not-a-real-key}
MODEL=${OPENROUTER_MODEL_ARG:-qwen/qwen3.7-flash}
BASE_URL=${OPENROUTER_BASE_URL_ARG:-https://openrouter.ai/api/v1}
IMAGE=${IMAGE_NAME:-submission-adapter:smoke}

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not available" >&2
  exit 2
fi
if [[ ! -d "$DATA" ]]; then
  echo "data directory not found: $DATA" >&2
  exit 2
fi
if [[ -f "$DATA/queries.json" ]]; then
  QUERIES="$DATA/queries.json"
  XLSX="$DATA"
elif [[ -f "$DATA/queries/queries.json" ]]; then
  QUERIES="$DATA/queries/queries.json"
  XLSX="$DATA/xlsx"
else
  echo "queries.json not found under $DATA" >&2
  exit 2
fi

docker build --platform=linux/amd64 -t "$IMAGE" "$ROOT"

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
before=$(find "$DATA" -type f -print0 | sort -z | xargs -0 sha256sum)

docker run --rm \
  -v "$XLSX":/input/xlsx:ro \
  -v "$(dirname "$QUERIES")":/input/queries:ro \
  -v "$out":/output \
  "$IMAGE" \
  --xlsx-dir /input/xlsx \
  --queries "/input/queries/$(basename "$QUERIES")" \
  --output /output/submission.csv \
  --openrouter-key "$FAKE_KEY" \
  --openrouter-model "$MODEL" \
  --openrouter-base-url "$BASE_URL"

python3 - "$out/submission.csv" <<'PY'
import csv, sys
from pathlib import Path
path = Path(sys.argv[1])
rows = list(csv.reader(path.open(encoding="utf-8")))
if not rows or rows[0] != ["id", "answer"]:
    sys.exit(f"header must be id,answer, found {rows[:1]}")
ids = [r[0] for r in rows[1:]]
if any(len(r) != 2 for r in rows):
    sys.exit("every row must have 2 columns")
if len(ids) != len(set(ids)):
    sys.exit("duplicate id in submission")
if not ids:
    sys.exit("submission has no data rows")
if list(path.parent.glob("run_log.jsonl")):
    sys.exit("run_log.jsonl must not sit beside the submission")
print(f"rows={len(ids)}")
PY

after=$(find "$DATA" -type f -print0 | sort -z | xargs -0 sha256sum)
if [[ "$before" != "$after" ]]; then
  echo "input directory changed" >&2
  exit 1
fi
echo "docker smoke ok"
