"""Trusted identity/transport adapter for the offline-network container.

The host owns the current-case binding. Planner bodies cannot choose another
question. This adapter does not load answers, change plans or intercept maths.
"""
import argparse
import contextvars
import http.client
import json
import os
import socket
import sys


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--socket", required=True)
    p.add_argument("--policy", choices=("fallback","selective"), required=True)
    p.add_argument("--output-tokens", type=int, choices=(600,1200), required=True)
    p.add_argument("--no-key", action="store_true")
    p.add_argument('--solver-profile',choices=('legacy','direct','raw','dual','hybrid'),default='legacy')
    p.add_argument('--model',default='qwen/qwen3.7-flash')
    args = p.parse_args()
    from ci_container_boundary_probe import verify
    print("BOUNDARY_JSON=" + json.dumps(verify(args.socket)), flush=True)
    def audit(event, values):
        if event == "socket.connect" and values[1] != args.socket:
            raise PermissionError("only the scoped Unix gateway is allowed")
        if event == "socket.getaddrinfo":
            raise PermissionError("name resolution disabled")
    sys.addaudithook(audit)
    os.environ["OPENROUTER_API_KEY"] = "" if args.no_key else "scoped-evaluation-marker"
    os.environ["OPENROUTER_MODEL"] = args.model
    os.environ['DS2_SOLVER_PROFILE'] = args.solver_profile
    os.environ["QWEN_REVIEW_POLICY"] = args.policy
    os.environ["QWEN_MAX_OUTPUT_TOKENS"] = str(args.output_tokens)
    sys.path.insert(0, "/app")
    import run
    from src import llm_client
    llm_client.MAX_TOKENS = args.output_tokens
    current = contextvars.ContextVar("case", default=None)
    def request(route, body, timeout=10):
        connection = UnixHTTP(args.socket, timeout)
        try:
            connection.request("POST", route, body=body, headers={"Content-Type":"application/json"})
            response = connection.getresponse()
            data = response.read(2_000_001)
            if response.status != 200 or len(data) > 2_000_000:
                raise RuntimeError("scoped gateway rejected the request")
            return json.loads(data)
        finally:
            connection.close()
    original_answer, original_plan = run.answer_one, llm_client.llm_plan
    def answer(question, *a, **kw):
        qid = str(question["id"])
        request("/begin", json.dumps({"id":qid}).encode())
        token = current.set(qid)
        try:
            return original_answer(question, *a, **kw)
        finally:
            current.reset(token)
            request("/end", b"{}")
    def plan(*a, **kw):
        if current.get() is None or str(kw.get("qid")) != current.get():
            raise RuntimeError("planner identity differs from its enclosing case")
        return original_plan(*a, **kw)
    def post(body, timeout):
        if args.no_key or current.get() is None:
            raise RuntimeError("paid request outside an enabled case")
        return request("/chat", body, timeout)
    run.answer_one, llm_client.llm_plan, llm_client._post = answer, plan, post
    run.main(["--xlsx-dir","/input/xlsx_files", "--queries","/input/queries.json",
              "--output","/output/submission.csv", "--openrouter-key",os.environ["OPENROUTER_API_KEY"],
              "--openrouter-model",args.model, "--openrouter-base-url","https://openrouter.ai/api/v1",
              "--log","/output/solver-log.jsonl"])


if __name__ == "__main__":
    main()
