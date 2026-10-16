"""Bounded Qwen planning. OFF unless OPENROUTER_API_KEY is supplied.

Reserve USD .03 before dispatch, at most three attempts per shared question
budget. Only finite usage.cost is billed cost; missing usage remains reserved.
Request/provider/output bounds support this operational cap, not a claim that
an estimated token count is a provider-certified billing guarantee.
"""
import json, os, re, sys, time, urllib.request
import signal
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

APPROVED_MODEL = "qwen/qwen3.7-flash"
MODEL = os.environ.get("OPENROUTER_MODEL", APPROVED_MODEL)
URL = "https://openrouter.ai/api/v1/chat/completions"
PROMPTS = Path(__file__).parent / "prompts"
# Price ceilings, NOT observed billing rates. The approved provider has
# context-size tiers; a flat cheapest-endpoint estimate is not accounting.
PRICE_IN, PRICE_OUT = 0.25 / 1e6, 1.0 / 1e6
MAX_BODY_BYTES = 24 * 1024
RESERVATION = Decimal("0.03")
PROVIDER = {"only": ["alibaba"], "allow_fallbacks": False, "require_parameters": True,
            "max_price": {"prompt": 0.25, "completion": 1, "request": 0}}
BILLING_HALT_REASON = None


def _money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


_configured_cap = _money(os.environ.get("COST_CAP_USD", "0.09"))
COST_CAP = float(min(_configured_cap, Decimal("0.09"))) if _configured_cap is not None else 0.0
MAX_TOKENS = 1200 if os.environ.get("QWEN_MAX_OUTPUT_TOKENS") == "1200" else 600
MAX_ATTEMPTS = 3
RETRY_SLEEP = 1.0
REASONING = {"enabled": False}
_STRIP = {"description", "$schema", "$id", "title"}


