"""Tiny self-contained JSON-Schema subset validator for src/plan_schema.json
(no jsonschema dependency). Supports: type (incl. lists), const, enum, required,
properties, additionalProperties:false, items, minItems, maxItems, minimum."""
import json
from pathlib import Path

SCHEMA_FILE = Path(__file__).resolve().parent / "plan_schema.json"
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def load_schema():
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def errors(v, s, path="$"):
    """Return a list of error strings (empty = valid)."""
    out = []
    t = s.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](v) for x in ts):
            return [f"{path}: expected {t}, got {type(v).__name__}"]
    if "const" in s and v != s["const"]:
        out.append(f"{path}: must equal {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        out.append(f"{path}: {v!r} not in enum")
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                out.append(f"{path}: missing required '{k}'")
        props = s.get("properties", {})
        for k, sub in v.items():
            if k in props:
                out += errors(sub, props[k], f"{path}.{k}")
            elif s.get("additionalProperties") is False:
                out.append(f"{path}: unexpected property '{k}'")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            out.append(f"{path}: fewer than {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            out.append(f"{path}: more than {s['maxItems']} items")
        if "items" in s:
            for i, x in enumerate(v):
                out += errors(x, s["items"], f"{path}[{i}]")
    if "minimum" in s and _TYPES["number"](v) and v < s["minimum"]:
        out.append(f"{path}: below minimum {s['minimum']}")
    return out


def validate(plan, schema=None):
    return errors(plan, schema if schema is not None else load_schema())
