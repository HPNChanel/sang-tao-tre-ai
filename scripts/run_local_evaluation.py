"""Run frozen, answer-free 20-question batches; do not print predictions."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

import qwen_campaign as C


def snapshot(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file()}


def solver_env(token):
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC", "LANG"}
    result = {k: v for k, v in os.environ.items() if k.upper() in keep}
    result.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                  PYTHONDONTWRITEBYTECODE="1", EVAL_GATEWAY_TOKEN=token)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--credential")
    parser.add_argument("--stage", choices=C.STAGE_CAPS, default="baseline")
    parser.add_argument("--policy", choices=("fallback", "selective"), default="fallback")
    parser.add_argument("--output-tokens", type=int, choices=(600, 1200), default=600)
    parser.add_argument("--repetition", type=int, default=1)
    parser.add_argument("--no-key", action="store_true")
    parser.add_argument('--solver-profile',choices=('legacy','direct','raw','dual'),default='legacy')
    parser.add_argument('--model',default='qwen/qwen3.7-flash')
    args = parser.parse_args()
    control, source, dataset, output = map(lambda p: Path(p).resolve(),
                                          (args.control, args.source, args.dataset, args.output))
    if output.exists():
        raise SystemExit("output already exists; resume requires checking prior invocation receipts")
    source_manifest = json.loads((source / "SOURCE_MANIFEST.json").read_text())
    if source_manifest["source_commit"] != args.source_sha:
        raise SystemExit("source revision does not match its frozen manifest")
    for relative, digest in source_manifest["files"].items():
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != digest:
            raise SystemExit("frozen runtime source changed")
    queries = json.loads((dataset / "queries.json").read_text(encoding="utf-8-sig"))
    ids = [str(q["id"]) for q in queries]
    if not ids or len(set(ids)) != len(ids):
        raise SystemExit("query IDs must be nonempty and unique")
    for query in queries:
        name = query["file_name"]
        if not isinstance(name, str) or not re.fullmatch(r"[^/\\:]+\.xlsx", name, flags=re.I):
            raise SystemExit("unsafe workbook filename")
    output.mkdir(parents=True)
    paid = not args.no_key
    key = C.load_key(args.credential) if paid else None
    if paid:
        C.preflight(key, control / "pricing.json")
    all_rows, receipts = [], []
    configuration = {"policy": args.policy, "output_tokens": args.output_tokens, "api_enabled": paid}
    if args.solver_profile!='legacy': configuration['solver_profile'] = args.solver_profile
    if args.model!=C.MODEL: configuration['model'] = args.model
    with C.exclusive_ledger(control / "ledger.json"):
        ledger = C.Ledger(control / "ledger.json")
        for start in range(0, len(queries), 20):
            batch = queries[start:start + 20]
            batch_dir = output / f"batch-{start // 20:03}"
            inputs, outputs = batch_dir / "input", batch_dir / "output"
            (inputs / "xlsx_files").mkdir(parents=True)
            outputs.mkdir()
            C.atomic_json(inputs / "queries.json", batch)
            for name in set(q["file_name"] for q in batch):
                shutil.copyfile(dataset / "xlsx_files" / name, inputs / "xlsx_files" / name)
            before = snapshot(inputs)
            input_sha = hashlib.sha256(C.canonical(before)).hexdigest()
            invocation = ledger.register(args.stage, args.source_sha, input_sha, configuration,
                                         [str(q["id"]) for q in batch], args.repetition,
                                         case_keys=C.stable_case_keys(batch,before))
            token = secrets.token_urlsafe(32)
            server = thread = None
            gateway_url = ""
            if paid:
                gateway = C.Gateway(ledger, key, token, args.stage, invocation, control / "pricing.json")
                server = C.make_server(gateway)
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
                thread.start()
                gateway_url = f"http://127.0.0.1:{server.server_address[1]}"
            command = [sys.executable, str(Path(__file__).with_name("eval_solver.py")),
                       "--source", str(source), "--input", str(inputs), "--output", str(outputs / "submission.csv"),
                       "--gateway", gateway_url, "--policy", args.policy, "--output-tokens", str(args.output_tokens),
                       '--solver-profile',args.solver_profile,'--model',args.model]
            if not paid:
                command.append("--no-key")
            started = time.monotonic()
            timed_out = False
            try:
                with (outputs / "stdout.txt").open("wb") as stdout, (outputs / "stderr.txt").open("wb") as stderr:
                    try:
                        process = subprocess.run(command, env=solver_env(token), cwd=source,
                                                 stdout=stdout, stderr=stderr, timeout=2400)
                        code = process.returncode
                    except subprocess.TimeoutExpired:
                        code, timed_out = -1, True
                elapsed = time.monotonic() - started
            finally:
                if server:
                    server.shutdown()
                    server.server_close()  # join in-flight billing handlers before proceeding
                    thread.join()
            if snapshot(inputs) != before:
                raise SystemExit("solver changed an input file")
            expected_ids = [str(q["id"]) for q in batch]
            with (outputs / "submission.csv").open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
            if not rows or rows[0] != ["id", "answer"] or any(len(r) != 2 for r in rows[1:]) or [r[0] for r in rows[1:]] != expected_ids:
                raise SystemExit("solver output violates the CSV contract")
            all_rows.extend(rows[1:])
            receipt = {"index": start // 20, "invocation": invocation, "input_sha256": input_sha,
                       "source_sha": args.source_sha, "configuration": configuration,
                       "questions": len(batch), "elapsed_seconds": elapsed,
                       "exit_code": code, "timed_out": timed_out,
                       "cost": ledger.totals(run=invocation), "csv_sha256": hashlib.sha256((outputs / "submission.csv").read_bytes()).hexdigest()}
            C.atomic_json(batch_dir / "receipt.json", receipt)
            receipts.append(receipt)
            print(json.dumps({"batch": start // 20, "questions": len(batch), "exit_code": code,
                              "seconds": round(elapsed, 3), "cost": receipt["cost"]}), flush=True)
            if code != 0 or ledger.state["halt_reason"]:
                raise SystemExit("evaluation stopped; preserve partial receipts")
        with (output / "submission.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "answer"])
            writer.writerows(all_rows)
        result = {"source_sha": args.source_sha, "configuration": configuration,
                  "questions": len(queries), "batches": receipts, "platform": sys.platform,
                  "adapter": "transport and question identity only; no goldens supplied",
                  "provider_credential_supplied_to_solver": False}
        C.atomic_json(output / "receipt.json", result)


if __name__ == "__main__":
    main()
