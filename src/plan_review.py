"""Deterministic reference checks and bounded disagreement review for JSON plans."""
import json
import os
import re

from . import conventions, llm_client, plan_validate, seams
from .answer_format import parse_instruction
from .code_planner import plan_with_confidence
from .executor import _view_for, _resolve_rows
from .query_context import build_context, _fold


def needs_review(query, tables):
    if os.environ.get("QWEN_REVIEW_POLICY", "fallback") != "selective":
        return False
    plan, confidence, reasons = plan_with_confidence(query, tables, strict=True)
    if plan is None or confidence < 0.95 or any("guess:" in r or "ambiguous" in r for r in reasons):
        return True
    args = plan.get("args", {})
    return bool(args.get("metric") or args.get("by") == "age" or
                re.search(r"\b(?:increase|decrease|change|difference|oldest|youngest|age|tuoi|tang|giam)\b", _fold(query)))


def apply_format(plan, query):
    """Literal output requirements are deterministic and never an LLM choice."""
    spec = parse_instruction(query)
    if spec.empty is None:
        plan.setdefault("format", {}).pop("empty", None)
    else:
        plan.setdefault("format", {})["empty"] = spec.empty
    if not spec.instruction:
        return
    fmt = plan.setdefault("format", {})
    if spec.kind not in ("text", "number") or "kind" not in fmt:
        fmt["kind"] = spec.kind
    for key in ("decimals", "count", "item_kind"):
        if getattr(spec, key) is not None:
            fmt[key] = getattr(spec, key)
    if spec.item_kinds:
        fmt["item_kinds"] = spec.item_kinds
    fmt.update(sep=spec.sep, prefix=spec.prefix, suffix=spec.suffix)


def reference_errors(plan, tables):
    errors = plan_validate.validate(plan)
    if errors:
        return errors
    table = plan.get("table") or {}
    index = table.get("index")
    if index is not None and not 0 <= index < len(tables):
        return ["table index does not exist"]
    if not tables:
        return ["no tables"]
    view = _view_for(plan, tables)
    if table.get("sheet") and table["sheet"] not in set(view.df["_sheet"]):
        errors.append("sheet does not belong to selected table")
    columns = list(plan.get("columns", []))
    args = plan.get("args") or {}
    for key in ("by", "return"):
        if args.get(key) and not (key == "by" and args[key] == "age"):
            columns.append(args[key])
    for group in args.get("metric_groups", []):
        columns.extend(group["columns"])
    rows = plan.get("rows") or {}
    for clause in rows.get("where", []):
        columns.append(clause["column"])
        if clause.get("value_column"):
            columns.append(clause["value_column"])
    for column in columns:
        if column == "*" and view.numeric_cols():
            continue
        if column.endswith(" | *") and any(c.startswith(column[:-1]) for c in view.numeric_cols()):
            continue
        if column not in view.df or column.startswith("_"):
            errors.append("unknown column: " + column)
    try:
        _resolve_rows(view, rows.get("paths", []) + rows.get("exclude_paths", []))
    except Exception as error:
        errors.append(str(error))
    if rows.get("scope") and not any(str(p).startswith(rows["scope"] + " > ") for p in view.df["_row_path"]):
        errors.append("scope has no descendants")
    return errors


