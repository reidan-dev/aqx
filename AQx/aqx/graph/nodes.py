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
        "negate": False,  # wraps this single clause in NOT
    }


def if_output_ports(num_cases: int) -> List[Port]:
    """If has 1+ conditional branches (the first is "If", the rest "Elif N") plus an
    always-present "Else" - no condition needed for Else, it's just whatever's left."""
    ports = [Port("If" if i == 0 else f"Elif {i}", "exec") for i in range(max(num_cases, 1))]
    ports.append(Port("Else", "exec"))
    return ports




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
    "telegram": {
        "label": "Telegram",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"message": "telegram message", "max_sends": 0, "wait_seconds": 0.0},
    },
    "ocr": {
        "label": "OCR",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"region": "", "interval_seconds": 5.0, "extract_pattern": "", "last_value": None},
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
        "outputs": [Port("out", "exec")],
        "default_props": {"name": ""},
    },
    "logic": {
        "label": "Logic",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"condition": default_condition(), "last_value": None},
    },
    "loop_exit": {
        "label": "Exit Loop",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"condition": default_condition()},
    },
}

LOOP_TYPES = ("for_loop", "while_loop", "until_loop")


def make_node(node_type: str, node_id: str, x: float, y: float, props: Dict[str, Any] = None) -> Node:
    spec = NODE_SPECS[node_type]
    merged = {**spec["default_props"], **(props or {})}
    if node_type == "if":
        outputs = if_output_ports(len(merged.get("cases") or [default_condition()]))
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


def is_spliceable(node_type: str) -> bool:
    """Whether this node type can be dropped directly onto an existing wire to be
    auto-inserted inline (A -> this -> B, replacing A -> B) - only makes sense for a
    single-in/single-out block, since anything with multiple/dynamic ports (If,
    loops) would leave it ambiguous which downstream port continues to B."""
    spec = NODE_SPECS.get(node_type)
    return spec is not None and len(spec["inputs"]) == 1 and len(spec["outputs"]) == 1


HELP_TEXT: Dict[str, str] = {
    "start": "Where every run begins. Wire its \"out\" to whatever should happen first. Every flow needs exactly one.",
    "recorded_block": "Plays back a mouse/keyboard recording you made earlier. Double-click to pick which recording and how many times to repeat it.",
    "delay": "Pauses the flow for a fixed number of seconds before continuing to the next block.",
    "log": "Writes a message to the Execution Log - useful for marking progress or debugging a flow while you build it. Include $variable_name to insert a variable set earlier in the flow by a Set Variable block. Drag it directly onto an existing wire to tap into that point in the flow without disturbing what's already connected.",
    "telegram": "Sends a message via your Telegram bot (set the bot token and chat ID once in Settings > Preferences). Include $variable_name to insert a variable set earlier in the flow, same as Log. Optional \"Max sends\" caps how many times it actually sends even if reached more often (e.g. inside a loop); optional \"Wait before sending\" delays each send. A send failure is logged but doesn't stop the flow.",
    "ocr": "Reads text/numbers from a named screen region (drawn once, reusable by any node). Outputs whatever it reads, or nothing if it couldn't read anything. Optionally set an \"Extract pattern\" (regex) to pull just part of the reading - e.g. (\\d+)/ on \"Items 23/300\" extracts \"23\". With a capture group, the first group is used; without one, the whole match is used; no match means nothing was read, same as OCR finding no text at all.",
    "set_variable": "Stores a value - either typed literally or copied from an OCR node's last reading - under a name other blocks can reference in their conditions.",
    "if": "Branches into If / Elif.../Else based on one or more conditions (comparing an OCR reading or a variable). Use the \"...\" menu's dialog to add Elif branches or combine conditions with AND/OR/NOT.",
    "for_loop": "Repeats its \"body\" a fixed number of times, then continues from \"done\". 0 repeats means it skips the body entirely.",
    "while_loop": "Repeats its \"body\" for as long as a condition stays true (checked fresh before every pass), then continues from \"done\" once it goes false.",
    "until_loop": "Repeats its \"body\" until a condition becomes true (checked fresh before every pass, so it's the opposite of While), then continues from \"done\".",
    "connector": "A plain junction point - wire something into it, then wire its \"out\" to as many blocks as you like. All of them fire, in order, whenever it's reached. Purely for tidying up wire routing.",
    "logic": "Evaluates an AND/OR/NOT combination of OCR readings, variables, and other Logic blocks, and stores the result. Other blocks (If/While/Until/Exit Loop, or another Logic block) can then use \"Logic block\" as a condition source to reuse that result - this is how you compose logic out of smaller reusable pieces instead of one giant condition.",
    "loop_exit": "Checks a condition; if true, immediately exits its direct parent loop (the nearest enclosing For/While/Until) and continues from that loop's \"done\" port. If false, continues normally to \"out\". Placing it outside any loop is a no-op (logged as a warning).",
}


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
    if node.type == "telegram":
        message = str(node.props.get("message", ""))
        text = f'"{_truncate(message, 16)}"' if message else "(empty message)"
        max_sends = node.props.get("max_sends", 0)
        if max_sends:
            text += f" ≤{max_sends}×"
        wait_seconds = node.props.get("wait_seconds", 0.0)
        if wait_seconds:
            text += f" +{wait_seconds}s"
        return _truncate(text, 34)
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
        extract_marker = " [regex]" if node.props.get("extract_pattern") else ""
        return _truncate(f"{region} @{interval}s{extract_marker} → {value_str}", 40)
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
        return _truncate(node.props.get("name") or "Connector")
    if node.type == "logic":
        last_value = node.props.get("last_value")
        value_str = "" if last_value is None else f" → {last_value}"
        return _truncate(f"{describe_condition(node.props.get('condition', default_condition()))}{value_str}", 34)
    if node.type == "loop_exit":
        return _truncate(f"exit if {describe_condition(node.props.get('condition', default_condition()))}", 34)
    return ""
