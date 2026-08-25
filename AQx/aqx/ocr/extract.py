from __future__ import annotations

import re
from typing import Optional, Tuple


def apply_extract_pattern(value: Optional[str], pattern: str) -> Tuple[Optional[str], Optional[str]]:
    """Applies an optional regex to an OCR reading, pulling out just part of it (e.g.
    pattern r"(\\d+)/" on "Items 23/300" extracts "23"). Returns (result, error).

    Empty pattern or a None value passes the value through unchanged (no error).
    A pattern with a capture group returns the first group; without one, the whole
    match. No match returns None - the same "nothing read" meaning a failed OCR
    read already has, so downstream conditions treat it identically. An invalid
    regex returns the raw value unchanged, paired with the error text, so a typo in
    the pattern degrades to "no extraction" rather than losing the reading."""
    if not pattern or value is None:
        return value, None
    try:
        match = re.search(pattern, str(value))
    except re.error as exc:
        return value, str(exc)
    if match is None:
        return None, None
    return (match.group(1) if match.groups() else match.group(0)), None
