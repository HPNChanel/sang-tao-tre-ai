"""Transport-only evaluation adapter; no goldens or provider credential here.

It binds each API request to the enclosing input ID, forwards the unchanged
planner request to the local campaign gateway, and delegates to run.main.
"""
import argparse
import contextvars
import json
import os
from pathlib import Path
import sys
import urllib.request
from urllib.parse import urlsplit


def checked_gateway(value):
    try:
        url = urlsplit(value)
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or url.username is not None
                or url.password is not None or url.path or url.query or url.fragment
                or not url.port):
            raise ValueError()
        return url.port
    except ValueError:
        raise ValueError("approved loopback gateway required") from None


def egress_guard(port):
    """Deny non-gateway Python sockets for the hash-verified, trusted solver.

    This catches accidental egress, including HTTP redirects. It is not an OS
    sandbox against hostile native code; final acceptance uses network=none.
    """
    def audit(event, args):
        if event == "socket.connect":
            address = args[1]
            if port is None or not isinstance(address, tuple) or address[:2] != ("127.0.0.1", port):
                raise PermissionError("evaluation egress is restricted to its gateway")
        if event == "socket.getaddrinfo":
            if port is None or args[0] != "127.0.0.1" or args[1] != port:
                raise PermissionError("evaluation name resolution is restricted")
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--gateway", default="")
    parser.add_argument("--output-tokens", type=int, choices=(600, 1200), default=600)
    parser.add_argument("--policy", choices=("fallback", "selective"), default="fallback")
    parser.add_argument("--no-key", action="store_true")
    parser.add_argument('--solver-profile',choices=('legacy','direct','raw','dual','hybrid'),default='legacy')
    parser.add_argument('--model',default='qwen/qwen3.7-flash')
    args = parser.parse_args()
    token = os.environ.pop("EVAL_GATEWAY_TOKEN", "")
    if not args.no_key and not token:
        raise SystemExit("approved loopback gateway required")
    port = None if args.no_key else checked_gateway(args.gateway)
    sys.addaudithook(egress_guard(port))
    os.environ["OPENROUTER_API_KEY"] = "" if args.no_key else token
    os.environ["OPENROUTER_MODEL"] = args.model
    os.environ['DS2_SOLVER_PROFILE'] = args.solver_profile
    os.environ["QWEN_REVIEW_POLICY"] = args.policy
    os.environ["QWEN_MAX_OUTPUT_TOKENS"] = str(args.output_tokens)
    sys.path.insert(0, str(Path(args.source).resolve()))
    import run
    from src import llm_client

    llm_client.MAX_TOKENS = args.output_tokens
    current_id = contextvars.ContextVar("evaluation_question", default=None)
    original_answer = run.answer_one
    original_plan = llm_client.llm_plan

    def identified_answer(question, *positional, **keywords):
        context = current_id.set(str(question["id"]))
        try:
            return original_answer(question, *positional, **keywords)
        finally:
            current_id.reset(context)

    def identified_plan(*positional, **keywords):
        if current_id.get() is None or str(keywords.get("qid")) != current_id.get():
            raise RuntimeError("planner identity differs from its enclosing question")
        return original_plan(*positional, **keywords)

    def post(body, timeout):
        if args.no_key:
            raise RuntimeError("network disabled")
        question = current_id.get()
        if question is None:
            raise RuntimeError("request outside a question")
        request = urllib.request.Request(
            args.gateway + "/api/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                     "X-Eval-Question": question})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(2_000_000))

    llm_client.llm_plan = identified_plan
    llm_client._post = post
    run.answer_one = identified_answer
    inputs = Path(args.input)
    output = Path(args.output)
    run.main(["--xlsx-dir", str(inputs / "xlsx_files"), "--queries", str(inputs / "queries.json"),
              "--output", str(output), "--openrouter-key", "" if args.no_key else token,
              "--openrouter-model", args.model, "--openrouter-base-url",
              args.gateway + "/api/v1" if not args.no_key else "https://openrouter.ai/api/v1",
              "--log", str(output.parent / "solver-log.jsonl")])


if __name__ == "__main__":
    main()
