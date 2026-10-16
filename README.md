# PDU Team Excel question answering

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
export QWEN_REVIEW_POLICY=selective
export QWEN_MAX_OUTPUT_TOKENS=600
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

The packaged profile is `selective` with at most `600` output tokens per
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

## Repository packaging and tests

Build the deterministic submission from a committed runtime revision:

```bash
python scripts/build_submission_package.py --ref HEAD --output deliverables/AI26C0101.zip
python -m pytest -q
```
