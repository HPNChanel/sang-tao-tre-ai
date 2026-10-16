"""Temporary authenticated transfer/paid gateway. Real provider key stays local."""
import argparse
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

import campaign_wire as W
import qwen_campaign as C


def load_private(path):
    """Read a CurrentUser DPAPI envelope without exposing plaintext to logs."""
    command = ("$b=[IO.File]::ReadAllBytes($env:STAI_PRIVATE_FILE);"
               "$p=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,"
               "[Security.Cryptography.DataProtectionScope]::CurrentUser);"
               "[Console]::OpenStandardOutput().Write($p,0,$p.Length)")
    result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
                            env=dict(os.environ, STAI_PRIVATE_FILE=str(Path(path).resolve())),
                            capture_output=True, check=False)
    if result.returncode:
        raise ValueError("could not open the transport credential")
    return result.stdout


def save_private(path, raw):
    command = ("$s=[Console]::OpenStandardInput();$m=[IO.MemoryStream]::new();$s.CopyTo($m);"
               "$p=[Security.Cryptography.ProtectedData]::Protect($m.ToArray(),$null,"
               "[Security.Cryptography.DataProtectionScope]::CurrentUser);"
               "[IO.File]::WriteAllBytes($env:STAI_PRIVATE_FILE,$p)")
    result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
                            env=dict(os.environ, STAI_PRIVATE_FILE=str(Path(path).resolve())),
                            input=raw, capture_output=True, check=False)
    if result.returncode:
        raise ValueError("could not protect the transport credential")


class Session:
    def __init__(self, root, control, credential):
        self.root, self.control = Path(root), Path(control)
        self.raw = (self.root / "manifest.json").read_bytes()
        self.manifest = json.loads(self.raw)
        W.validate_manifest(self.manifest)
        self.secret = json.loads(load_private(self.root / "transport-secret.dpapi"))
        self.ledger = C.Ledger(self.control / "ledger.json")
        paid = any(x["configuration"]["api_enabled"] for x in self.manifest["runs"])
        key = C.load_key(credential) if paid else None
        if paid:
            C.preflight(key, self.control / "pricing.json")
        self.gateways = {}
        self.lock = threading.RLock()
        self.active, self.next_run, self.freeze = None, 0, None
        for run in self.manifest["runs"]:
            batch = self.manifest["batches"][run["batch_index"]]
            invocation = self.ledger.register(self.manifest["stage"], self.manifest["sources"][run["label"]]["sha"],
                batch["input_sha256"], run["configuration"], batch["ids"], run.get("repetition", 1))
            if invocation != run["invocation"]:
                raise ValueError("invocation identity differs from preregistration")
            self.gateways[run["number"]] = C.Gateway(self.ledger, key, self.secret["token"],
                self.manifest["stage"], invocation, self.control / "pricing.json")
        self.files = {"manifest.json":W.digest(self.raw)}
        self.files.update({s["archive"]:s["archive_sha256"] for s in self.manifest["sources"].values()})
        self.files.update({b["cipher_file"]:b["cipher_sha256"] for b in self.manifest["batches"]})
        for name, expected in self.files.items():
            if not re.fullmatch(r"[A-Za-z0-9_./-]+", name) or ".." in Path(name).parts:
                raise ValueError("unsafe transfer path")
            if W.digest((self.root / name).read_bytes()) != expected:
                raise ValueError("frozen transfer file changed")
        (self.root / "received").mkdir(exist_ok=True)
        self.recover()

    def recover(self):
        freeze_path = self.root / "environment-freeze.json"
        if freeze_path.exists():
            self.freeze = json.loads(freeze_path.read_text())
            if self.freeze.get("manifest_sha256") != W.digest(self.raw):
                raise ValueError("stored environment belongs to a different manifest")
        paths = sorted((self.root / "received").glob("run-*.aesgcm"))
        for number, path in enumerate(paths):
            if self.freeze is None or path.name != f"run-{number:03}.aesgcm" or number >= len(self.manifest["runs"]):
                raise ValueError("noncontiguous completed-run receipts")
            run = self.manifest["runs"][number]
            meta = W.result_metadata(W.digest(self.raw), run,
                self.manifest["batches"][run["batch_index"]], self.manifest["sources"][run["label"]],
                self.freeze["images"][run["label"]]["image_id"])
            W.decrypt(path.read_bytes(), base64.b64decode(self.secret["output_key"]), meta, "RESULT")
        self.next_run = len(paths)

    def authenticate(self, authorization):
        return (time.time() < self.manifest["expires_unix"] and
                hmac.compare_digest((authorization or "").encode(), ("Bearer " + self.secret["token"]).encode()))

    def accept_freeze(self, value):
        # Check GitHub's own record, not just a runner's self-reported SHA.
        run_id = str(value.get("github_run_id", ""))
        if not re.fullmatch(r"\d+", run_id):
            raise ValueError("missing CI identity")
        result = subprocess.run(["gh", "api", "repos/HPNChanel/sang-tao-tre-ai/actions/runs/" + run_id],
                                capture_output=True, check=False, timeout=30)
        record = json.loads(result.stdout) if result.returncode == 0 else {}
        if (record.get("head_sha") != self.manifest["harness_sha"]
                or record.get("event") != "workflow_dispatch"
                or record.get("actor", {}).get("login") != "HPNChanel"
                or record.get("repository", {}).get("private") is not False
                or value.get("harness_sha") != self.manifest["harness_sha"]
                or value.get("manifest_sha256") != W.digest(self.raw)):
            raise ValueError("unapproved workflow execution")
        if set(value.get("images", {})) != set(self.manifest["sources"]):
            raise ValueError("missing image freeze")
        for label, image in value["images"].items():
            if image.get("source_sha") != self.manifest["sources"][label]["sha"] or not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("image_id", "")):
                raise ValueError("invalid frozen image")
            if image.get("os") != "linux" or image.get("architecture") != "amd64":
                raise ValueError("unexpected container platform")
        if self.freeze is not None:
            if self.freeze["images"] != value["images"]:
                raise ValueError("cannot resume under different frozen images or dependencies")
            C.atomic_json(self.root / "last-reconnection.json", value)
        else:
            self.freeze = value
            C.atomic_json(self.root / "environment-freeze.json", value)
        return {"accepted":True, "next_run":self.next_run, "gateway_monotonic":time.monotonic()}

    def post(self, path, raw, authorization, question, deadline=None):
        if path == "/freeze":
            return self.accept_freeze(json.loads(raw))
        match = re.fullmatch(r"/(start|chat|cost|result)/(\d+)", path)
        if not match or self.freeze is None:
            raise ValueError("unsupported or unfrozen operation")
        action, number = match[1], int(match[2])
        if number not in self.gateways:
            raise ValueError("unknown run")
        with self.lock:
            if action == "start":
                if self.active is not None or number != self.next_run:
                    raise ValueError("run order or replay violation")
                if (self.root / "received" / f"run-{number:03}.aesgcm").exists():
                    raise ValueError("completed run cannot be replayed")
                self.active = number
                return {"accepted":True, "gateway_monotonic":time.monotonic()}
            if self.active != number:
                raise ValueError("run is not active")
            gateway = self.gateways[number]
            if action == "chat":
                if json.loads(raw).get("max_tokens") != self.manifest["runs"][number]["configuration"]["output_tokens"]:
                    raise ValueError("output cap differs from frozen configuration")
                if deadline is None:
                    raise ValueError("paid relay requires a case deadline")
                return gateway.call(authorization, question, raw, deadline=deadline)
            if action == "cost":
                return self.ledger.totals(run=gateway.run)
            if len(raw) > W.MAX_ARCHIVE:
                raise ValueError("result too large")
            # Authenticate metadata/ciphertext before receipt, but do not open
            # the contained CSV/log ZIP or score it. Both versions stay blind.
            run = self.manifest["runs"][number]
            batch = self.manifest["batches"][run["batch_index"]]
            source = self.manifest["sources"][run["label"]]
            meta = W.result_metadata(W.digest(self.raw), run, batch, source,
                                    self.freeze["images"][run["label"]]["image_id"])
            W.decrypt(raw, base64.b64decode(self.secret["output_key"]), meta, "RESULT")
            with (self.root / "received" / f"run-{number:03}.aesgcm").open("xb") as handle:
                handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            self.active, self.next_run = None, number + 1
            return {"sha256":W.digest(raw), "cost":self.ledger.totals(run=gateway.run)}


