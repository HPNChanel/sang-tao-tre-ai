"""Private evaluation transport and a durable, single-writer USD 5 ledger.

This is evaluation infrastructure, excluded from the submission ZIP. It never
loads a golden answer and never writes prompts, completions, or credentials to
its ledger. A reservation is an operational control, not a provider billing
guarantee. Only an explicit, finite usage.cost reconciles a paid attempt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

MODEL = "qwen/qwen3.7-flash"
MAX_BODY = 24 * 1024
RESERVE = Decimal("0.03")
QUESTION_CAP = Decimal("0.09")
CAMPAIGN_CAP = Decimal("5.00")
STAGE_CAPS = {"baseline": "0.50", "development": "2.00", "sealed": "1.50",
              "performance": "0.50", "reserve": "0.50"}
API = "https://openrouter.ai/api/v1"


class CampaignError(ValueError):
    pass


def money(value):
    if isinstance(value, bool) or value is None:
        raise CampaignError("missing or invalid cost")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CampaignError("invalid cost") from None
    if not result.is_finite() or result < 0:
        raise CampaignError("nonfinite or negative cost")
    return result


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    with temporary.open("wb") as handle:
        handle.write(canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class Ledger:
    """One gateway owns the process lock for the entire paid evaluation run."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.state = json.loads(self.path.read_text(encoding="utf-8"))
        if self.state.get("schema_version") != 1 or money(self.state["cap_usd"]) != CAMPAIGN_CAP:
            raise CampaignError("unexpected ledger contract")
        if self.state.get("stage_caps_usd") != STAGE_CAPS:
            raise CampaignError("stage caps must match the approved campaign")

    @classmethod
    def create(cls, path, campaign_id):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        initial = {"schema_version": 1, "campaign_id": campaign_id,
                   "cap_usd": str(CAMPAIGN_CAP), "stage_caps_usd": STAGE_CAPS,
                   "halt_reason": None, "invocations": {}, "attempts": []}
        try:
            with path.open("xb") as handle:
                handle.write(canonical(initial) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise CampaignError("ledger already exists; refusing to reset spending") from None
        return cls(path)

    def save(self):
        atomic_json(self.path, self.state)

    def register(self, stage, source_sha, input_sha, configuration, case_ids, repetition=1):
        configuration = dict(configuration)
        configuration.setdefault("api_enabled", True)
        if stage not in STAGE_CAPS:
            raise CampaignError("unknown stage")
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha) or not re.fullmatch(r"[0-9a-f]{64}", input_sha):
            raise CampaignError("source/input hashes required")
        if configuration.get("policy") not in {"fallback", "selective"} or configuration.get("output_tokens") not in {600, 1200}:
            raise CampaignError("unknown evaluation configuration")
        if set(configuration) != {"policy", "output_tokens", "api_enabled"} or not isinstance(configuration["api_enabled"], bool):
            raise CampaignError("unexpected evaluation configuration")
        if isinstance(repetition, bool) or repetition not in range(1, 4) or (stage != "performance" and repetition != 1):
            raise CampaignError("undeclared evaluation repetition")
        ids = list(case_ids)
        if not ids or len(set(ids)) != len(ids) or any(not isinstance(q, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", q) for q in ids):
            raise CampaignError("case IDs must be unique opaque identifiers")
        id_hashes = [hashlib.sha256(q.encode()).hexdigest() for q in ids]
        identity = {"source_sha": source_sha, "input_sha256": input_sha,
                    "configuration": configuration, "repetition": repetition,
                    "case_ids_sha256": hashlib.sha256(canonical(id_hashes)).hexdigest()}
        invocation = hashlib.sha256(canonical(identity)).hexdigest()
        with self.lock:
            entry = dict(identity, stage=stage, cases=id_hashes)
            old = self.state["invocations"].get(invocation)
            if old is not None and old != entry:
                raise CampaignError("invocation identity cannot move between stages")
            self.state["invocations"][invocation] = entry
            self.save()
        return invocation

    def question_key(self, run, question):
        invocation = self.state["invocations"].get(run)
        if invocation is None:
            raise CampaignError("unregistered invocation")
        if not isinstance(question, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", question):
            raise CampaignError("invalid question identifier")
        hashed = hashlib.sha256(question.encode()).hexdigest()
        if hashed not in invocation["cases"]:
            raise CampaignError("unknown question identifier")
        return hashlib.sha256((run + ":" + hashed).encode()).hexdigest()

    def totals(self, *, stage=None, run=None, question=None):
        actual = Decimal(0)
        unresolved = Decimal(0)
        count = 0
        for entry in self.state["attempts"]:
            if stage is not None and entry["stage"] != stage:
                continue
            if run is not None and entry["run"] != run:
                continue
            if question is not None and entry["question"] != question:
                continue
            count += 1
            if entry["cost_usd"] is None:
                unresolved += money(entry["reserved_usd"])
            else:
                actual += money(entry["cost_usd"])
        return {"actual_usd": str(actual), "unresolved_usd": str(unresolved),
                "exposure_usd": str(actual + unresolved), "attempts": count}

    def reserve(self, stage, run, question, body_sha256):
        with self.lock:
            if self.state["halt_reason"]:
                raise CampaignError("campaign is halted")
            if stage not in STAGE_CAPS or not run or not question:
                raise CampaignError("missing evaluation identity")
            question_key = self.question_key(run, question)
            if self.state["invocations"][run]["stage"] != stage:
                raise CampaignError("invocation stage mismatch")
            own = self.totals(run=run, question=question_key)
            if own["attempts"] >= 3 or money(own["exposure_usd"]) + RESERVE > QUESTION_CAP:
                raise CampaignError("question attempt or cost limit")
            if money(self.totals(stage=stage)["exposure_usd"]) + RESERVE > money(STAGE_CAPS[stage]):
                raise CampaignError("stage budget exhausted")
            if money(self.totals()["exposure_usd"]) + RESERVE > CAMPAIGN_CAP:
                raise CampaignError("campaign budget exhausted")
            entry = {"number": len(self.state["attempts"]) + 1, "stage": stage,
                     "run": run, "question": question_key, "request_sha256": body_sha256,
                     "reserved_usd": str(RESERVE), "cost_usd": None,
                     "status": "reserved", "created_unix": time.time(),
                     "prompt_tokens": None, "completion_tokens": None,
                     "reasoning_tokens": None, "generation_id": None}
            self.state["attempts"].append(entry)
            self.save()  # fsync BEFORE handing the request to the network
            return entry["number"]

    def dispatched(self, number):
        with self.lock:
            self.state["attempts"][number - 1]["status"] = "dispatched"
            self.save()

    def finish(self, number, response=None, status="response", duration=None):
        with self.lock:
            entry = self.state["attempts"][number - 1]
            if entry["cost_usd"] is not None:
                raise CampaignError("attempt already reconciled")
            usage = response.get("usage") if isinstance(response, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            try:
                billed = money(usage.get("cost"))
            except CampaignError:
                billed = None
            entry["status"] = "reconciled" if billed is not None else "unknown:" + status
            entry["duration_seconds"] = duration
            entry["cost_usd"] = str(billed) if billed is not None else None
            if isinstance(response, dict):
                generation = response.get("id")
                entry["generation_id"] = generation if isinstance(generation, str) else None
            for field in ("prompt_tokens", "completion_tokens"):
                value = usage.get(field)
                entry[field] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
            detail = usage.get("completion_tokens_details") or {}
            reasoning = detail.get("reasoning_tokens") if isinstance(detail, dict) else None
            entry["reasoning_tokens"] = reasoning if isinstance(reasoning, int) and reasoning >= 0 else None
            if billed is not None and billed > money(entry["reserved_usd"]):
                self.state["halt_reason"] = "actual_cost_exceeded_reservation"
            if money(self.totals()["exposure_usd"]) > CAMPAIGN_CAP:
                self.state["halt_reason"] = "campaign_cost_exceeded_cap"
            self.save()

    def halt(self, reason):
        with self.lock:
            self.state["halt_reason"] = reason
            self.save()


@contextmanager
def exclusive_ledger(path):
    """An interrupted gateway leaves its lock, requiring explicit reconciliation."""
    lock_path = Path(str(path) + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise CampaignError("another or interrupted gateway owns the ledger") from None
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def load_key(path=None):
    value = os.environ.pop("EVAL_OPENROUTER_KEY", "")
    if not value and path:
        env = dict(os.environ, QWEN_CREDENTIAL_FILE=str(Path(path).resolve()))
        command = "$c=Import-Clixml -LiteralPath $env:QWEN_CREDENTIAL_FILE;[Console]::Write($c.GetNetworkCredential().Password)"
        process = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                 env=env, capture_output=True, text=True, check=False)
        if process.returncode:
            raise CampaignError("could not open the Windows credential")
        value = process.stdout.strip()
    if not value or not value.startswith("sk-or-"):
        raise CampaignError("a valid campaign credential is required")
    return value


def remote_json(path, key=None, body=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(API + path, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(2_000_000))


def price_signature(document):
    data = document.get("data", {})
    if data.get("id") != MODEL:
        raise CampaignError("unexpected model catalog identity")
    endpoints = []
    for endpoint in data.get("endpoints", []):
        if endpoint.get("tag") not in {"alibaba", "alibaba/us-east-1"}:
            continue
        price = endpoint.get("pricing") or {}
        tiers = [price] + list(price.get("overrides") or [])
        for tier in tiers:
            if money(tier.get("prompt")) > Decimal("0.00000025") or money(tier.get("completion")) > Decimal("0.000001"):
                raise CampaignError("provider rate exceeds campaign ceiling")
            for fee in ("request", "image", "web_search", "internal_reasoning"):
                if fee in tier and money(tier[fee]) != 0:
                    raise CampaignError("unsupported extra provider fee")
        endpoints.append({field: endpoint.get(field) for field in
                          ("tag", "name", "context_length", "max_prompt_tokens", "max_completion_tokens", "pricing", "supported_parameters")})
    if not endpoints:
        raise CampaignError("approved provider unavailable")
    return {"model": MODEL, "endpoints": sorted(endpoints, key=lambda value: value["tag"])}


def preflight(key, snapshot_path, *, initialize=False):
    account = remote_json("/key", key)["data"]
    if money(account.get("limit")) != CAMPAIGN_CAP or account.get("limit_reset") is not None:
        raise CampaignError("campaign key needs a non-resetting USD 5 limit")
    remaining = money(account.get("limit_remaining"))
    if remaining < RESERVE:
        raise CampaignError("provider key balance below the next reservation")
    current = price_signature(remote_json("/models/" + MODEL + "/endpoints"))
    snapshot_path = Path(snapshot_path)
    if initialize:
        if snapshot_path.exists():
            raise CampaignError("refusing to overwrite the approved pricing snapshot")
        atomic_json(snapshot_path, current)
    elif not snapshot_path.exists() or canonical(json.loads(snapshot_path.read_text(encoding="utf-8"))) != canonical(current):
        raise CampaignError("provider metadata changed; paid execution is blocked")
    return {"model": MODEL, "key_limit_usd": "5.00", "key_remaining_usd": str(remaining),
            "key_usage_usd": str(money(account.get("usage"))),
            "pricing_sha256": hashlib.sha256(canonical(current)).hexdigest()}


def verify_prices(snapshot_path):
    current = price_signature(remote_json("/models/" + MODEL + "/endpoints", timeout=10))
    approved = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    if canonical(current) != canonical(approved):
        raise CampaignError("provider metadata changed")


def guarded_body(raw):
    if len(raw) > MAX_BODY:
        raise CampaignError("request body exceeds 24 KiB")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CampaignError("invalid request JSON") from None
    if not isinstance(body, dict) or body.get("model") != MODEL:
        raise CampaignError("only the approved Qwen model is allowed")
    if body.get("max_tokens") not in (600, 1200):
        raise CampaignError("unapproved output token cap")
    if body.get("temperature") != 0 or body.get("response_format") != {"type": "json_object"}:
        raise CampaignError("unexpected planner generation configuration")
    if body.get("reasoning") != {"enabled": False}:
        raise CampaignError("reasoning must be disabled")
    allowed = {"model", "messages", "max_tokens", "temperature", "response_format", "reasoning", "provider"}
    if set(body) - allowed:
        raise CampaignError("unsupported request extension")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 4:
        raise CampaignError("invalid message list")
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise CampaignError("only plain text messages are allowed")
        if message["role"] not in {"system", "user", "assistant"} or not isinstance(message["content"], str):
            raise CampaignError("only plain text messages are allowed")
    body["provider"] = {"only": ["alibaba"], "allow_fallbacks": False, "require_parameters": True,
                        "max_price": {"prompt": 0.25, "completion": 1, "request": 0}}
    result = canonical(body)
    if len(result) > MAX_BODY:
        raise CampaignError("guarded request body exceeds 24 KiB")
    return result


class Gateway:
    def __init__(self, ledger, key, token, stage, run, pricing):
        self.ledger, self.key, self.token = ledger, key, token
        self.stage, self.run, self.pricing = stage, run, Path(pricing)
        self.serial = threading.Lock()

    def call(self, authorization, question, raw, deadline=None):
        if not hmac.compare_digest((authorization or "").encode(), ("Bearer " + self.token).encode()):
            raise CampaignError("unauthorized gateway call")
        if not question or len(question) > 200 or any(ord(c) < 32 for c in question):
            raise CampaignError("missing or invalid question identity")
        guarded = guarded_body(raw)
        if deadline is not None and (not isinstance(deadline, (int,float)) or not math.isfinite(deadline)):
            raise CampaignError("invalid caller deadline")
        with self.serial:
            # Authenticate the logical question before any outbound request.
            self.ledger.question_key(self.run, question)
            if not self.ledger.state["invocations"][self.run]["configuration"]["api_enabled"]:
                raise CampaignError("this invocation has network disabled")
            if deadline is not None and not 0 < deadline - time.time() <= 91:
                raise CampaignError("caller deadline expired or is outside the approved window")
            try:
                verify_prices(self.pricing)
            except Exception:
                self.ledger.halt("provider_metadata_not_verified")
                raise CampaignError("provider prices could not be verified; no paid request sent") from None
            remaining = 85 if deadline is None else min(85, deadline - time.time())
            if remaining <= 0:
                raise CampaignError("caller deadline expired before reservation")
            number = self.ledger.reserve(self.stage, self.run, question, hashlib.sha256(guarded).hexdigest())
            self.ledger.dispatched(number)
            started = time.monotonic()
            try:
                remaining = 85 if deadline is None else min(85, deadline - time.time())
                if remaining <= 0:
                    raise TimeoutError("deadline expired before dispatch")
                response = remote_json("/chat/completions", self.key, guarded, timeout=remaining)
            except Exception as error:
                self.ledger.finish(number, status=type(error).__name__, duration=time.monotonic() - started)
                raise CampaignError("upstream request failed; reservation retained") from None
            self.ledger.finish(number, response, duration=time.monotonic() - started)
            if self.ledger.state["halt_reason"]:
                raise CampaignError("campaign stopped after a billing anomaly")
            return response


def make_server(gateway, host="127.0.0.1", port=0):
    if host != "127.0.0.1":
        raise CampaignError("gateway must bind to IPv4 loopback")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                if self.path != "/api/v1/chat/completions":
                    raise CampaignError("unsupported gateway path")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    raise CampaignError("invalid request size")
                self.connection.settimeout(10)
                payload = self.rfile.read(size)
                if len(payload) != size:
                    raise CampaignError("incomplete request")
                response = gateway.call(self.headers.get("Authorization"), self.headers.get("X-Eval-Question"), payload)
                status = 200
            except (CampaignError, ValueError, OSError) as error:
                response = {"error": {"message": str(error) if isinstance(error, CampaignError) else "invalid gateway request"}}
                status = 429
            raw = canonical(response)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the durable ledger still knows whether this call was billed

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = False
    return server


def serve(gateway, host, port, ready_path):
    server = make_server(gateway, host, port)
    atomic_json(ready_path, {"port": server.server_address[1], "pid": os.getpid(),
                            "stage": gateway.stage, "run": gateway.run})
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "preflight", "serve", "summary", "register"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--credential")
    parser.add_argument("--stage", choices=STAGE_CAPS)
    parser.add_argument("--run")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--source-sha")
    parser.add_argument("--input")
    parser.add_argument("--policy", choices=("fallback", "selective"), default="fallback")
    parser.add_argument("--output-tokens", type=int, choices=(600, 1200), default=600)
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ledger_path, pricing_path = root / "ledger.json", root / "pricing.json"
    if args.action == "init":
        with exclusive_ledger(ledger_path):
            ledger = Ledger(ledger_path) if ledger_path.exists() else Ledger.create(ledger_path, "qwen-evaluation-20261016")
            if ledger.state["attempts"] or pricing_path.exists():
                raise CampaignError("campaign already initialized; use preflight instead")
            result = preflight(load_key(args.credential), pricing_path, initialize=True)
        atomic_json(root / "preflight.json", result)
        print(json.dumps(result))
    elif args.action == "summary":
        ledger = Ledger(ledger_path)
        print(json.dumps({"totals": ledger.totals(), "stages": {s: ledger.totals(stage=s) for s in STAGE_CAPS},
                          "halt_reason": ledger.state["halt_reason"]}))
    elif args.action == "preflight":
        print(json.dumps(preflight(load_key(args.credential), pricing_path)))
    elif args.action == "register":
        if not args.input or not args.source_sha or not args.stage:
            raise CampaignError("input, source SHA and stage are required")
        source = Path(args.input)
        queries = json.loads((source / "queries.json").read_text(encoding="utf-8-sig"))
        files = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in source.rglob("*") if p.is_file()}
        if set(files) - {"queries.json"} - {"xlsx_files/" + str(q["file_name"]) for q in queries}:
            raise CampaignError("evaluation input must contain only queries and referenced workbooks")
        with exclusive_ledger(ledger_path):
            invocation = Ledger(ledger_path).register(
                args.stage, args.source_sha, hashlib.sha256(canonical(files)).hexdigest(),
                {"policy": args.policy, "output_tokens": args.output_tokens},
                [str(q["id"]) for q in queries], args.repetition)
        print(json.dumps({"invocation": invocation, "questions": len(queries)}))
    else:
        if not args.stage or not args.run:
            raise CampaignError("stage and run are required")
        key = load_key(args.credential)
        preflight(key, pricing_path)
        token = os.environ.pop("EVAL_GATEWAY_TOKEN", "")
        if len(token) < 32:
            raise CampaignError("a random gateway token is required")
        with exclusive_ledger(ledger_path):
            gateway = Gateway(Ledger(ledger_path), key, token, args.stage, args.run, pricing_path)
            if args.run not in gateway.ledger.state["invocations"]:
                raise CampaignError("invocation must be registered before serving")
            serve(gateway, "127.0.0.1", args.port, root / "gateway-ready.json")


if __name__ == "__main__":
    try:
        main()
    except CampaignError as error:
        raise SystemExit("CAMPAIGN_BLOCKED: " + str(error)) from None
