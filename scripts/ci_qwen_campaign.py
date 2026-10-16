"""Manual, pinned Linux evaluation. No plaintext inputs or results in CI logs."""
import base64
from contextlib import contextmanager
import csv
from http.server import BaseHTTPRequestHandler
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

import campaign_wire as W
import qwen_campaign as C

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("transport redirects are forbidden")


class Remote:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", parsed.netloc)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("approved TLS transfer endpoint required")
        self.url, self.token = url.rstrip("/"), token
        self.opener = urllib.request.build_opener(NoRedirect())
        self.clock_reference = None

    def set_clock_reference(self, acknowledgment):
        host = acknowledgment.get("gateway_monotonic")
        if (acknowledgment.get("accepted") is not True or isinstance(host, bool)
                or not isinstance(host, (int, float)) or not math.isfinite(host)):
            raise ValueError("missing or invalid gateway clock reference")
        # Sample only after the reply is received. Reply transit therefore
        # shortens the mapped window; wall-clock skew cannot extend it.
        self.clock_reference = (host, time.monotonic())

    def mapped_deadline(self, deadline):
        if (self.clock_reference is None or isinstance(deadline, bool)
                or not isinstance(deadline, (int, float)) or not math.isfinite(deadline)):
            raise ValueError("missing clock reference or invalid case deadline")
        host, client = self.clock_reference
        return host + (deadline - client)

    def request(self, route, data=None, question=None, timeout=100, deadline=None):
        headers = {"Authorization":"Bearer " + self.token, "Content-Type":"application/json"}
        if question is not None:
            headers["X-Eval-Question"] = question
        if deadline is not None:
            headers["X-Eval-Deadline"] = str(self.mapped_deadline(deadline))
        request = urllib.request.Request(self.url + route, data=data, headers=headers)
        with self.opener.open(request, timeout=timeout) as response:
            raw = response.read(W.MAX_ARCHIVE + 1)
        if len(raw) > W.MAX_ARCHIVE:
            raise ValueError("transport object too large")
        return raw

    def post(self, route, data=b"{}", question=None, deadline=None):
        response = json.loads(self.request(route, data, question, deadline=deadline))
        if route == "/freeze" or re.fullmatch(r"/start/\d+", route):
            # Refresh each batch, including after reconnects, so independent
            # monotonic clock rates do not accumulate over a long campaign.
            self.set_clock_reference(response)
        return response


class ScopedProxy(socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    daemon_threads = False
    def __init__(self, path, remote, run, queries):
        if not hasattr(socketserver, "UnixStreamServer"):
            raise RuntimeError("the container proxy requires Linux Unix sockets")
        # Read the hash-verified input on the trusted host. Match the original
        # runner's stable workbook grouping; the container cannot skip cases.
        self.remote, self.run = remote, run
        self.queries = {str(q["id"]):q["query"] for q in queries}
        self.ids = [str(q["id"]) for q in sorted(queries, key=lambda q:q["file_name"])]
        self.current, self.position, self.attempts = None, -1, 0
        self.serial = threading.RLock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < size <= C.MAX_BODY:
                        raise ValueError("invalid size")
                    self.connection.settimeout(100)
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        raise ValueError("incomplete request")
                    response = self.server.handle(self.path, raw)
                    data, status = C.canonical(response), 200
                except Exception:
                    data, status = b'{"error":"scoped gateway rejected request"}', 403
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        super().__init__(str(path), Handler)
        os.chmod(path, 0o666)  # isolated mounted directory, accessible to UID 65534

    def handle(self, route, raw):
        with self.serial:
            if route == "/probe" and raw == b"{}":
                return {"scoped_proxy":True}
            if route == "/begin":
                qid = json.loads(raw).get("id")
                if self.current is not None or self.position + 1 >= len(self.ids) or qid != self.ids[self.position + 1]:
                    raise ValueError("invalid case transition")
                self.position, self.current, self.attempts = self.ids.index(qid), qid, 0
                self.deadline = time.monotonic() + 90
                return {"accepted":True}
            if route == "/end" and self.current is not None:
                self.current = None
                return {"accepted":True}
            if route != "/chat" or self.current is None or self.attempts >= 3 or not self.run["configuration"]["api_enabled"]:
                raise ValueError("request outside active paid case")
            guarded = C.guarded_body(raw)
            body = json.loads(guarded)
            if body["max_tokens"] != self.run["configuration"]["output_tokens"]:
                raise ValueError("request config differs")
            question = body["messages"][1]["content"].partition("\n\nQUESTION:\n")[2]
            expected = self.queries[self.current]
            if not question.startswith(expected):
                raise ValueError("request question differs from the host-selected case")
            suffix = question[len(expected):]
            if suffix.strip() and not suffix.startswith("\n\nReview the two proposed JSON plans below"):
                raise ValueError("unapproved question extension")
            self.attempts += 1
            current, deadline = self.current, self.deadline
        # Network settlement must not hold the case-transition lock. A timed
        # out solver may finish its case while the durable gateway reconciles.
        return self.remote.post(f"/chat/{self.run['number']}", guarded, current, deadline=deadline)


def checked(command, log, timeout=600):
    with Path(log).open("wb") as handle:
        process = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout)
    if process.returncode:
        raise RuntimeError("private subprocess failed; encrypted diagnostics retained")


