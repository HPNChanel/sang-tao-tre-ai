#!/usr/bin/env python3
"""Time cached and uncached runs on a generated workbook.

The pack is built by the dataset-2 generator (40,000 people rows). Queries are
generic templates over that workbook, not contest items. Uncached timing uses
a same-mix sample when the full set would repeat a multi-minute parse per query.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gen_fake_dataset2 import XLSX_NAME, write_pack  # noqa: E402

FAST = (
    "Which region had the highest Sales in {year}? Answer with only the region name.",
    "How many women aged 25 to 54 were employed? Answer with only the integer.",
    "Which age group had the most unemployed women? Answer with only the age group.",
    "How many women aged 16 to 24 were employed? Answer with only the integer.",
)
SLOW = (
    "How many people scored at least {n}? Answer with only the integer.",
    "What score did the oldest person receive? Answer with only the integer score.",
)
YEARS = (2023, 2024, 2025)


def queries(n: int):
    rows = []
    for i in range(n):
        if i % 10 == 0:
            text = SLOW[(i // 10) % len(SLOW)].format(n=40 + (i % 50), year=YEARS[i % 3])
            kind = "record"
        else:
            text = FAST[i % len(FAST)].format(n=40 + (i % 50), year=YEARS[i % 3])
            kind = "sheet"
        rows.append({"id": f"q{i:04d}", "file_name": XLSX_NAME, "query": text, "kind": kind})
    return rows


def write_queries(data: Path, rows) -> None:
    (data / "queries.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ids = [row["id"] for row in rows]
    with open(data / "sample_submission.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "answer"])
        for qid in reversed(ids):
            writer.writerow([qid, ""])


def parse_time(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    elapsed = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", text)
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
    if not elapsed or not rss:
        raise SystemExit(f"time -v output missing elapsed or RSS in {path.name}")
    bits = elapsed.group(1).split(":")
    if len(bits) == 3:
        seconds = int(bits[0]) * 3600 + int(bits[1]) * 60 + float(bits[2])
    elif len(bits) == 2:
        seconds = int(bits[0]) * 60 + float(bits[1])
    else:
        seconds = float(bits[0])
    return seconds, int(rss.group(1)) / 1024.0


def parse_query_times(path: Path):
    times = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[1].endswith("s"):
            continue
        try:
            times.append(float(parts[1][:-1]))
        except ValueError:
            continue
    return times


def percentile(values, p):
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def run_mode(data: Path, out: Path, extra):
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = out.with_suffix(out.suffix + ".time")
    err = out.with_suffix(out.suffix + ".err")
    cmd = [
        "/usr/bin/time", "-v", "-o", str(stamp),
        sys.executable, str(ROOT / "run.py"),
        "--data", str(data), "--out", str(out),
        "--query-timeout", "60", *extra,
    ]
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    with open(err, "w", encoding="utf-8") as handle:
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, stderr=handle)
    if proc.returncode != 0:
        raise SystemExit(f"run.py exited {proc.returncode}; see {err}")
    wall, rss = parse_time(stamp)
    times = parse_query_times(err)
    return {"wall_s": wall, "rss_mb": rss, "times": times, "csv": out, "err": err}


def summarize(label, stats, query_count, estimated=False):
    times = stats["times"]
    return {
        "label": label,
        "queries": query_count if not estimated else f"{len(times)} sample, x{query_count} est",
        "wall_s": stats["wall_s"] if not estimated else stats["wall_s"] / max(len(times), 1) * query_count,
        "p50": percentile(times, 0.50),
        "p95": percentile(times, 0.95),
        "max": max(times) if times else float("nan"),
        "rss_mb": stats["rss_mb"],
        "estimated": estimated,
        "ran": len(times),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=300)
    ap.add_argument("--uncached-sample", type=int, default=20)
    ap.add_argument("--work", type=Path, default=None)
    args = ap.parse_args(argv)
    if not Path("/usr/bin/time").is_file():
        raise SystemExit("/usr/bin/time is required")
    work = args.work or Path(tempfile.mkdtemp(prefix="bench-cache-"))
    work.mkdir(parents=True, exist_ok=True)
    full = work / "full"
    write_pack(full)
    rows = queries(args.queries)
    write_queries(full, rows)
    sample_n = min(args.uncached_sample, args.queries)
    sample = work / "sample"
    sample.mkdir(parents=True, exist_ok=True)
    # Same workbook bytes; only the query list is shorter.
    os.symlink(full / "xlsx_files", sample / "xlsx_files")
    write_queries(sample, rows[:sample_n])

    print(f"work={work}", flush=True)
    print(f"cached sample n={sample_n}", flush=True)
    cached_sample = run_mode(sample, work / "sample-cached.csv", [])
    print(f"uncached sample n={sample_n}", flush=True)
    uncached_sample = run_mode(sample, work / "sample-uncached.csv", ["--no-cache"])
    same = cached_sample["csv"].read_bytes() == uncached_sample["csv"].read_bytes()
    print(f"sample csv identical={same}", flush=True)
    if not same:
        raise SystemExit("cached and uncached sample CSVs differ")

    print(f"cached full n={args.queries}", flush=True)
    cached_full = run_mode(full, work / "full-cached.csv", [])
    # Sample answers must match the same ids in the full cached file.
    full_rows = list(csv.reader(cached_full["csv"].open(encoding="utf-8")))
    full_ans = {row[0]: row[1] for row in full_rows[1:]}
    sample_rows = list(csv.reader(cached_sample["csv"].open(encoding="utf-8")))
    for row in sample_rows[1:]:
        if full_ans.get(row[0]) != row[1]:
            raise SystemExit(f"full run disagrees on {row[0]}")

    cached = summarize("cached", cached_full, args.queries)
    uncached = summarize("uncached (estimated)", uncached_sample, args.queries, estimated=True)
    slow = sum(1 for row in rows if row["kind"] == "record")
    fast = args.queries - slow

    def cell(stats):
        wall = stats["wall_s"]
        note = " estimated" if stats["estimated"] else ""
        return (
            f"{wall:.1f}{note}",
            f"{stats['p50']:.3f}",
            f"{stats['p95']:.3f}",
            f"{stats['max']:.3f}",
            f"{stats['rss_mb']:.1f}",
        )

    print()
    print(f"templates: {fast} sheet-level, {slow} record-level (every 10th). timeout=60s")
    print(f"uncached measured on {sample_n} queries with the same mix; wall scaled by {args.queries}/{sample_n}.")
    print(f"sample CSV diff: identical ({sample_n} rows)")
    print()
    print("| mode | wall s | per-query p50 | p95 | max | peak RSS MiB |")
    print("|---|---:|---:|---:|---:|---:|")
    for stats in (cached, uncached):
        wall, p50, p95, mx, rss = cell(stats)
        print(f"| {stats['label']} | {wall} | {p50} | {p95} | {mx} | {rss} |")
    print()
    print(f"uncached measured wall_s={uncached_sample['wall_s']:.3f} rss_mb={uncached_sample['rss_mb']:.1f}")
    print(f"cached measured wall_s={cached_full['wall_s']:.3f} rss_mb={cached_full['rss_mb']:.1f} queries={len(cached_full['times'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
