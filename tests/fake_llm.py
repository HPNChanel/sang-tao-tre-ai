"""Fake OpenRouter transport (no network, no key)."""
import json
import urllib.error

PLAN = {"version": 1, "op": "argmax", "args": {"ties": "all"}, "format": {"kind": "name"}}


def resp(content, prompt=400, completion=60, reasoning=None, finish="stop", reasoning_text=None):
    # Explicit synthetic billing metadata; tests must not infer billed cost
    # from token counts when the response has no usage.cost.
    u = {"prompt_tokens": prompt, "completion_tokens": completion,
         "cost": prompt * 0.00000025 + completion * 0.000001}
    if reasoning is not None:
        u["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    msg = {"role": "assistant", "content": content}
    if reasoning_text:
        msg["reasoning"] = reasoning_text
    return {"id": "gen-fake", "model": "qwen/qwen3.7-flash", "usage": u,
            "choices": [{"index": 0, "finish_reason": finish, "message": msg}]}


def plain(plan=PLAN):
    return resp(json.dumps(plan))


def fenced(plan=PLAN, lang="json"):
    return resp(f"```{lang}\n{json.dumps(plan, indent=1)}\n```")


def reasoning_preamble(plan=PLAN):
    think = "Let me think. The user asks which {row} is largest... " * 40
    return resp(f"<think>{think}</think>\nOkay, here is the plan:\n{json.dumps(plan)}",
                completion=590, reasoning=520, reasoning_text=think)


def truncated(plan=PLAN):
    return resp(json.dumps(plan)[:25], completion=600, reasoning=570, finish="length")


def http_error(code):
    return urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions", code,
                                  {402: "Payment Required", 429: "Too Many Requests"}.get(code, "err"),
                                  {}, None)


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.bodies = list(responses), []

    def __call__(self, body, timeout):
        self.bodies.append(json.loads(body))
        r = self.responses[min(len(self.bodies), len(self.responses)) - 1]
        if isinstance(r, BaseException):
            raise r
        return r
