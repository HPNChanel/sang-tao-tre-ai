#!/usr/bin/env python3
"""python run.py --data D --out O/submission.csv [--log run_log.jsonl]"""
import argparse, csv, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.xlsx_tables import load_tables, summarize  # noqa: E402
from src import seams, llm_client, plan_validate  # noqa: E402

PLAN_SCHEMA_FILE = Path(__file__).resolve().parent / "src" / "plan_schema.json"
_SHEET_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls")


def _resolve_workbook(data_dir, fname):
    """Workbook path for a query file_name.

    Exact path under xlsx_files/, then beside queries.json. Otherwise the same
    directory, matching the name case-insensitively, then the same stem with a
    spreadsheet extension. A miss returns the xlsx_files path so the caller
    still emits a row.
    """
    data = Path(data_dir)
    raw = str(fname or "").strip().replace("\\", "/")
    rel = Path(raw)
    if rel.is_absolute():
        rel = Path(rel.name)
    parts = list(rel.parts)
    if parts and parts[0].lower() == "xlsx_files":
        parts = parts[1:]
    rel = Path(*parts) if parts else Path()
    roots = (data / "xlsx_files", data)
    if not parts:
        return roots[0] / raw
    for root in roots:
        exact = root / rel
        if exact.is_file():
            return exact
    name = rel.name
    parent = rel.parent
    stem = Path(name).stem.lower()
    rank = {ext: i for i, ext in enumerate(_SHEET_SUFFIXES)}
    for root in roots:
        directory = root / parent
        if not directory.is_dir():
            continue
        files = [p for p in directory.iterdir() if p.is_file()]
        named = sorted((p for p in files if p.name.lower() == name.lower()), key=lambda p: p.name)
        if named:
            return named[0]
        if not stem:
            continue
        stemmed = [
            p for p in files if p.stem.lower() == stem and p.suffix.lower() in rank
        ]
        if stemmed:
            stemmed.sort(key=lambda p: (rank[p.suffix.lower()], p.name.lower()))
            return stemmed[0]
    return roots[0] / rel


def _dedupe_queries(queries):
    """Keep the first query for each id. Later copies are not answered again."""
    seen = set()
    kept = []
    for q in queries:
        qid = q.get("id") if isinstance(q, dict) else None
        if qid is not None and qid in seen:
            continue
        if qid is not None:
            seen.add(qid)
        kept.append(q)
    return kept


def _unique(ids):
    seen = set()
    out = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def _rule_answer(query, tables, fname, strict=None):
    """Rule planner + executor. strict=False is the no-key path."""
    plan = seams.code_plan(query, tables, fname, strict)
    return seams.execute(plan, tables, query) if plan else None


def _llm_then_rule(q, tables, fname, budget):
    """LLM plan, or the non-strict rule answer if the LLM fails for any reason.

    Failure includes exceptions, HTTP errors, timeouts, unparseable JSON, schema
    rejection, an executor error on the LLM plan, and the cost cap. The fallback
    is plan(..., strict=False), so a fake key plus a dead LLM matches a no-key run.
    """
    query = q["query"]
    ans = None
    try:
        schema = PLAN_SCHEMA_FILE.read_text() if PLAN_SCHEMA_FILE.exists() else "{}"
        plan = llm_client.llm_plan(query, summarize(tables), schema, budget,
                                   validate=plan_validate.validate, qid=q.get("id", ""))
        if plan is None:
            print(f"[schema] {q.get('id')}: no schema-valid LLM plan "
                  f"(attempts={budget.attempts}, cap/retries exhausted); "
                  f"using non-strict planner",
                  file=sys.stderr)
        else:
            ans = seams.execute(plan, tables, query)
    except Exception as e:
        print(f"[warn] {q.get('id')}: LLM {type(e).__name__}: {e}", file=sys.stderr)
        ans = None
    if ans is None:
        ans = _rule_answer(query, tables, fname, False)
    return ans


def answer_one(q, data_dir, cache):
    budget = llm_client.Budget()
    fname = q.get("file_name", "")
    path = _resolve_workbook(data_dir, fname)
    try:
        key = str(path)
        if key not in cache:
            cache.clear()  # keep RAM low: only one workbook in memory
            cache[key] = load_tables(str(path))
        tables = cache[key]
        ans = _rule_answer(q["query"], tables, fname)
        if ans is None and llm_client.enabled() and seams.have_executor():
            ans = _llm_then_rule(q, tables, fname, budget)
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
    queries = _dedupe_queries(json.loads((data / "queries.json").read_text(encoding="utf-8")))
    header, order = ["id", "answer"], [q["id"] for q in queries]
    sample = data / "sample_submission.csv"
    if sample.exists():
        with open(sample, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        ids = _unique(r[0] for r in rows[1:] if r)
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
                                 "completion_tokens": b.completion_tokens,
                                 "reasoning_tokens": b.reasoning_tokens,
                                 "attempts": b.log,
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
