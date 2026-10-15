#!/usr/bin/env python3
"""python run.py --data D --out O/submission.csv [--log run_log.jsonl]"""
import argparse, csv, ctypes, gc, json, os, select, signal, sys, time
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.xlsx_tables import load_tables, summarize  # noqa: E402
from src import seams, llm_client, plan_validate  # noqa: E402

PLAN_SCHEMA_FILE = Path(__file__).resolve().parent / "src" / "plan_schema.json"
_SHEET_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls")
_MISS = object()
DEFAULT_TIMEOUT_S = 20.0
DEFAULT_RSS_MB = 640


class _QueryTimedOut(BaseException):
    """Not an Exception, so a broad `except Exception` cannot swallow it."""


class _RunState:
    def reset(self, out, header, order, flush_every):
        self.out = out
        self.header = list(header)
        self.order = list(order)
        self.answers = {}
        self.flush_every = max(int(flush_every), 1)
        self.pending = 0
        self.child = None
        self.flushing = False


_RUN = _RunState()
_RUN.reset(None, ["id", "answer"], [], 1)


def _rss_bytes():
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


def _release_memory():
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _table_nbytes(tables):
    total = 0
    for table in tables or []:
        frame = getattr(table, "df", None)
        if frame is None:
            continue
        try:
            total += int(frame.memory_usage(deep=True).sum())
        except Exception:
            total += 1024
    return total


class TableCache:
    """Parsed workbooks keyed by resolved path, mtime, and size.

    Least-recently-used entries are dropped when the retained frames or the
    process RSS pass the budget. The workbook needed for the current query is
    kept even if it alone is large. A sticky RSS high-water mark does not
    wipe the cache: eviction stops when freeing objects does not shrink RSS.
    """

    def __init__(self, enabled=True, byte_budget=None, rss_budget=None):
        self.enabled = enabled
        self.rss_budget = int(DEFAULT_RSS_MB * 1024 * 1024 if rss_budget is None else rss_budget)
        self.byte_budget = self.rss_budget if byte_budget is None else int(byte_budget)
        self._data = OrderedDict()
        self._bytes = 0

    @classmethod
    def coerce(cls, obj):
        if isinstance(obj, cls):
            return obj
        impl = obj.get("__table_cache__") if isinstance(obj, dict) else None
        if not isinstance(impl, cls):
            impl = cls()
            if isinstance(obj, dict):
                obj["__table_cache__"] = impl
        return impl

    def _key(self, path: Path):
        st = path.stat()
        return (str(path.resolve()), st.st_mtime_ns, st.st_size)

    def lookup(self, path: Path):
        if not self.enabled or not path.is_file():
            return _MISS
        key = self._key(path)
        item = self._data.get(key)
        if item is None:
            return _MISS
        self._data.move_to_end(key)
        return item[0]

    def make_room(self):
        if not self.enabled:
            return
        while self._data and self._bytes > self.byte_budget:
            self._discard(next(iter(self._data)))
            _release_memory()

    def store(self, path: Path, tables):
        if not self.enabled or not path.is_file():
            return
        key = self._key(path)
        for old in [k for k in self._data if k[0] == key[0] and k != key]:
            self._discard(old)
        if key in self._data:
            self._discard(key)
        nbytes = _table_nbytes(tables)
        self._data[key] = (tables, nbytes)
        self._bytes += nbytes
        self._data.move_to_end(key)
        self._evict(protect=key)

    def _discard(self, key):
        item = self._data.pop(key, None)
        if item is not None:
            self._bytes = max(0, self._bytes - item[1])

    def _evict(self, protect):
        while len(self._data) > 1 and self._bytes > self.byte_budget:
            victim = next((k for k in self._data if k != protect), None)
            if victim is None:
                break
            self._discard(victim)
            _release_memory()
        while len(self._data) > 1 and _rss_bytes() > self.rss_budget:
            before = _rss_bytes()
            victim = next((k for k in self._data if k != protect), None)
            if victim is None:
                break
            self._discard(victim)
            _release_memory()
            if _rss_bytes() >= before - 1024 * 1024:
                break


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


def _timeout_setting(cli):
    if cli is not None:
        return float(cli)
    raw = os.environ.get("DS2_QUERY_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    return float(raw)


def _rss_setting(cli):
    if cli is not None:
        return float(cli)
    raw = os.environ.get("DS2_CACHE_RSS_MB", "").strip()
    if not raw:
        return float(DEFAULT_RSS_MB)
    return float(raw)


def _deadline(timeout):
    if timeout is None:
        timeout = _timeout_setting(None)
    timeout = float(timeout)
    if timeout <= 0:
        return None
    return time.monotonic() + timeout


def _remain(deadline):
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _on_alarm(signum, frame):
    raise _QueryTimedOut()


def _run_alarmed(fn, timeout):
    """Run fn on this thread. SIGALRM interrupts Python code, including openpyxl's cell loop."""
    if timeout is None:
        return fn()
    if timeout <= 0:
        raise _QueryTimedOut()
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, max(float(timeout), 1e-3))
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        signal.signal(signal.SIGALRM, previous)


def _kill_pid(pid):
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass


def _run_forked(fn, timeout):
    """Run fn in a child. The parent's cache is not in the child's writes.

    join-equivalent: the parent waits on the result pipe and SIGKILLs the
    child when the timeout fires, including when fn is inside a C extension.
    """
    if timeout is None:
        return fn()
    if timeout <= 0:
        raise _QueryTimedOut()
    if not hasattr(os, "fork"):
        return _run_alarmed(fn, timeout)
    read_fd, write_fd = os.pipe()
    try:
        pid = os.fork()
    except OSError:
        os.close(read_fd)
        os.close(write_fd)
        return _run_alarmed(fn, timeout)
    if pid == 0:
        os.close(read_fd)
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.setitimer(signal.ITIMER_REAL, 0)
            try:
                ans = fn()
                if ans is not None and not isinstance(ans, str):
                    ans = str(ans)
                payload = json.dumps({"ok": True, "ans": ans}, ensure_ascii=False).encode()
            except BaseException as exc:
                payload = json.dumps(
                    {"ok": False, "err": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
                ).encode()
            try:
                view = payload
                while view:
                    n = os.write(write_fd, view)
                    if n <= 0:
                        break
                    view = view[n:]
            except Exception:
                pass
        finally:
            try:
                os.close(write_fd)
            except Exception:
                pass
            os._exit(0)
    os.close(write_fd)
    _RUN.child = pid
    timed_out = False
    chunks = []
    try:
        end = time.monotonic() + float(timeout)
        while True:
            remain = end - time.monotonic()
            if remain <= 0:
                timed_out = True
                break
            try:
                ready, _, _ = select.select([read_fd], [], [], remain)
            except InterruptedError:
                continue
            if not ready:
                timed_out = True
                break
            try:
                chunk = os.read(read_fd, 65536)
            except InterruptedError:
                continue
            if not chunk:
                break
            chunks.append(chunk)
        if timed_out:
            raise _QueryTimedOut()
        try:
            msg = json.loads(b"".join(chunks).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"worker reply: {exc}") from exc
        if not msg.get("ok"):
            raise RuntimeError(msg.get("err") or "worker failed")
        return msg.get("ans")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        if _RUN.child == pid:
            _RUN.child = None
        _kill_pid(pid)


def _load_for_query(cache, path, deadline):
    hit = cache.lookup(path)
    if hit is not _MISS:
        return hit
    cache.make_room()
    tables = _run_alarmed(lambda: load_tables(str(path)), _remain(deadline))
    cache.store(path, tables)
    return tables


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


def answer_one(q, data_dir, cache, timeout=None):
    budget = llm_client.Budget()
    fname = q.get("file_name", "")
    path = _resolve_workbook(data_dir, fname)
    cache = TableCache.coerce(cache)
    deadline = _deadline(timeout)
    ans = None
    try:
        tables = _load_for_query(cache, path, deadline)
        ans = _run_forked(lambda: _rule_answer(q["query"], tables, fname), _remain(deadline))
        if ans is None and llm_client.enabled() and seams.have_executor():
            ans = _run_alarmed(
                lambda: _llm_then_rule(q, tables, fname, budget), _remain(deadline)
            )
    except _QueryTimedOut:
        print(f"[timeout] {q.get('id')}", file=sys.stderr)
        ans = None
    except Exception as e:  # never crash the whole run
        print(f"[warn] {q.get('id')}: {type(e).__name__}: {e}", file=sys.stderr)
        ans = None
    return ("" if ans is None else str(ans)), budget


def _atomic_csv(path, header, order, answers):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    pad = [""] * max(len(header) - 2, 0)
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for qid in order:
            writer.writerow([qid, answers.get(qid, "")] + pad)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _flush_submission():
    if _RUN.out is None or _RUN.flushing:
        return
    _RUN.flushing = True
    try:
        _atomic_csv(_RUN.out, _RUN.header, _RUN.order, _RUN.answers)
    finally:
        _RUN.flushing = False


def _on_sigterm(signum, frame):
    pid = _RUN.child
    _RUN.child = None
    _kill_pid(pid)
    _flush_submission()
    raise SystemExit(128 + int(signum))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log", default=None, help="JSONL log (default: <out dir>/run_log.jsonl)")
    ap.add_argument("--query-timeout", type=float, default=None,
                    help="Seconds per query (default 20, or DS2_QUERY_TIMEOUT). 0 disables.")
    ap.add_argument("--cache-rss-mb", type=float, default=None,
                    help="RSS / frame budget in MiB (default 640, or DS2_CACHE_RSS_MB).")
    ap.add_argument("--no-cache", action="store_true", help="Parse each workbook on every query.")
    ap.add_argument("--flush-every", type=int, default=1,
                    help="Rewrite the CSV every N finished queries.")
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
    timeout = _timeout_setting(a.query_timeout)
    cache = TableCache(
        enabled=not a.no_cache,
        rss_budget=int(_rss_setting(a.cache_rss_mb) * 1024 * 1024),
    )
    _RUN.reset(out, header, order, a.flush_every)
    previous_term = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        _flush_submission()
        with open(log, "w", encoding="utf-8") as lf:
            for qid in sorted(order, key=lambda i: byid[i].get("file_name", "")):
                t = time.time()
                try:
                    ans, b = answer_one(byid[qid], data, cache, timeout=timeout)
                except KeyboardInterrupt:
                    _flush_submission()
                    raise
                _RUN.answers[qid] = ans
                lf.write(json.dumps({"id": qid, "tokens": b.tokens,
                                     "completion_tokens": b.completion_tokens,
                                     "reasoning_tokens": b.reasoning_tokens,
                                     "attempts": b.log,
                                     "cost_usd": round(b.spent, 6)}) + "\n")
                lf.flush()
                print(f"{qid}\t{time.time()-t:.4f}s\t{ans!r}", file=sys.stderr)
                _RUN.pending += 1
                if _RUN.pending >= _RUN.flush_every:
                    _flush_submission()
                    _RUN.pending = 0
        _flush_submission()
    except KeyboardInterrupt:
        _flush_submission()
        raise
    finally:
        try:
            _flush_submission()
        except Exception:
            pass
        signal.signal(signal.SIGTERM, previous_term)
        _kill_pid(_RUN.child)
        _RUN.child = None
    return 0


if __name__ == "__main__":
    sys.exit(main())