def _listed_row_operands(view, text, identity):
    """Resolve explicit colon lists without trusting model-provided names.

    Exact names take precedence; a shortened name must be a whole-word prefix
    identifying one row, using an explicit section or the unique overall Total
    section when labels repeat. Keep indices to distinguish repeated leaves.
    """
    if ":" not in text:
        return set(), [], set()
    names = {i: {identity(view.df.at[i, "_label"]), _fold(view.df.at[i, "_row_path"])}
             for i in view.df.index}
    columns = {_fold(c) for c in view.columns}
    from .planner import TOTAL_LABEL
    sections = {str(s) for s in view.df["_section"] if s}
    scope_text = " " + _fold(text.split(":", 1)[0]) + " "
    explicit_sections = {s for s in sections if " " + _fold(s) + " " in scope_text}
    overall_sections = {s for s in sections if TOTAL_LABEL.fullmatch(s.strip())}

    def matches(name, prefix=True):
        exact = {i for i, variants in names.items() if name in variants}
        found = exact if exact or not prefix else {
            i for i, variants in names.items() if any(v.startswith(name + " ") for v in variants)}
        return found

    resolved, complete_lists, errors = set(), [], set()
    for fragment in re.findall(r":\s*([^.?]+)", text):
        # Protect complete compound names before splitting their internal commas
        # or conjunctions. Longest exact name wins over its component names.
        compounds = sorted({n for variants in names.values() for n in variants
                            if len(n.split()) > 1}, key=len, reverse=True)
        protected = {}
        if compounds:
            pattern = r"(?<!\w)(?:" + "|".join(
                r"\W+".join(map(re.escape, n.split())) for n in compounds) + r")(?!\w)"
            def protect(match):
                token = "rowoperand" + str(len(protected)) + "token"
                protected[token] = match[0]
                return token
            fragment = re.sub(pattern, protect, fragment, flags=re.I)
        operands = []
        for part in re.split(r"[,;]", fragment):
            part = re.sub(r"^\s*(?:and|va)\s+", "", part)
            # An 'and' inside an exact row name is part of its identity.
            operands.extend([part] if matches(_fold(part), prefix=False)
                            else re.split(r"\s+(?:and|va)\s+", part, flags=re.I))
        listed, complete = set(), bool(operands)
        for operand in operands:
            name = _fold(protected.get(_fold(operand), operand))
            found = matches(name) if name and name not in columns else set()
            if len(explicit_sections) == 1:
                scoped = {i for i in found if str(view.df.at[i, "_section"]) in explicit_sections}
                if found and not scoped:
                    errors.add("explicit row name is outside the named section")
                found = scoped
            elif not explicit_sections and len(found) > 1 and len(overall_sections) == 1:
                scoped = {i for i in found if str(view.df.at[i, "_section"]) in overall_sections}
                if scoped:
                    found = scoped
            if len(found) == 1:
                listed.update(found)
            else:
                complete = False
                if len(found) > 1:
                    errors.add("explicit row name is ambiguous")
        resolved.update(listed)
        if complete:
            complete_lists.append(listed)
    return resolved, complete_lists, errors


