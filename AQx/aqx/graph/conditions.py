from __future__ import annotations

from typing import Any, Optional

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
        return compare_to in value_str

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


def describe_condition(condition: dict) -> str:
    """Short human-readable summary of a condition dict, for node summaries/labels."""
    source_type = condition.get("source_type")
    if source_type == "ocr":
        source = condition.get("source_label") or "(no region)"
    elif source_type == "variable":
        source = f"${condition.get('source_name') or '(unset)'}"
    else:
        source = "(no source)"
    op = OPERATOR_LABELS.get(condition.get("operator", "equals"), "=")
    compare_to = condition.get("compare_to", "")
    return f"{source} {op} '{compare_to}'"
