# Tài năng AI – xlsx QA solver

## Install
```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Configure (optional)
By default the solver is pure code and never calls an LLM.
To enable the fallback planner (qwen/qwen3.7-flash via OpenRouter), set:
```
export OPENROUTER_API_KEY=...        # never stored in source
export COST_CAP_USD=0.02             # optional per-question cap (hard max 0.09)
export QWEN_PRICE_IN_PER_M=0.0345 QWEN_PRICE_OUT_PER_M=0.1495   # optional override; defaults = priciest OpenRouter endpoint as of 2026-10-15
```
The LLM only converts a question into a JSON plan; code computes the answer.

## Run
```
python run.py --data "<folder with queries.json and xlsx_files/>" --out "<dir>/submission.csv"
```
Writes `submission.csv` (same header and id order as `sample_submission.csv` if present)
and `run_log.jsonl` next to it (one line per question: `id`, `tokens`, `cost_usd`;
override with `--log`). Paths with spaces are fine; no dataset path is hard-coded.

## Test
```
python -m pytest -q
```
