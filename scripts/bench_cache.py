#!/usr/bin/env python3
"""Time cached and uncached runs on a generated workbook.

The pack is built by the dataset-2 generator (40,000 people rows). Queries are
generic templates over that workbook, not contest items. Both modes run the
full query list at the default 20s answer timeout.
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


def blanks(path: Path):
    rows = list(csv.reader(path.open(encoding="utf-8")))
    return [row[0] for row in rows[1:] if len(row) < 2 or row[1] == ""]


def timed_out_ids(path: Path):
    found = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("[timed-out] "):
            continue
        rest = line[len("[timed-out] "):].strip()
        if rest and rest != "none":
            found = [part.strip() for part in rest.split(",") if part.strip()]
        else:
            found = []
    return found


def child_hwm_mb(path: Path):
    match = re.search(r"\[child-hwm-kb\] (\d+)", path.read_text(encoding="utf-8", errors="replace"))
    if not match:
        return 0.0
    return int(match.group(1)) / 1024.0


def run_mode(data: Path, out: Path, extra):
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = out.with_suffix(out.suffix + ".time")
    err = out.with_suffix(out.suffix + ".err")
    cmd = [
        "/usr/bin/time", "-v", "-o", str(stamp),
        sys.executable, str(ROOT / "run.py"),
        "--data", str(data), "--out", str(out),
        *extra,
    ]
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    with open(err, "w", encoding="utf-8") as handle:
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, stderr=handle)
    if proc.returncode != 0:
        raise SystemExit(f"run.py exited {proc.returncode}; see {err}")
    wall, rss = parse_time(stamp)
    times = parse_query_times(err)
    child = child_hwm_mb(err)
    return {
        "wall_s": wall,
        "rss_mb": rss,
        "child_rss_mb": child,
        "peak_rss_mb": max(rss, child),
        "times": times,
        "csv": out,
        "err": err,
        "blanks": blanks(out),
        "timed_out": timed_out_ids(err),
    }


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
        "child_rss_mb": stats["child_rss_mb"],
        "peak_rss_mb": stats["peak_rss_mb"],
        "blanks": len(stats["blanks"]),
        "timed_out": stats["timed_out"],
        "estimated": estimated,
        "ran": len(times),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=300)
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

    print(f"work={work}", flush=True)
    print(f"cached full n={args.queries} timeout=20s", flush=True)
    cached_full = run_mode(full, work / "full-cached.csv", [])
    print(
        f"cached wall={cached_full['wall_s']:.1f}s blanks={len(cached_full['blanks'])} "
        f"timed_out={cached_full['timed_out'] or 'none'}",
        flush=True,
    )
    print(f"uncached full n={args.queries} timeout=20s", flush=True)
    uncached_full = run_mode(full, work / "full-uncached.csv", ["--no-cache"])
    same = cached_full["csv"].read_bytes() == uncached_full["csv"].read_bytes()
    print(f"full csv identical={same}", flush=True)
    if not same:
        raise SystemExit("cached and uncached CSVs differ")

    cached = summarize("cached", cached_full, args.queries)
    uncached = summarize("uncached", uncached_full, args.queries)
    slow = sum(1 for row in rows if row["kind"] == "record")
    fast = args.queries - slow

    def cell(stats):
        return (
            f"{stats['wall_s']:.1f}",
            f"{stats['p50']:.3f}",
            f"{stats['p95']:.3f}",
            f"{stats['max']:.3f}",
            f"{stats['peak_rss_mb']:.1f}",
            str(stats["blanks"]),
        )

    print()
    print(f"templates: {fast} sheet-level, {slow} record-level (every 10th). answer timeout=20s, preload=120s")
    print("peak RSS is the greater of the parent ru_maxrss and the sampled fork-child VmHWM.")
    print(f"full CSV diff: identical ({args.queries} rows)")
    print(f"cached timed-out: {cached['timed_out'] or 'none'}")
    print(f"uncached timed-out: {uncached['timed_out'] or 'none'}")
    print()
    print("| mode | wall s | per-query p50 | p95 | max | peak RSS MiB | blanks |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for stats in (cached, uncached):
        wall, p50, p95, mx, rss, blank_n = cell(stats)
        print(f"| {stats['label']} | {wall} | {p50} | {p95} | {mx} | {rss} | {blank_n} |")
    print()
    print(
        f"cached parent_rss_mb={cached_full['rss_mb']:.1f} child_rss_mb={cached_full['child_rss_mb']:.1f} "
        f"queries={len(cached_full['times'])}"
    )
    print(
        f"uncached parent_rss_mb={uncached_full['rss_mb']:.1f} child_rss_mb={uncached_full['child_rss_mb']:.1f} "
        f"queries={len(uncached_full['times'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
