#!/usr/bin/env python3
"""Contest entrypoint.

  python run.py --xlsx-dir DIR --queries FILE --output CSV \\
      --openrouter-key KEY --openrouter-model MODEL --openrouter-base-url URL

Legacy flags still work and map onto the same paths:
  python run.py --data D --out O/submission.csv [--log run_log.jsonl]
"""
import argparse, csv, ctypes, gc, json, multiprocessing, os, pickle, signal, sys, tempfile, time
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.xlsx_tables import load_tables, summarize  # noqa: E402
from src import seams, llm_client, plan_validate  # noqa: E402

PLAN_SCHEMA_FILE = Path(__file__).resolve().parent / "src" / "plan_schema.json"
_SHEET_SUFFIXES = (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls")
_MISS = object()
DEFAULT_TIMEOUT_S = 90.0
PROCESSING_LIMIT_S = 2390.0
FINALIZE_RESERVE_S = 10.0
DEFAULT_PRELOAD_S = 120.0
DEFAULT_RSS_MB = 640
_KILL_GRACE_S = 0.5


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
        self.proc = None
        self.flushing = False
        self.timed_out = []
        self.deadline_skip = []
        self.child_hwm_kb = 0


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


def _preload_setting(cli):
    if cli is not None:
        return float(cli)
    raw = os.environ.get("DS2_PRELOAD_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_PRELOAD_S
    return float(raw)


def _positive_timeout(value):
    """A positive limit in seconds, or None when the limit is disabled."""
    if value is None:
        value = _timeout_setting(None)
    value = float(value)
    if value <= 0:
        return None
    return value


def _fork_context():
    try:
        return multiprocessing.get_context("fork")
    except ValueError:
        return None


def _want_fork(force_in_process=False):
    """True when children can be created with the fork start method.

    Fork shares the parent's parsed tables. Spawn, Windows, --in-process,
    and DS2_IN_PROCESS stay in this process. A thread cannot be killed, and
    signal.alarm is not available on Windows, so that path has no per-query
    interrupt.
    """
    if force_in_process:
        return False
    flag = os.environ.get("DS2_IN_PROCESS", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return False
    method = os.environ.get("DS2_START_METHOD", "").strip().lower()
    if method and method != "fork":
        return False
    if not hasattr(os, "fork"):
        return False
    return _fork_context() is not None


def _remaining(started, limit):
    if limit is None:
        return None
    return limit - (time.monotonic() - started)


def _on_alarm(signum, frame):
    raise _QueryTimedOut()


def _run_alarmed(fn, timeout):
    """Interrupt parent-side review/LLM work on POSIX.

    This is not the no-fork fallback. That path cannot interrupt a query that
    has already started.
    """
    if timeout is None:
        return fn()
    if timeout <= 0:
        raise _QueryTimedOut()
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
        return fn()
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


_PENDING = None


def _reset_child_signals():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        if hasattr(signal, "setitimer"):
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
            except Exception:
                pass


def _fork_target(conn):
    """Run the callable inherited from the parent. Tables are not pickled."""
    _reset_child_signals()
    fn = _PENDING
    try:
        conn.send((True, fn()))
    except BaseException as exc:
        try:
            conn.send((False, f"{type(exc).__name__}: {exc}"))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _proc_hwm_kb(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except OSError:
        return 0
    return 0


def _note_child_hwm(pid):
    kb = _proc_hwm_kb(pid)
    if kb > _RUN.child_hwm_kb:
        _RUN.child_hwm_kb = kb


def _stop_process(proc):
    """SIGTERM, a short grace, then SIGKILL. Reap the child either way."""
    if proc is None:
        return
    try:
        alive = proc.is_alive()
    except Exception:
        alive = False
    if alive:
        _note_child_hwm(proc.pid)
        try:
            proc.terminate()
        except Exception:
            pass
        proc.join(timeout=_KILL_GRACE_S)
        try:
            alive = proc.is_alive()
        except Exception:
            alive = False
        if alive:
            try:
                proc.kill()
            except Exception:
                pass
            proc.join(timeout=2)
            try:
                if proc.is_alive():
                    _kill_pid(proc.pid)
                    proc.join(timeout=1)
            except Exception:
                pass
    else:
        proc.join(timeout=1)
    if getattr(proc, "pid", None) and _RUN.child == proc.pid:
        _RUN.child = None
    if _RUN.proc is proc:
        _RUN.proc = None
    try:
        if not proc.is_alive():
            proc.close()
    except Exception:
        pass


def _run_forked(fn, timeout):
    """Run fn in a forked child. The parent kills that child when time is up.

    The clock starts as the child is created. SIGTERM is sent at the deadline,
    then SIGKILL after a short grace. The child shares the parent's memory via
    fork. fn's return value is pickled back and must stay small.
    timeout None runs fn here. timeout <= 0 means the budget is already spent.
    """
    global _PENDING
    if timeout is None:
        return fn()
    if timeout <= 0:
        raise _QueryTimedOut()
    ctx = _fork_context()
    if ctx is None:
        raise RuntimeError("fork is not available")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    _PENDING = fn
    proc = ctx.Process(target=_fork_target, args=(child_conn,))
    deadline = time.monotonic() + float(timeout)
    try:
        proc.start()
    except Exception:
        _PENDING = None
        parent_conn.close()
        child_conn.close()
        raise
    # The child already copied this page. Drop the parent's reference.
    _PENDING = None
    child_conn.close()
    _RUN.child = proc.pid
    _RUN.proc = proc
    try:
        message = None
        while message is None:
            _note_child_hwm(proc.pid)
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise _QueryTimedOut()
            if not parent_conn.poll(remain):
                continue
            try:
                message = parent_conn.recv()
            except EOFError as exc:
                raise RuntimeError("worker reply missing") from exc
        ok, payload = message
        if not ok:
            raise RuntimeError(payload)
        return payload
    finally:
        try:
            parent_conn.close()
        except Exception:
            pass
        _stop_process(proc)


def _dump_tables(path, dest):
    tables = load_tables(str(path))
    partial = dest + ".writing"
    with open(partial, "wb") as handle:
        pickle.dump(tables, handle, protocol=pickle.HIGHEST_PROTOCOL)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, dest)
    return True


def _load_killable(path, timeout):
    """Parse path in a child and unpickle the tables into this process.

    Killing the child is what stops a stuck parse. The parent stores the
    tables only after they unpickle, so a killed child cannot half-fill the cache.
    """
    fd, dest = tempfile.mkstemp(prefix="ds2-tables-", suffix=".pkl")
    os.close(fd)
    partial = dest + ".writing"
    try:
        _run_forked(lambda: _dump_tables(path, dest), timeout)
        with open(dest, "rb") as handle:
            return pickle.load(handle)
    finally:
        for name in (dest, partial):
            try:
                os.unlink(name)
            except OSError:
                pass


def _load_into_parent(path, use_fork, preload_timeout):
    """Parse within the remaining shared query deadline.

    With fork, the loader child is killed at preload_timeout. Without fork the
    parse runs to completion; it cannot be interrupted.
    """
    limit = float(preload_timeout)
    if (not use_fork) or limit <= 0:
        return load_tables(str(path))
    return _load_killable(path, limit)


def _cached_tables(cache, path, use_fork, preload_timeout):
    hit = cache.lookup(path)
    if hit is not _MISS:
        return hit
    cache.make_room()
    tables = _load_into_parent(path, use_fork, preload_timeout)
    cache.store(path, tables)
    return tables


def _uncached_rule(path, query, fname, dest=None):
    """Load and answer inside one child. The parent's limit covers both.

    dest, when set, receives a pickle of the tables before the answer is
    computed, still inside this child. The parent stores that pickle only
    after the child returns.
    """
    tables = load_tables(str(path))
    if dest:
        partial = dest + ".writing"
        with open(partial, "wb") as handle:
            pickle.dump(tables, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, dest)
    return _rule_answer(query, tables, fname)


def _fork_miss(path, query, fname, limit, cache, allow_store):
    """Parse and answer in one child. limit applies to that whole child."""
    dest = None
    partial = None
    if allow_store and cache.enabled:
        fd, dest = tempfile.mkstemp(prefix="ds2-tables-", suffix=".pkl")
        os.close(fd)
        partial = dest + ".writing"
    try:
        ans = _run_forked(lambda: _uncached_rule(path, query, fname, dest), limit)
        if dest and os.path.isfile(dest) and os.path.getsize(dest) > 0:
            with open(dest, "rb") as handle:
                tables = pickle.load(handle)
            cache.make_room()
            cache.store(path, tables)
        return ans
    finally:
        for name in (dest, partial):
            if not name:
                continue
            try:
                os.unlink(name)
            except OSError:
                pass


def _answer_rule(query, tables, fname, limit, use_fork, started):
    if use_fork and limit is not None:
        return _run_forked(
            lambda: _rule_answer(query, tables, fname),
            _remaining(started, limit),
        )
    return _rule_answer(query, tables, fname)


def _answer_llm(q, tables, fname, budget, limit, use_fork, started):
    left = _remaining(started, limit)
    if use_fork and left is not None and hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM"):
        return _run_alarmed(lambda: _llm_then_rule(q, tables, fname, budget), left)
    return _llm_then_rule(q, tables, fname, budget)


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
        from src.plan_review import choose
        ans = choose(query, tables, fname, budget, schema, q.get("id", ""))
    except Exception as e:
        print(f"[warn] {q.get('id')}: LLM {type(e).__name__}: {e}", file=sys.stderr)
        from src.plan_review import safe_rule
        ans = safe_rule(query, tables, fname)
    return ans


def _note_timeout(qid):
    if qid is None or qid in _RUN.timed_out:
        return
    _RUN.timed_out.append(qid)


def answer_one(q, data_dir, cache, timeout=None, *, use_fork=None, preload_timeout=None, whole_query=False):
    """Answer one query.

    With fork, parsing and rule planning run in killable child phases under
    one clock. Parent-side review and LLM calls use the remaining time. Parsed
    tables are cached lazily, including when later rule planning times out.
    Without POSIX process/alarm support, parsing and Python computation cannot
    be forcibly interrupted; HTTP calls still receive the shared deadline.
    """
    if use_fork is None:
        use_fork = _want_fork(False)
    limit = _positive_timeout(timeout)
    if preload_timeout is None:
        preload_timeout = _preload_setting(None)
    else:
        preload_timeout = float(preload_timeout)
    budget = llm_client.Budget()
    fname = q.get("file_name", "")
    path = _resolve_workbook(data_dir, fname)
    cache = TableCache.coerce(cache)
    ans = None
    tables = None
    started = time.monotonic()
    budget.deadline = None if limit is None else started + limit
    try:
        hit = _MISS if whole_query or not cache.enabled else cache.lookup(path)
        if hit is _MISS:
            cache.make_room()
            left = _remaining(started, limit)
            if left is not None and left <= 0:
                raise _QueryTimedOut()
            parse_limit = min(preload_timeout, left) if left is not None and preload_timeout > 0 else left
            tables = _load_into_parent(path, use_fork, parse_limit or 0)
            if cache.enabled and not whole_query:
                cache.store(path, tables)
        else:
            tables = hit
        # Parsing, planning, validation and every retry use this same clock.
        # No clock reset after a cache miss.
        if limit is not None and _remaining(started, limit) <= 0:
            raise _QueryTimedOut()
        ans = _answer_rule(q["query"], tables, fname, limit, use_fork, started)
        review = False
        if ans is not None and tables is not None and llm_client.enabled():
            from src.plan_review import needs_review
            review = _run_alarmed(lambda: needs_review(q["query"], tables), _remaining(started, limit))
        if (ans is None or review) and llm_client.enabled() and seams.have_executor():
            if tables is None:
                left = _remaining(started, limit)
                if left is not None and left <= 0:
                    raise _QueryTimedOut()
                tables = _load_into_parent(
                    path, use_fork, left if left is not None else preload_timeout
                )
            ans = _answer_llm(q, tables, fname, budget, limit, use_fork, started)
    except _QueryTimedOut:
        _note_timeout(q.get("id"))
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
    proc = _RUN.proc
    _RUN.proc = None
    pid = _RUN.child
    _RUN.child = None
    if proc is not None:
        _stop_process(proc)
    else:
        _kill_pid(pid)
    _flush_submission()
    raise SystemExit(128 + int(signum))


def _report_timeouts():
    killed = ", ".join(str(i) for i in _RUN.timed_out) or "none"
    skipped = ", ".join(str(i) for i in _RUN.deadline_skip) or "none"
    print(f"[timed-out] {killed}", file=sys.stderr)
    print(f"[deadline-skip] {skipped}", file=sys.stderr)
    print(f"[child-hwm-kb] {_RUN.child_hwm_kb}", file=sys.stderr)


def _path_key(path):
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _preload(cache, data, byid, execution, use_fork, preload_timeout):
    """Parse each workbook once, before any answer timer starts.

    A file that exceeds the preload limit is not cached. Its queries then run
    load and answer together under the answer timeout, in a killable child.
    Without fork, this parse cannot be stopped; it runs to completion and is cached.
    """
    skipped = set()
    if not cache.enabled:
        return skipped
    seen = set()
    for qid in execution:
        path = _resolve_workbook(data, byid[qid].get("file_name", ""))
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        try:
            cache.make_room()
            tables = _load_into_parent(path, use_fork, preload_timeout)
            cache.store(path, tables)
        except _QueryTimedOut:
            skipped.add(key)
            affected = _affected_queries(data, byid, execution, key)
            print(
                f"[preload-timeout] {path.name}: not cached; "
                f"{affected} queries will parse inside the answer timeout and may be blank",
                file=sys.stderr,
            )
        except Exception as exc:
            skipped.add(key)
            affected = _affected_queries(data, byid, execution, key)
            print(
                f"[preload-failed] {path.name}: {type(exc).__name__}: {exc}; "
                f"not cached; {affected} queries will parse inside the answer timeout and may be blank",
                file=sys.stderr,
            )
    return skipped


def _affected_queries(data, byid, execution, key):
    count = 0
    for qid in execution:
        path = _resolve_workbook(data, byid[qid].get("file_name", ""))
        if _path_key(path) == key:
            count += 1
    return count


def _completions_url(base):
    text = str(base).strip().rstrip("/")
    if text.endswith("/chat/completions"):
        return text
    return text + "/chat/completions"


def _apply_openrouter_cli(key, model, base_url):
    """Take OpenRouter settings from CLI. Absent flags leave the process as-is."""
    saved = {}
    if key is not None:
        saved["key"] = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = key
    if model is not None and str(model).strip():
        saved["model"] = llm_client.MODEL
        llm_client.MODEL = str(model).strip()
    if base_url is not None and str(base_url).strip():
        saved["url"] = llm_client.URL
        llm_client.URL = _completions_url(base_url)
    return saved


def _restore_openrouter_cli(saved):
    if "key" in saved:
        if saved["key"] is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = saved["key"]
    if "model" in saved:
        llm_client.MODEL = saved["model"]
    if "url" in saved:
        llm_client.URL = saved["url"]


def _read_queries(path):
    path = Path(path)
    if path.is_dir():
        path = path / "queries.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("queries", [])
    return _dedupe_queries(payload)


class _StderrLog:
    """JSONL log lines for the contest path. Nothing is written beside the CSV."""

    def write(self, text):
        sys.stderr.write(text)
        return len(text)

    def flush(self):
        sys.stderr.flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.flush()
        return False


def _open_run_log(path):
    if path is None:
        return _StderrLog()
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "w", encoding="utf-8")


def main(argv=None):
    run_started = time.monotonic()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--xlsx-dir", default=None)
    ap.add_argument("--queries", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--openrouter-key", default=None)
    ap.add_argument("--openrouter-model", default=None)
    ap.add_argument("--openrouter-base-url", default=None)
    ap.add_argument("--log", default=None, help="JSONL log (legacy default: <out dir>/run_log.jsonl)")
    ap.add_argument("--query-timeout", type=float, default=None,
                    help="Seconds for the whole query (default 90, or DS2_QUERY_TIMEOUT). "
                         "The parent sends SIGTERM at this limit, then SIGKILL. "
                         "A cache hit times only the answer. A cache miss times parse and answer together. "
                         "0 disables. Without fork this does not interrupt a running query; "
                         "queries stop being started after timeout times the query count.")
    ap.add_argument("--preload-timeout", type=float, default=None,
                    help="Legacy maximum parse time inside the shared query deadline "
                         "(default 120, or DS2_PRELOAD_TIMEOUT). "
                         "With fork the loader is killed at this limit. "
                         "Without fork a parse runs to completion.")
    ap.add_argument("--cache-rss-mb", type=float, default=None,
                    help="RSS / frame budget in MiB (default 640, or DS2_CACHE_RSS_MB).")
    ap.add_argument("--no-cache", action="store_true",
                    help="Do not keep parsed tables. Each query parses and answers "
                         "in one child under the query timeout.")
    ap.add_argument("--in-process", action="store_true",
                    help="Do not fork. Same path as Windows or DS2_START_METHOD=spawn. "
                         "Also enabled by DS2_IN_PROCESS=1.")
    ap.add_argument("--flush-every", type=int, default=1,
                    help="Rewrite the CSV every N finished queries.")
    a = ap.parse_args(argv)
    btc_io = bool(a.xlsx_dir or a.queries or a.output)
    xlsx_dir = a.xlsx_dir or a.data
    queries_arg = a.queries or (str(Path(a.data) / "queries.json") if a.data else None)
    output = a.output or a.out
    if not xlsx_dir or not queries_arg or not output:
        ap.error("need --xlsx-dir, --queries, and --output (or legacy --data and --out)")
    data = Path(xlsx_dir)
    queries = _read_queries(queries_arg)
    header, order = ["id", "answer"], [q["id"] for q in queries]
    if not btc_io:
        sample = Path(a.data) / "sample_submission.csv"
        if sample.exists():
            with open(sample, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            header = rows[0]
            ids = _unique(r[0] for r in rows[1:] if r)
            if set(ids) == set(order):
                order = ids
    byid = {q["id"]: q for q in queries}
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.log:
        log_path = Path(a.log)
    elif btc_io:
        log_path = None
    else:
        log_path = out.parent / "run_log.jsonl"
    openrouter_saved = _apply_openrouter_cli(
        a.openrouter_key, a.openrouter_model, a.openrouter_base_url
    )
    timeout = _timeout_setting(a.query_timeout)
    preload_timeout = _preload_setting(a.preload_timeout)
    use_fork = _want_fork(a.in_process)
    cache = TableCache(
        enabled=not a.no_cache,
        rss_budget=int(_rss_setting(a.cache_rss_mb) * 1024 * 1024),
    )
    execution = sorted(order, key=lambda i: byid[i].get("file_name", ""))
    _RUN.reset(out, header, order, a.flush_every)
    previous_term = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        _flush_submission()
        # Load each workbook on its first question. Setup and parsing count
        # toward the 2,390-second processing window, leaving ten seconds for CSV.
        run_deadline = run_started + PROCESSING_LIMIT_S
        if not use_fork and timeout > 0:
            run_deadline = min(run_deadline, run_started + timeout * max(len(execution), 1))
        with _open_run_log(log_path) as lf:
            for index, qid in enumerate(execution):
                if run_deadline is not None and time.monotonic() >= run_deadline:
                    for rest in execution[index:]:
                        _RUN.answers[rest] = ""
                        _RUN.deadline_skip.append(rest)
                        lf.write(json.dumps({"id": rest, "tokens": 0,
                                             "completion_tokens": 0,
                                             "reasoning_tokens": 0,
                                             "attempts": [],
                                             "cost_usd": 0.0}) + "\n")
                        print(f"{rest}\tdeadline-skip\t''", file=sys.stderr)
                    lf.flush()
                    _flush_submission()
                    break
                t = time.perf_counter()
                left = run_deadline - time.monotonic()
                query_limit = min(timeout, left) if timeout > 0 else left
                try:
                    ans, b = answer_one(
                        byid[qid], data, cache, timeout=query_limit,
                        use_fork=use_fork, preload_timeout=preload_timeout,
                    )
                except KeyboardInterrupt:
                    _flush_submission()
                    raise
                _RUN.answers[qid] = ans
                lf.write(json.dumps({"id": qid, "tokens": b.tokens,
                                     "completion_tokens": b.completion_tokens,
                                     "reasoning_tokens": b.reasoning_tokens,
                                     "attempts": b.log,
                                     "cost_usd": b.cost_usd,
                                     "actual_cost_usd": b.actual_usd,
                                     "unresolved_cost_usd": b.unresolved_usd,
                                     "budget_exposure_usd": b.spent,
                                     "token_usage_unknown": b.token_usage_unknown,
                                     "decision": getattr(b, "decision", {"source": "rule"}),
                                     "elapsed_seconds": time.perf_counter()-t}) + "\n")
                lf.flush()
                print(f"{qid}\t{time.perf_counter()-t:.4f}s\t{ans!r}", file=sys.stderr)
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
        try:
            _report_timeouts()
        except Exception:
            pass
        signal.signal(signal.SIGTERM, previous_term)
        _kill_pid(_RUN.child)
        _RUN.child = None
        _restore_openrouter_cli(openrouter_saved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