def build_images(manifest, remote, work):
    sources, images = {}, {}
    for label, item in manifest["sources"].items():
        raw = remote.request("/files/" + item["archive"])
        if W.digest(raw) != item["archive_sha256"]:
            raise ValueError("source archive hash changed")
        source = work / ("source-" + label)
        W.unpack(raw, source)
        inventory = json.loads((source / "SOURCE_MANIFEST.json").read_text())
        if inventory["source_commit"] != item["sha"]:
            raise ValueError("source revision changed")
        for name, value in inventory["files"].items():
            if W.digest((source / name).read_bytes()) != value:
                raise ValueError("runtime file changed")
        if set(W.snapshot(source)) != set(inventory["files"]) | {"SOURCE_MANIFEST.json", "PACKAGE_INFO.json"}:
            # Current source package has one README, not an executable wildcard.
            extra = set(W.snapshot(source)) - set(inventory["files"]) - {"SOURCE_MANIFEST.json"}
            if extra - {"README.md", "README_SUBMISSION.md", "README_SUBMISSION.txt", "README.txt", "PACKAGE_INFO.json"}:
                raise ValueError("unmanifested runtime file")
        sources[label] = source
    # Equalize only the build environment, while preserving every run.py/src
    # byte. Record this normalization separately from each source ZIP hash.
    recipe_source = sources.get("candidate", next(iter(sources.values())))
    recipe = (recipe_source / "Dockerfile").read_bytes()
    requirements = (recipe_source / "requirements.txt").read_bytes()
    if not re.search(rb"FROM python:3\.11-slim@sha256:[0-9a-f]{64}", recipe):
        raise ValueError("base image must be pinned by digest")
    for label, source in sources.items():
        (source / "Dockerfile").write_bytes(recipe)
        (source / "requirements.txt").write_bytes(requirements)
        image = "stai-eval:" + label + "-" + manifest["sources"][label]["sha"][:12]
        checked(["docker", "build", "--platform=linux/amd64", "-t", image, str(source)], work / ("build-" + label + ".log"))
        inspected = json.loads(subprocess.check_output(["docker","image","inspect",image]))[0]
        image_id = inspected["Id"]
        frozen = subprocess.check_output(["docker","run","--rm","--network=none","--entrypoint=python",image_id,"-m","pip","freeze"], text=True)
        images[label] = {"source_sha":manifest["sources"][label]["sha"], "image_id":image_id,
            "os":inspected["Os"], "architecture":inspected["Architecture"], "pip_freeze":frozen.splitlines(),
            "build_recipe_sha256":W.digest(recipe), "requirements_sha256":W.digest(requirements)}
    if len({tuple(x["pip_freeze"]) for x in images.values()}) != 1:
        raise ValueError("baseline and candidate dependency environments differ")
    return images


def docker_base(name, image, inputs, outputs, socket_dir):
    return ["docker","run","--name",name,"--platform=linux/amd64","--network=none",
            "--memory=1000000000","--memory-swap=1000000000","--cpus=2","--pids-limit=128",
            "--user=65534:65534","--cap-drop=ALL","--security-opt=no-new-privileges","--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=67108864", "--env=PYTHONDONTWRITEBYTECODE=1",
            "--mount",f"type=bind,src={inputs},dst=/input,readonly",
            "--mount",f"type=bind,src={outputs},dst=/output",
            "--mount",f"type=bind,src={socket_dir},dst=/gateway,readonly",
            "--mount",f"type=bind,src={ROOT / 'scripts'},dst=/harness,readonly",
            "--entrypoint=python",image]