@contextmanager
def _accounting_atomic():
    """A query alarm must not release a reserve halfway through settlement."""
    previous = None
    if hasattr(signal, "pthread_sigmask"):
        blocked = {getattr(signal, name) for name in ("SIGALRM", "SIGINT", "SIGTERM")
                   if hasattr(signal, name)}
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        if previous is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)


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
    def valid(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    return valid(u.get("prompt_tokens")), valid(u.get("completion_tokens")), valid(r)


def enabled():
    return bool(os.environ.get("OPENROUTER_API_KEY"))


class Budget:
    def __init__(self, cap=COST_CAP):
        amount = _money(cap)
        self._cap = min(amount, Decimal("0.09")) if amount is not None else Decimal(0)
        self.cap, self.tokens, self.attempts = float(self._cap), 0, 0
        self._actual, self._unknown = Decimal(0), Decimal(0)
        self.completion_tokens = self.reasoning_tokens = 0
        self.log = []
        self.halt_reason = None
        self.deadline = None
        self.token_usage_unknown = False

    @property
    def spent(self):
        """Conservative exposure, retained for the existing budget interface."""
        return float(self._actual + self._unknown)

    @property
    def actual_usd(self):
        return float(self._actual)

    @property
    def unresolved_usd(self):
        return float(self._unknown)

    @property
    def cost_usd(self):
        return None if self._unknown else float(self._actual)

    @staticmethod
    def worst_tokens(prompt_chars):
        # Diagnostic bound only. Never report this estimate as token usage.
        return MAX_BODY_BYTES + 4096 + MAX_TOKENS

    def worst_case(self, prompt_chars):
        return float(RESERVATION)

    def allow(self, prompt_chars):
        return (not self.halt_reason and not BILLING_HALT_REASON and self.attempts < MAX_ATTEMPTS
                and self._actual + self._unknown + RESERVATION <= self._cap
                and (self.deadline is None or time.monotonic() < self.deadline))

    def reserve(self, prompt_chars=0):
        if not self.allow(prompt_chars):
            return None
        self._unknown += RESERVATION
        self.attempts += 1
        self.token_usage_unknown = True
        self.log.append({"attempt": self.attempts, "status": "dispatched", "prompt_tokens": None,
                         "completion_tokens": None, "reasoning_tokens": None,
                         "worst_case": True, "reserved_usd": float(RESERVATION), "cost_usd": None})
        return len(self.log) - 1

    def finish(self, index, data=None, status="ok"):
        with _accounting_atomic():
            self._finish(index, data, status)

    def _finish(self, index, data, status):
        global BILLING_HALT_REASON
        entry = self.log[index]
        if entry.get("finished"):
            raise ValueError("attempt already accounted")
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        amount = _money(usage.get("cost"))
        pin, pout, reasoning = usage_tokens(data)
        known_tokens = all(isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)
                           and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens"))
        self.tokens += pin + pout
        self.completion_tokens += pout
        self.reasoning_tokens += reasoning
        if amount is not None:
            self._unknown -= RESERVATION
            self._actual += amount
            if amount > RESERVATION:
                self.halt_reason = BILLING_HALT_REASON = "actual_cost_exceeded_reservation"
        entry.update(status=status, finished=True, prompt_tokens=pin if known_tokens else None,
                     completion_tokens=pout if known_tokens else None, reasoning_tokens=reasoning,
                     worst_case=amount is None, cost_usd=None if amount is None else float(amount),
                     generation_id=data.get("id") if isinstance(data, dict) else None)
        self.token_usage_unknown = any(e["prompt_tokens"] is None or e["completion_tokens"] is None for e in self.log)

    def charge(self, pin, pout, prompt_chars, reasoning=0, status="ok", cost=None):
        """Compatibility helper; token counts alone never imply billed cost."""
        index = self.reserve(prompt_chars)
        if index is not None:
            self.finish(index, {"usage": {"prompt_tokens": pin, "completion_tokens": pout,
                                         "reasoning_tokens": reasoning, "cost": cost}}, status)


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
        return json.loads(r.read(2_000_000))


def llm_plan(query, tables_summary, schema, budget: Budget, timeout=60, validate=None, qid="", max_attempts=None):
    """Return plan dict or None. Never raises on network/parse errors."""
    if not enabled() or MODEL != APPROVED_MODEL or BILLING_HALT_REASON:
        return None
    msgs = build_messages(query, tables_summary, compact_schema(schema))
    chars = sum(len(m["content"]) for m in msgs)
    body = json.dumps({"model": MODEL, "messages": msgs, "max_tokens": min(MAX_TOKENS, 1200),
                       "temperature": 0, "response_format": {"type": "json_object"},
                       "reasoning": REASONING, "provider": PROVIDER},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        budget.halt_reason = "request_exceeds_24_kib"
        return None
    attempts_here = MAX_ATTEMPTS if max_attempts is None else min(max(0, max_attempts), MAX_ATTEMPTS)
    for attempt in range(attempts_here):
        if not budget.allow(chars):
            return None
        index = budget.reserve(chars)  # retained even if SIGALRM interrupts _post
        if index is None:
            return None
        remaining = timeout if budget.deadline is None else min(timeout, budget.deadline - time.monotonic())
        if remaining <= 0:
            return None
        try:
            data = _post(body, remaining)
        except Exception as e:
            budget.finish(index, status=f"error:{type(e).__name__}")
            if budget.allow(chars) and attempt + 1 < attempts_here:
                wait = RETRY_SLEEP * (1 + attempt)
                if budget.deadline is not None:
                    wait = min(wait, max(0, budget.deadline - time.monotonic()))
                time.sleep(wait)
            continue
        try:
            ch = data["choices"][0]
            finish = ch.get("finish_reason")
            plan = parse_plan(ch["message"].get("content") or "")
        except Exception:
            finish, plan = None, None
        if plan is None:
            budget.finish(index, data, status="truncated" if finish == "length" else "unparseable")
            print(f"[llm] {qid}: attempt {attempt + 1} no JSON plan (finish={finish})", file=sys.stderr)
            continue
        budget.finish(index, data, status="ok")
        if budget.halt_reason:
            return None
        if validate is not None:
            errs = validate(plan)
            if errs:  # counts as a used attempt; cap + MAX_ATTEMPTS still apply
                budget.log[-1]["status"] = "schema_invalid"
                print(f"[schema] {qid}: attempt {attempt + 1} plan invalid: "
                      + "; ".join(errs[:3]), file=sys.stderr)
                continue
        return plan
    return None
