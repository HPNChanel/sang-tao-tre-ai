"""OpenRouter fallback planner (qwen/qwen3.7-flash). OFF unless OPENROUTER_API_KEY is set.

Hard cost cap: every attempt (incl. retries) is pre-charged against a per-question
budget using the worst case (prompt tokens estimate + max_tokens) x price; an
attempt is refused if that worst case would exceed the cap. Actual cost from the
response usage is then recorded. Cap default 0.02 USD (<< 0.10 rule).
"""
import json, os, time, urllib.request
from pathlib import Path

MODEL = "qwen/qwen3.7-flash"
URL = "https://openrouter.ai/api/v1/chat/completions"
PROMPTS = Path(__file__).parent / "prompts"
# USD per token; override via env if OpenRouter pricing differs. Conservative defaults.
PRICE_IN = float(os.environ.get("QWEN_PRICE_IN_PER_M", "0.50")) / 1e6
PRICE_OUT = float(os.environ.get("QWEN_PRICE_OUT_PER_M", "2.00")) / 1e6
COST_CAP = min(float(os.environ.get("COST_CAP_USD", "0.02")), 0.09)
MAX_TOKENS = 600
MAX_ATTEMPTS = 3


def enabled():
    return bool(os.environ.get("OPENROUTER_API_KEY"))


class Budget:
    def __init__(self, cap=COST_CAP):
        self.cap, self.spent, self.tokens = cap, 0.0, 0

    def worst_case(self, prompt_chars):
        return (prompt_chars / 2.5) * PRICE_IN + MAX_TOKENS * PRICE_OUT  # pessimistic chars/token

    def allow(self, prompt_chars):
        return self.spent + self.worst_case(prompt_chars) <= self.cap

    def charge(self, pin, pout, prompt_chars):
        c = pin * PRICE_IN + pout * PRICE_OUT
        if pin == 0 and pout == 0:  # no usage reported -> assume worst case
            c = self.worst_case(prompt_chars)
        self.spent += c
        self.tokens += pin + pout


def build_messages(query, tables_summary, schema):
    sys_p = (PROMPTS / "plan_system.txt").read_text(encoding="utf-8")
    usr = (PROMPTS / "plan_user.txt").read_text(encoding="utf-8").format(
        schema=schema, tables=tables_summary, query=query)
    return [{"role": "system", "content": sys_p}, {"role": "user", "content": usr}]


def llm_plan(query, tables_summary, schema, budget: Budget, timeout=60):
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
        req = urllib.request.Request(URL, data=body, headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
        except Exception:
            budget.charge(0, 0, chars)  # unknown spend: charge worst case
            time.sleep(1 + attempt)
            continue
        u = data.get("usage") or {}
        budget.charge(u.get("prompt_tokens", 0), u.get("completion_tokens", 0), chars)
        try:
            txt = data["choices"][0]["message"]["content"].strip()
            txt = txt[txt.find("{"): txt.rfind("}") + 1]
            return json.loads(txt)
        except Exception:
            continue
    return None
