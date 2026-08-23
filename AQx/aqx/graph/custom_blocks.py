from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from ..paths import CUSTOM_BLOCKS_DIR


def _safe_filename(name: str) -> str:
    """Collapses a user-typed block name to a filesystem-safe stem (no path
    separators or traversal), so a name like "../../etc/passwd" can't escape
    CUSTOM_BLOCKS_DIR."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "block"
    return slug[:80]


def list_custom_blocks() -> List[Dict[str, Any]]:
    CUSTOM_BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
    blocks = []
    for path in sorted(CUSTOM_BLOCKS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        base_type = data.get("base_type")
        if not base_type:
            continue
        blocks.append({"name": data.get("name", path.stem), "base_type": base_type, "props": data.get("props", {})})
    return blocks


def save_custom_block(name: str, base_type: str, props: dict) -> None:
    CUSTOM_BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
    path = CUSTOM_BLOCKS_DIR / f"{_safe_filename(name)}.json"
    path.write_text(json.dumps({"name": name, "base_type": base_type, "props": props}, indent=2))
