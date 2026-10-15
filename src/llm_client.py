"""OpenRouter fallback planner (qwen/qwen3.7-flash). OFF unless OPENROUTER_API_KEY is set.

Hard cost cap: every attempt (incl. retries) is pre-charged against a per-question
budget using the worst case (prompt tokens estimate + max_tokens) x price; an
attempt is refused if that worst case would exceed the cap. Actual cost from the
response usage is then recorded. Cap default 0.02 USD (<< 0.10 rule).
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path

MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3.7-flash")
URL = "https://openrouter.ai/api/v1/chat/completions"
PROMPTS = Path(__file__).parent / "prompts"
# USD per 1M tokens. Looked up 2026-10-15 from
#   https://openrouter.ai/api/v1/models  (qwen/qwen3.7-flash: prompt 0.03, completion 0.13)
#   https://openrouter.ai/api/v1/models/qwen/qwen3.7-flash/endpoints
#   (Alibaba endpoints: 0.03/0.13 and 0.0345/0.1495)
# Default = the MOST expensive endpoint so the budget never under-counts.
PRICE_IN = float(os.environ.get("QWEN_PRICE_IN_PER_M", "0.0345")) / 1e6
PRICE_OUT = float(os.environ.get("QWEN_PRICE_OUT_PER_M", "0.1495")) / 1e6
COST_CAP = min(float(os.environ.get("COST_CAP_USD", "0.02")), 0.09)
MAX_TOKENS = 600
MAX_ATTEMPTS = 3
RETRY_SLEEP = 1.0
REASONING = {"enabled": False}
_STRIP = {"description", "$schema", "$id", "title"}


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in _STRIP}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def compact_schema(schema):
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError:
            return schema
    return json.dumps(_strip(schema), separators=(",", ":"), ensure_ascii=False)


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?(</think>|$)", re.S)


def parse_plan(txt):
    if not isinstance(txt, str) or not txt.strip():
        return None
    t = _THINK.sub("", txt)
    cands = [m.group(1) for m in _FENCE.finditer(t)] + [t]
    dec = json.JSONDecoder()
    for c in cands:
        found = None
        for i in [m.start() for m in re.finditer(r"\{", c)]:
            try:
                obj, _ = dec.raw_decode(c, i)
            except ValueError:
                continue
            if isinstance(obj, dict) and "op" in obj:
                found = obj
            elif found is None and isinstance(obj, dict):
                found = obj
        if found is not None:
            return found
    return None


def usage_tokens(data):
    u = (data.get("usage") if isinstance(data, dict) else None) or {}
    if not isinstance(u, dict):
        u = {}
    det = u.get("completion_tokens_details") or {}
    r = (det.get("reasoning_tokens") if isinstance(det, dict) else 0) or u.get("reasoning_tokens") or 0
    return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), int(r or 0)


def enabled():
    return bool(os.environ.get("OPENROUTER_API_KEY"))


class Budget:
    def __init__(self, cap=COST_CAP):
        self.cap, self.spent, self.tokens, self.attempts = cap, 0.0, 0, 0
        self.completion_tokens = self.reasoning_tokens = 0
        self.log = []

    @staticmethod
    def worst_tokens(prompt_chars):
        return int(prompt_chars / 2.5) + 1 + MAX_TOKENS  # pessimistic chars/token

    def worst_case(self, prompt_chars):
        return (prompt_chars / 2.5 + 1) * PRICE_IN + MAX_TOKENS * PRICE_OUT

    def allow(self, prompt_chars):
        return self.spent + self.worst_case(prompt_chars) <= self.cap

    def charge(self, pin, pout, prompt_chars, reasoning=0, status="ok"):
        """Record one attempt. Failed / usage-less attempts are charged worst case."""
        if not pin and not pout:
            cost, toks, worst = self.worst_case(prompt_chars), self.worst_tokens(prompt_chars), True
        else:
            cost, toks, worst = pin * PRICE_IN + pout * PRICE_OUT, pin + pout, False
        self.spent += cost
        self.tokens += toks
        self.completion_tokens += pout
        self.reasoning_tokens += reasoning
        self.attempts += 1
        self.log.append({"attempt": self.attempts, "status": status, "prompt_tokens": pin,
                         "completion_tokens": pout, "reasoning_tokens": reasoning,
                         "worst_case": worst, "cost_usd": round(cost, 8)})


def build_messages(query, tables_summary, schema):
    sys_p = (PROMPTS / "plan_system.txt").read_text(encoding="utf-8")
    usr = (PROMPTS / "plan_user.txt").read_text(encoding="utf-8").format(
        schema=schema, tables=tables_summary, query=query)
    return [{"role": "system", "content": sys_p}, {"role": "user", "content": usr}]


def _post(body: bytes, timeout):
    """Single HTTP attempt (mocked in tests)."""
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def llm_plan(query, tables_summary, schema, budget: Budget, timeout=60, validate=None, qid=""):
    """Return plan dict or None. Never raises on network/parse errors."""
    if not enabled():
        return None
    msgs = build_messages(query, tables_summary, compact_schema(schema))
    chars = sum(len(m["content"]) for m in msgs)
    for attempt in range(MAX_ATTEMPTS):
        if not budget.allow(chars):
            return None
        body = json.dumps({"model": MODEL, "messages": msgs, "max_tokens": MAX_TOKENS,
                           "temperature": 0, "response_format": {"type": "json_object"},
                           "reasoning": REASONING}).encode()
        try:
            data = _post(body, timeout)
        except Exception as e:
            budget.charge(0, 0, chars, status=f"error:{type(e).__name__}")
            time.sleep(RETRY_SLEEP * (1 + attempt))
            continue
        pin, pout, rt = usage_tokens(data)
        try:
            ch = data["choices"][0]
            finish = ch.get("finish_reason")
            plan = parse_plan(ch["message"].get("content") or "")
        except Exception:
            finish, plan = None, None
        if plan is None:
            budget.charge(pin, pout, chars, rt, status="truncated" if finish == "length" else "unparseable")
            print(f"[llm] {qid}: attempt {attempt + 1} no JSON plan (finish={finish})", file=sys.stderr)
            continue
        budget.charge(pin, pout, chars, rt, status="ok")
        if validate is not None:
            errs = validate(plan)
            if errs:  # counts as a used attempt; cap + MAX_ATTEMPTS still apply
                budget.log[-1]["status"] = "schema_invalid"
                print(f"[schema] {qid}: attempt {attempt + 1} plan invalid: "
                      + "; ".join(errs[:3]), file=sys.stderr)
                continue
        return plan
    return None
