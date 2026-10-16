"""Run the original image command and report kernel cgroup-v2 peak memory.

Mounted read-only for the measured CI run, never copied into the contest image.
The cgroup metric includes this small supervisor, the solver, its descendants,
and charged file cache. It is not a sampled RSS estimate.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

PREFIX = "PUBLIC1_MEMORY_JSON="


def main(command):
    import resource  # Linux-only; this script executes inside the container.

    cgroup = Path("/sys/fs/cgroup")

    def number(name):
        return int((cgroup / name).read_text(encoding="ascii").strip())

    def events():
        return {
            key: int(value)
            for key, value in (
                line.split() for line in
                (cgroup / "memory.events").read_text(encoding="ascii").splitlines()
            )
        }

    # Fail before starting if reliable whole-container telemetry is unavailable.
    number("memory.peak")
    limit = number("memory.max")
    swap_limit = number("memory.swap.max")
    before = events()
    started = time.perf_counter()
    result = subprocess.run(command, check=False)
    elapsed = time.perf_counter() - started
    after = events()
    record = {
        "measurement": "cgroup-v2 memory.peak (includes supervisor and file cache)",
        "solver_exit_code": result.returncode,
        "solver_wall_seconds": round(elapsed, 6),
        "peak_bytes": number("memory.peak"),
        "limit_bytes": limit,
        "swap_limit_bytes": swap_limit,
        "swap_current_bytes": number("memory.swap.current"),
        "events_delta": {key: after[key] - before.get(key, 0) for key in after},
        # Supplementary single-process high-water mark; not aggregate container RAM.
        "children_maxrss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    }
    print(PREFIX + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: ci_memory_probe.py COMMAND [ARGS...]")
    raise SystemExit(main(sys.argv[1:]))