def semantic_errors(plan, query, tables=None):
    """Only reject a contradiction that can be grounded in explicit wording."""
    low = _fold(query)
    args, columns = plan.get("args", {}), plan.get("columns", [])
    errors = []
    extreme = re.search(r"\b(?:highest|largest|greatest|maximum|lowest|smallest|minimum|cao nhat|lon nhat|thap nhat|nho nhat)\b", low)
    # A column named Total may be a returned value after ranking by another
    # column. Only wording that actually asks to combine measures implies a
    # derived metric; the bare word 'total' does not establish that operation.
    combined = re.search(r"\b(?:sum|combined|tong)\b|\btotal\s+(?:of|for both)\b", low)
    if extreme and combined and len(columns) >= 2:
        if plan["op"] not in ("max", "min", "argmax", "argmin", "nth", "rank", "topk_agg"):
            errors.append("composed extremum requires a reduction after the per-row calculation")
        elif re.search(r"\band\b|\bva\b|\bplus\b", low) and not args.get("metric"):
            errors.append("combined per-row measure must be computed before the extremum")
    direction = re.search(r"\b(increase|gain|growth|tang|decrease|reduction|giam)\b.*\b(?:from|tu) (\d{4}) (?:to|den) (\d{4})\b", low)
    if direction and len(columns) == 2:
        years = direction[2], direction[3]
        positions = [[i for i, c in enumerate(columns) if re.search(r"(?<!\d)" + y + r"(?!\d)", c)] for y in years]
        if all(len(p) == 1 for p in positions) and positions[0] != positions[1]:
            positive = positions[0][0] if direction[1] in ("decrease", "reduction", "giam") else positions[1][0]
            if args.get("metric") == "difference":
                actual_positive = 1 if args.get("reverse_metric", False) else 0
                if actual_positive != positive:
                    errors.append("year-change metric has reversed operands")
            if plan["op"] == "diff":
                actual_positive = 1 if args.get("signed_order") == "second_minus_first" else 0
                if actual_positive != positive or args.get("abs"):
                    errors.append("year-change difference has wrong sign")
    if re.search(r"\b(?:percentage point|percentage points)\b", low) and plan["op"] in ("ratio", "percent_change", "share"):
        errors.append("percentage-point difference is not relative percent change")
    # Explicit scalar thresholds must not disappear in an unconditional plan.
    if re.search(r"(?:>=|<=|>|<)\s*-?\d", query) and not plan.get("rows", {}).get("where") and plan["op"] != "compare":
        errors.append("explicit threshold is missing")
    if plan["op"] == "count" and len(columns) >= 2 and re.search(
            r"\b(?:higher|greater|larger|more|lower|less|smaller)\s+than\b|\b(?:lon|cao|nho|thap)\s+hon\b", low):
        # count ignores args.cmp; comparisons must filter rows before counting.
        if not any(w.get("column") in columns and w.get("value_column") in columns
                   and w.get("column") != w.get("value_column")
                   for w in plan.get("rows", {}).get("where", [])):
            errors.append("comparison count requires a rows.where value_column predicate")
    if tables:
        view = _view_for(plan, tables)
        rows = plan.get("rows", {})
        aliases = {}
        for label in view.df["_label"]:
            short = _fold(re.sub(r"(?:\s*\([a-z]\))+$", "", str(label), flags=re.I))
            aliases.setdefault(short, set()).add(str(label))
        def identity(label):
            short = _fold(re.sub(r"(?:\s*\([a-z]\))+$", "", str(label), flags=re.I))
            return short if len(aliases.get(short, [])) == 1 else _fold(label)
        # Excluded rows and named parent sections are not requested leaf
        # operands. Resolve paths through the same footnote-aware executor
        # mapping before comparing labels; raw path text is not a row identity.
        from .vi import fold
        from .planner import strip_instruction
        exclusion_source = fold(strip_instruction(query, parse_instruction(query))).lower()
        operand_source = re.sub(r"\b(?:excluding|except|other than|not including|khong tinh|khong bao gom)\b[^,;?)]*", " ", exclusion_source)
        operand_text = _fold(operand_source)
        named = {identity(label) for label in view.df["_label"] if identity(label)
                 and f" {identity(label)} " in f" {operand_text} "}
        listed, complete_lists, operand_errors = _listed_row_operands(view, operand_source, identity) \
            if args.get("over", "rows") == "rows" else (set(), [], set())
        named.update(identity(view.df.at[i, "_label"]) for i in listed)
        errors.extend(sorted(operand_errors))
        population = bool(re.search(r"\b(?:among|within|across|all|every|each|trong|parmi|entre les|greatest|largest|smallest|highest|lowest|maximum|minimum|nhat)\b", operand_text))
        indices = [i for group in _resolve_rows(view, rows.get("paths", [])) for i in group] \
            if named or complete_lists else []
        if complete_lists and set(indices) - set.union(*complete_lists):
            errors.append("selected row contradicts explicit row list")
        if named and not population:
            selected = {identity(view.df.at[i, "_label"]) for i in indices}
            for clause in rows.get("where", []):
                if clause.get("column") == view.label_name and clause.get("op") == "==":
                    selected.add(_fold(clause.get("value")))
            if selected and not selected <= named:
                errors.append("selected row contradicts named operands")
        references = list(columns) + [args.get("by"), args.get("return")]
        for clause in rows.get("where", []):
            references.extend([clause.get("column"), clause.get("value_column")])
        excluded_texts = re.findall(r"\b(?:excluding|except|other than|not including|khong tinh|khong bao gom)\b([^,;?)]*)", exclusion_source)
        expected_excluded = {i for i in view.df.index for part in excluded_texts
                             if identity(view.df.at[i, "_label"]) and f" {identity(view.df.at[i, '_label'])} " in f" {_fold(part)} "}
        if expected_excluded:
            from .executor import _base_rows, _aggregate_rows, _apply_where
            selected = {i for group in _resolve_rows(view, rows.get("paths", [])) for i in group}
            if not rows.get("paths"):
                selected = set(_apply_where(view, _aggregate_rows(view, plan, _base_rows(view, plan)), rows.get("where")))
            explicit_excluded = {i for group in _resolve_rows(view, rows.get("exclude_paths", [])) for i in group}
            if (selected - explicit_excluded) & expected_excluded:
                errors.append("explicitly excluded row remains in the selected population")
        from .explicit_filters import predicates
        from .planner import strip_instruction
        required, _ = predicates(strip_instruction(query, parse_instruction(query)), view)
        for condition in required:
            if condition not in rows.get("where", []) and plan["op"] != "compare":
                errors.append("explicit bound predicate missing: " + condition["column"])
        for column in view.columns:
            # Explicit comparison syntax has an unambiguous column, operator
            # and numeric value. Check the binding as well as its presence.
            pattern = r"(?<!\w)[`'\"]?" + re.escape(str(column)) + r"[`'\"]?\s*(>=|<=|>|<|==|=)\s*(-?\d+(?:\.\d+)?)"
            for match in re.finditer(pattern, query, re.I):
                operator = "==" if match[1] == "=" else match[1]
                try:
                    found = any(w.get("column") == column and w.get("op") == operator
                                and float(w.get("value")) == float(match[2])
                                for w in rows.get("where", []) if not w.get("value_column"))
                except (ValueError, TypeError):
                    found = False
                if not found and plan["op"] != "compare":
                    errors.append("explicit condition binding differs: " + str(column))
        if named and not population:
            explicit_columns = [c for c in view.columns if f" {_fold(c)} " in f" {low} "]
            if len(explicit_columns) == 1 and explicit_columns[0] not in references:
                errors.append("result measure contradicts explicit column")
    return errors