@contextmanager
def bounded_output(path):
    path.mkdir()
    subprocess.run(["sudo","mount","-t","tmpfs","-o","size=67108864,mode=0777","tmpfs",str(path)],
                   capture_output=True, check=True, timeout=20)
    try:
        yield
    finally:
        subprocess.run(["sudo","umount",str(path)],capture_output=True,check=True,timeout=20)


def run_batch(manifest, run, batch, inputs, image, remote, work, manifest_sha, output_key):
    with bounded_output(work / f"result-{run['number']:03}"):
        _run_batch(manifest, run, batch, inputs, image, remote, work, manifest_sha, output_key)


def _run_batch(manifest, run, batch, inputs, image, remote, work, manifest_sha, output_key):
    number = run["number"]
    output = work / f"result-{number:03}"
    socket_dir = work / f"socket-{number:03}"
    socket_dir.mkdir(); socket_dir.chmod(0o755)
    queries = json.loads((inputs / "queries.json").read_text(encoding="utf-8-sig"))
    if [str(q["id"]) for q in queries] != batch["ids"]:
        raise ValueError("decrypted IDs differ from the preregistered batch")
    proxy = ScopedProxy(socket_dir / "request.sock", remote, run, queries)
    worker = threading.Thread(target=proxy.serve_forever, kwargs={"poll_interval":.1}, daemon=True)
    remote.post(f"/start/{number}")
    worker.start()
    name = "stai-" + uuid.uuid4().hex
    args = ["/harness/ci_memory_probe.py", "python", "/harness/eval_unix_solver.py",
            "--socket","/gateway/request.sock", "--policy",run["configuration"]["policy"],
            "--output-tokens",str(run["configuration"]["output_tokens"]),
            '--solver-profile',run['configuration'].get('solver_profile','legacy'),
            '--model',run['configuration'].get('model',C.MODEL)]
    if not run["configuration"]["api_enabled"]:
        args.append("--no-key")
    started, timed_out, code = time.monotonic(), False, -1
    try:
        with (output / "stdout.txt").open("wb") as stdout, (output / "stderr.txt").open("wb") as stderr:
            try:
                process = subprocess.run(docker_base(name, image, inputs, output, socket_dir) + args,
                                         stdout=stdout, stderr=stderr, timeout=2400)
                code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                subprocess.run(["docker","kill","--signal=TERM",name], capture_output=True, timeout=10)
                try:
                    subprocess.run(["docker","wait",name], capture_output=True, timeout=8)
                except subprocess.TimeoutExpired:
                    subprocess.run(["docker","kill",name], capture_output=True, timeout=10)
    finally:
        proxy.shutdown(); proxy.server_close(); worker.join()
    elapsed = time.monotonic() - started
    state = json.loads(subprocess.check_output(["docker","inspect",name]))[0]["State"]
    subprocess.run(["docker","rm",name], capture_output=True, check=True)
    stderr = (output / "stderr.txt").read_text(encoding="utf-8", errors="replace")
    memory = [json.loads(line.split("=",1)[1]) for line in stderr.splitlines() if line.startswith("PUBLIC1_MEMORY_JSON=")]
    boundary = [json.loads(line.split("=",1)[1]) for line in (output / "stdout.txt").read_text().splitlines()
                if line.startswith("BOUNDARY_JSON=")]
    csv_path = output / "submission.csv"
    rows = list(csv.reader(csv_path.open(encoding="utf-8-sig", newline=""))) if csv_path.exists() else []
    csv_ok = bool(rows and rows[0] == ["id","answer"] and all(len(x)==2 for x in rows)
                  and [x[0] for x in rows[1:]] == batch["ids"])
    valid_memory = bool(memory and memory[-1]["peak_bytes"] <= 1_000_000_000
        and memory[-1]["limit_bytes"] <= 1_000_000_000 and memory[-1]["swap_limit_bytes"] == 0
        and memory[-1]["swap_current_bytes"] == 0 and memory[-1]["events_delta"].get("oom_kill",0) == 0)
    receipt = {"index":run["batch_index"], "number":number, "invocation":run["invocation"],
        "input_sha256":batch["input_sha256"], "source_sha":manifest["sources"][run["label"]]["sha"],
        "configuration":run["configuration"], "questions":len(batch["ids"]), "elapsed_seconds":elapsed,
        "exit_code":code,"timed_out":timed_out,"csv_contract_pass":csv_ok,"memory_pass":valid_memory,
        "memory":memory[-1] if memory else None,"boundary":boundary[-1] if boundary else None,
        "oom_killed":state.get("OOMKilled"),
        "cost":remote.post(f"/cost/{number}"), "csv_sha256":W.digest(csv_path.read_bytes()) if csv_path.exists() else None}
    (output / "receipt.json").write_bytes(W.canonical(receipt))
    meta = W.result_metadata(manifest_sha, run, batch, manifest["sources"][run["label"]], image)
    encrypted = W.encrypt(W.pack(output), output_key, meta)
    response = remote.post(f"/result/{number}", encrypted)
    if response["sha256"] != W.digest(encrypted):
        raise ValueError("result acknowledgment hash differs")
    print(json.dumps({"run":number,"batch":run["batch_index"],"label":run["label"],
                      "seconds":round(elapsed,3),"receipt_received":True}), flush=True)
    if (code or timed_out or elapsed > 2400 or not csv_ok or not valid_memory or not boundary or not boundary[-1].get("pass")
            or state.get("OOMKilled") or W.snapshot(inputs) != batch["files"]):
        raise RuntimeError("batch acceptance failed; encrypted result preserved")


