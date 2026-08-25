from __future__ import annotations

from typing import Any, Callable, Optional

OPERATORS = ["equals", "not_equals", "greater_than", "less_than", "contains"]

OPERATOR_LABELS = {
    "equals": "=",
    "not_equals": "≠",
    "greater_than": ">",
    "less_than": "<",
    "contains": "contains",
}


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_values(value: Any, operator: str, compare_to: str) -> bool:
    """Compares a runtime value (an OCR reading or a variable, usually a string or
    None) against a user-typed literal. Numeric comparison is used whenever both
    sides parse as numbers; otherwise falls back to string comparison. A None value
    (nothing read/set yet) never satisfies any operator except not_equals."""
    if value is None:
        return operator == "not_equals"

    value_str = str(value)

    if operator == "contains":
        # Case-insensitive: OCR's exact casing is unpredictable (font/rendering
        # dependent) and a case-sensitive miss here fails silently - the condition
        # just never triggers, with nothing to indicate why.
        return compare_to.lower() in value_str.lower()

    a, b = _as_float(value_str), _as_float(compare_to)
    if operator == "greater_than":
        return a is not None and b is not None and a > b
    if operator == "less_than":
        return a is not None and b is not None and a < b
    if operator == "equals":
        return (a == b) if (a is not None and b is not None) else (value_str == compare_to)
    if operator == "not_equals":
        return not compare_values(value, "equals", compare_to)
    return False


def is_compound(condition: dict) -> bool:
    return bool(condition) and "clauses" in condition


def evaluate_condition(condition: dict, resolve_leaf_value: Callable[[dict], Any]) -> bool:
    """Recursively evaluates a condition, which is either a leaf (resolved via
    resolve_leaf_value, then compared) or a compound {"join": "and"|"or", "clauses":
    [...]} of sub-conditions. Either shape may carry "negate": true to invert its
    own result - a leaf negates its comparison, a compound negates the whole group."""
    if not condition:
        return False
    if is_compound(condition):
        join = condition.get("join", "and")
        clauses = condition.get("clauses") or []
        results = [evaluate_condition(c, resolve_leaf_value) for c in clauses]
        if not results:
            result = False
        elif join == "or":
            result = any(results)
        else:
            result = all(results)
    else:
        value = resolve_leaf_value(condition)
        result = compare_values(value, condition.get("operator", "equals"), condition.get("compare_to", ""))
    if condition.get("negate"):
        result = not result
    return result


def describe_condition(condition: dict) -> str:
    """Short human-readable summary of a condition dict, for node summaries/labels."""
    if not condition:
        return "(no condition)"
    if is_compound(condition):
        clauses = condition.get("clauses") or []
        join_word = "AND" if condition.get("join", "and") == "and" else "OR"
        parts = [describe_condition(c) for c in clauses]
        text = f" {join_word} ".join(parts) if parts else "(empty)"
        if len(clauses) > 1:
            text = f"({text})"
    else:
        source_type = condition.get("source_type")
        if source_type == "ocr":
            source = condition.get("source_label") or "(no region)"
        elif source_type == "variable":
            source = f"${condition.get('source_name') or '(unset)'}"
        else:
            source = "(no source)"
        op = OPERATOR_LABELS.get(condition.get("operator", "equals"), "=")
        compare_to = condition.get("compare_to", "")
        text = f"{source} {op} '{compare_to}'"
    return f"NOT {text}" if condition.get("negate") else text
