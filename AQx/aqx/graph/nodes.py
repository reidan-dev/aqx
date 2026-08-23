from __future__ import annotations

from typing import Any, Dict, List

from .conditions import describe_condition
from .model import Node, Port


def default_condition() -> dict:
    return {
        "source_type": "ocr",  # "ocr" | "variable"
        "source_node": "",  # OCR node id, when source_type == "ocr"
        "source_label": "",  # OCR node's region name, cached for display
        "source_name": "",  # variable name, when source_type == "variable"
        "operator": "equals",
        "compare_to": "",
    }


def if_output_ports(num_cases: int) -> List[Port]:
    """If has 1+ conditional branches (the first is "If", the rest "Elif N") plus an
    always-present "Else" - no condition needed for Else, it's just whatever's left."""
    ports = [Port("If" if i == 0 else f"Elif {i}", "exec") for i in range(max(num_cases, 1))]
    ports.append(Port("Else", "exec"))
    return ports


def connector_output_ports(num_outputs: int) -> List[Port]:
    """Connector fans a single trigger out to 1+ destinations, all fired in order -
    no conditions, just plain numbered exec outputs."""
    return [Port(f"out{i + 1}", "exec") for i in range(max(num_outputs, 1))]


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
    "ocr": {
        "label": "OCR",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"region": "", "interval_seconds": 5.0, "last_value": None},
    },
    "set_variable": {
        "label": "Set Variable",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {
            "name": "",
            "source_type": "literal",  # "literal" | "ocr"
            "literal_value": "",
            "source_node": "",
            "source_label": "",
        },
    },
    "if": {
        "label": "If",
        "inputs": [Port("in", "exec")],
        "outputs": if_output_ports(1),
        "default_props": {"cases": [default_condition()]},
    },
    "for_loop": {
        "label": "For",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("body", "exec"), Port("done", "exec")],
        "default_props": {"count": 3},
    },
    "while_loop": {
        "label": "While",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("body", "exec"), Port("done", "exec")],
        "default_props": {"condition": default_condition()},
    },
    "until_loop": {
        "label": "Until",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("body", "exec"), Port("done", "exec")],
        "default_props": {"condition": default_condition()},
    },
    "connector": {
        "label": "Connector",
        "inputs": [Port("in", "exec")],
        "outputs": connector_output_ports(1),
        "default_props": {"name": "", "num_outputs": 1},
    },
}

LOOP_TYPES = ("for_loop", "while_loop", "until_loop")


def make_node(node_type: str, node_id: str, x: float, y: float, props: Dict[str, Any] = None) -> Node:
    spec = NODE_SPECS[node_type]
    merged = {**spec["default_props"], **(props or {})}
    if node_type == "if":
        outputs = if_output_ports(len(merged.get("cases") or [default_condition()]))
    elif node_type == "connector":
        outputs = connector_output_ports(int(merged.get("num_outputs", 1)))
    else:
        outputs = list(spec["outputs"])
    return Node(
        id=node_id,
        type=node_type,
        x=x,
        y=y,
        props=merged,
        inputs=list(spec["inputs"]),
        outputs=outputs,
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
    if node.type == "ocr":
        region = node.props.get("region") or "(none)"
        interval = node.props.get("interval_seconds", 5.0)
        last_value = node.props.get("last_value")
        value_str = f"'{_truncate(str(last_value), 8)}'" if last_value else "–"
        return _truncate(f"{region} @{interval}s → {value_str}")
    if node.type == "set_variable":
        name = node.props.get("name") or "(unnamed)"
        if node.props.get("source_type") == "ocr":
            value_desc = node.props.get("source_label") or "(no region)"
        else:
            value_desc = f"'{node.props.get('literal_value', '')}'"
        return _truncate(f"${name} = {value_desc}")
    if node.type == "if":
        cases = node.props.get("cases") or []
        n = len(cases)
        first = describe_condition(cases[0]) if cases else "(no condition)"
        extra = f" (+{n - 1} elif)" if n > 1 else ""
        return _truncate(f"{first}{extra}", 34)
    if node.type in LOOP_TYPES:
        if node.type == "for_loop":
            count = node.props.get("count", 0)
            return f"×{count}" if count else "×∞"
        return _truncate(describe_condition(node.props.get("condition", default_condition())), 30)
    if node.type == "connector":
        name = node.props.get("name") or "Connector"
        count = node.props.get("num_outputs", 1)
        return _truncate(f"{name} ×{count}")
    return ""
