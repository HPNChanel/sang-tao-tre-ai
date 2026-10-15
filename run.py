#!/usr/bin/env python3
"""python run.py --data D --out O/submission.csv [--log run_log.jsonl]"""
import argparse, csv, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.xlsx_tables import load_tables, summarize  # noqa: E402
from src import seams, llm_client  # noqa: E402

PLAN_SCHEMA_FILE = Path(__file__).resolve().parent / "src" / "plan_schema.json"  # TODO(KSP1)


def answer_one(q, data_dir, cache):
    budget = llm_client.Budget()
    fname = q.get("file_name", "")
    path = Path(data_dir) / "xlsx_files" / fname
    if not path.exists():
        path = Path(data_dir) / fname
    try:
        if fname not in cache:
            cache.clear()  # keep RAM low: only one workbook in memory
            cache[fname] = load_tables(str(path))
        tables = cache[fname]
        plan = seams.code_plan(q["query"], tables, fname)
        ans = seams.execute(plan, tables, q["query"]) if plan else None
        if ans is None and llm_client.enabled() and seams.have_executor():
            schema = PLAN_SCHEMA_FILE.read_text() if PLAN_SCHEMA_FILE.exists() else "{}"
            plan = llm_client.llm_plan(q["query"], summarize(tables), schema, budget)
            ans = seams.execute(plan, tables, q["query"]) if plan else None
    except Exception as e:  # never crash the whole run
        print(f"[warn] {q.get('id')}: {type(e).__name__}: {e}", file=sys.stderr)
        ans = None
    return ("" if ans is None else str(ans)), budget


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", default=None, help="JSONL log (default: <out dir>/run_log.jsonl)")
    a = ap.parse_args(argv)
    data = Path(a.data)
    queries = json.loads((data / "queries.json").read_text(encoding="utf-8"))
    header, order = ["id", "answer"], [q["id"] for q in queries]
    sample = data / "sample_submission.csv"
    if sample.exists():
        with open(sample, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        ids = [r[0] for r in rows[1:] if r]
        if set(ids) == set(order):
            order = ids
    byid = {q["id"]: q for q in queries}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    log = Path(a.log) if a.log else out.parent / "run_log.jsonl"
    answers, cache = {}, {}
    with open(log, "w", encoding="utf-8") as lf:
        for qid in sorted(order, key=lambda i: byid[i].get("file_name", "")):
            t = time.time()
            ans, b = answer_one(byid[qid], data, cache)
            answers[qid] = ans
            lf.write(json.dumps({"id": qid, "tokens": b.tokens,
                                 "cost_usd": round(b.spent, 6)}) + "\n")
            print(f"{qid}\t{time.time()-t:.2f}s\t{ans!r}", file=sys.stderr)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for qid in order:
            w.writerow([qid, answers[qid]] + [""] * (len(header) - 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