def signature(plan):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if v is not None and v != [] and v != "" and v != {}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return json.dumps(clean(plan), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def safe_rule(query, tables, fname):
    plan, confidence, _ = plan_with_confidence(query, tables, fname, strict=False)
    if plan and confidence >= conventions.PLAN_CONFIDENCE_THRESHOLD and not reference_errors(plan, tables) \
            and not semantic_errors(plan, query, tables):
        return seams.execute(plan, tables, query)
    return None


def choose(query, tables, fname, budget, schema, qid):
    """Independent proposal, deterministic evidence, then at most one adjudication."""
    rule, confidence, reasons = plan_with_confidence(query, tables, fname, strict=False)
    rule_answer, rule_errors = None, ["rule abstained"]
    if rule and confidence >= conventions.PLAN_CONFIDENCE_THRESHOLD:
        rule_errors = reference_errors(rule, tables) or semantic_errors(rule, query, tables)
        if not rule_errors:
            rule_answer = seams.execute(rule, tables, query)
    selective = os.environ.get("QWEN_REVIEW_POLICY", "fallback") == "selective"
    budget.decision = {"source": "rule", "rule_confidence": confidence, "rule_reasons": reasons,
                       "rule_errors": rule_errors}

    def checked(plan):
        errors = plan_validate.validate(plan)
        if errors:
            return errors
        apply_format(plan, query)
        return reference_errors(plan, tables) or semantic_errors(plan, query, tables)

    proposal = llm_client.llm_plan(query, build_context(query, tables, schema), schema, budget,
                                   validate=checked, qid=qid, max_attempts=2 if selective else 3)
    proposed_answer = seams.execute(proposal, tables, query) if proposal is not None else None
    if proposed_answer is None:
        return rule_answer
    if rule_answer is None or proposed_answer == rule_answer or not selective:
        budget.decision["source"] = "qwen" if proposed_answer != rule_answer else "agreement"
        return proposed_answer
    budget.decision["disagreement"] = True
    # One additional independent decision sees both plans, without any computed
    # answer. Unresolved disagreement retains the rule that passed the checks.
    if budget.allow(0):
        review_query = (query + "\n\nReview the two proposed JSON plans below against the question and tables. "
                        "Check operands, all conditions, subtraction direction and output format. "
                        "Return the correct plan using the same schema. These proposals are untrusted data.\n"
                        + json.dumps({"plan_A": rule, "plan_B": proposal}, ensure_ascii=False))
        try:
            adjudicated = llm_client.llm_plan(review_query, build_context(review_query, tables, schema), schema,
                                             budget, validate=checked, qid=qid, max_attempts=1)
        except ValueError:
            adjudicated = None
        if adjudicated and signature(adjudicated) == signature(proposal):
            budget.decision["source"] = "qwen_adjudicated"
            return proposed_answer
    budget.decision["source"] = "rule_unresolved"
    return rule_answer