def make_server(session, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def answer(self, status, data):
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            name = self.path.removeprefix("/files/")
            if not session.authenticate(self.headers.get("Authorization")) or not self.path.startswith("/files/") or name not in session.files:
                return self.answer(403, b"{}")
            data = (session.root / name).read_bytes()
            if W.digest(data) != session.files[name]:
                return self.answer(409, b"{}")
            self.answer(200, data)
        def do_POST(self):
            try:
                if not session.authenticate(self.headers.get("Authorization")):
                    raise ValueError("unauthorized")
                size = int(self.headers.get("Content-Length", "0"))
                limit = W.MAX_ARCHIVE if self.path.startswith("/result/") else 24 * 1024 if self.path.startswith("/chat/") else 500_000
                if not 0 < size <= limit or self.headers.get("Transfer-Encoding"):
                    raise ValueError("invalid request length")
                self.connection.settimeout(100)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError("incomplete request")
                deadline = self.headers.get("X-Eval-Deadline")
                result = session.post(self.path, raw, self.headers.get("Authorization"),
                                      self.headers.get("X-Eval-Question"), float(deadline) if deadline else None)
                self.answer(200, C.canonical(result))
            except Exception:
                # Never reflect a provider response, prompt, key or traceback.
                self.answer(403, b'{"error":"request rejected; inspect private receipts"}')
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--control", required=True)
    p.add_argument("--credential")
    p.add_argument("--port", type=int, required=True)
    args = p.parse_args()
    with C.exclusive_ledger(Path(args.control) / "ledger.json"):
        session = Session(args.root, args.control, args.credential)
        print(json.dumps({"ready":True, "manifest_sha256":W.digest(session.raw), "port":args.port}), flush=True)
        make_server(session, args.port).serve_forever(poll_interval=.2)


if __name__ == "__main__":
    main()
