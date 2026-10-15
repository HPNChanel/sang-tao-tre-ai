"""OpenRouter fallback planner (qwen/qwen3.7-flash). OFF unless OPENROUTER_API_KEY is set.

Hard cost cap: every attempt (incl. retries) is pre-charged against a per-question
budget using the worst case (prompt tokens estimate + max_tokens) x price; an
attempt is refused if that worst case would exceed the cap. Actual cost from the
response usage is then recorded. Cap default 0.02 USD (<< 0.10 rule).
"""
import json, os, sys, time, urllib.request
from pathlib import Path

MODEL = "qwen/qwen3.7-flash"
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


def enabled():
    return bool(os.environ.get("OPENROUTER_API_KEY"))


class Budget:
    def __init__(self, cap=COST_CAP):
        self.cap, self.spent, self.tokens, self.attempts = cap, 0.0, 0, 0

    @staticmethod
    def worst_tokens(prompt_chars):
        return int(prompt_chars / 2.5) + 1 + MAX_TOKENS  # pessimistic chars/token

    def worst_case(self, prompt_chars):
        return (prompt_chars / 2.5 + 1) * PRICE_IN + MAX_TOKENS * PRICE_OUT

    def allow(self, prompt_chars):
        return self.spent + self.worst_case(prompt_chars) <= self.cap

    def charge(self, pin, pout, prompt_chars):
        """Record one attempt. Failed / usage-less attempts are charged worst case."""
        if not pin and not pout:
            self.spent += self.worst_case(prompt_chars)
            self.tokens += self.worst_tokens(prompt_chars)
        else:
            self.spent += pin * PRICE_IN + pout * PRICE_OUT
            self.tokens += pin + pout
        self.attempts += 1


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
    msgs = build_messages(query, tables_summary, schema)
    chars = sum(len(m["content"]) for m in msgs)
    for attempt in range(MAX_ATTEMPTS):
        if not budget.allow(chars):
            return None
        body = json.dumps({"model": MODEL, "messages": msgs, "max_tokens": MAX_TOKENS,
                           "temperature": 0, "response_format": {"type": "json_object"}}).encode()
        try:
            data = _post(body, timeout)
        except Exception:
            budget.charge(0, 0, chars)  # unknown spend: charge worst case
            time.sleep(RETRY_SLEEP * (1 + attempt))
            continue
        u = (data.get("usage") if isinstance(data, dict) else None) or {}
        budget.charge(u.get("prompt_tokens", 0) or 0, u.get("completion_tokens", 0) or 0, chars)
        try:
            txt = data["choices"][0]["message"]["content"].strip()
            txt = txt[txt.find("{"): txt.rfind("}") + 1]
            plan = json.loads(txt)
        except Exception:
            continue
        if validate is not None:
            errs = validate(plan)
            if errs:  # counts as a used attempt; cap + MAX_ATTEMPTS still apply
                print(f"[schema] {qid}: attempt {attempt + 1} plan invalid: "
                      + "; ".join(errs[:3]), file=sys.stderr)
                continue
        return plan
    return None