def main():
    secret = json.loads(os.environ.pop("STAI_EVAL_SECRET"))
    manifest_sha = os.environ.pop("STAI_MANIFEST_SHA256")
    expected_sha = os.environ.pop("STAI_HARNESS_SHA")
    actual_sha = subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip()
    if actual_sha != expected_sha or os.environ.get("GITHUB_SHA") != expected_sha:
        raise ValueError("the reviewed harness SHA is required")
    remote = Remote(os.environ.pop("STAI_TRANSFER_URL"), secret["token"])
    raw = remote.request("/files/manifest.json")
    if W.digest(raw) != manifest_sha:
        raise ValueError("manifest changed")
    manifest = json.loads(raw)
    W.validate_manifest(manifest)
    if manifest["harness_sha"] != expected_sha or time.time() >= manifest["expires_unix"]:
        raise ValueError("expired or different harness")
    with tempfile.TemporaryDirectory(prefix="stai-private-") as folder:
        work = Path(folder)
        images = build_images(manifest, remote, work)
        freeze = {"status":"FROZEN","harness_sha":actual_sha,"manifest_sha256":manifest_sha,
            "github_run_id":os.environ["GITHUB_RUN_ID"],"images":images,
            "platform":platform.platform(),"python":platform.python_version(),
            "cpu":subprocess.check_output(["lscpu"],text=True),
            "memory":Path("/proc/meminfo").read_text(),"created_unix":time.time()}
        acknowledgment = remote.post("/freeze", W.canonical(freeze))
        # Freeze acknowledgment precedes the first input decryption.
        for run in manifest["runs"]:
            if run["number"] < acknowledgment["next_run"]:
                continue
            batch = manifest["batches"][run["batch_index"]]
            inputs = work / ("input-" + str(run["batch_index"]))
            if not inputs.exists():
                encrypted = remote.request("/files/" + batch["cipher_file"])
                if W.digest(encrypted) != batch["cipher_sha256"]:
                    raise ValueError("input ciphertext changed")
                clear = W.decrypt(encrypted, base64.b64decode(secret["input_key"]), batch["aad"])
                W.unpack(clear, inputs, batch["files"])
                del clear
                inputs.chmod(0o755)
                for path in inputs.rglob("*"):
                    path.chmod(0o755 if path.is_dir() else 0o644)
            if 'stable_case_keys' in batch:
                queries=json.loads((inputs/'queries.json').read_text(encoding='utf-8-sig'))
                if C.stable_case_keys(queries,batch['files'])!=batch['stable_case_keys']:
                    raise ValueError('stable case identities differ from authenticated inputs')
            run_batch(manifest, run, batch, inputs, images[run["label"]]["image_id"], remote,
                      work, manifest_sha, base64.b64decode(secret["output_key"]))
    print("Encrypted evaluation completed; no plaintext results published.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Stack traces may carry a question or private response; keep CI opaque.
        raise SystemExit("Evaluation failed; inspect local encrypted receipts.") from None
