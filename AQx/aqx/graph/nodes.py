from __future__ import annotations

from typing import Any, Dict

from .model import Node, Port

NODE_SPECS: Dict[str, dict] = {
    "start": {
        "label": "Start",
        "inputs": [],
        "outputs": [Port("out", "exec")],
        "default_props": {},
    },
    "recorded_block": {
        "label": "Record Block",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"recording": "", "repeat": 1},
    },
    "delay": {
        "label": "Delay",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"seconds": 1.0},
    },
    "log": {
        "label": "Log",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"message": "log message"},
    },
}


def make_node(node_type: str, node_id: str, x: float, y: float, props: Dict[str, Any] = None) -> Node:
    spec = NODE_SPECS[node_type]
    merged = {**spec["default_props"], **(props or {})}
    return Node(
        id=node_id,
        type=node_type,
        x=x,
        y=y,
        props=merged,
        inputs=list(spec["inputs"]),
        outputs=list(spec["outputs"]),
    )


def label_for(node_type: str) -> str:
    return NODE_SPECS[node_type]["label"]


def node_has_summary(node_type: str) -> bool:
    """Whether this node type has configurable props worth showing on the block face."""
    return bool(NODE_SPECS[node_type]["default_props"])


def _truncate(text: str, limit: int = 22) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summary_for(node: Node) -> str:
    """One-line summary of a node's current props, shown on the block itself."""
    if node.type == "delay":
        return f"{node.props.get('seconds', 1.0)}s"
    if node.type == "log":
        message = str(node.props.get("message", ""))
        return f'"{_truncate(message)}"' if message else "(empty message)"
    if node.type == "recorded_block":
        recording = node.props.get("recording") or "(none set)"
        repeat = node.props.get("repeat", 1)
        repeat_str = "×∞" if repeat == 0 else f"×{repeat}"
        return _truncate(f"{recording} {repeat_str}")
    return ""
