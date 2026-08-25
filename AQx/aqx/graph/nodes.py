from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .conditions import describe_condition
from .model import Node, Port

DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*[hH])?\s*(?:(\d+)\s*[mM])?\s*$")


def parse_duration_seconds(text: str) -> Optional[int]:
    """Parses a duration like "4h", "2m", or "3h30m" into total seconds. Hours and
    minutes are each optional but at least one must be present, and hours (if any)
    must come before minutes. Returns None for anything that doesn't match (empty
    string, garbage, minutes-before-hours, etc.)."""
    if not text or not text.strip():
        return None
    m = DURATION_RE.match(text)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return hours * 3600 + minutes * 60


def format_duration_seconds(seconds: float) -> str:
    """The inverse of parse_duration_seconds, for status messages - always shows
    both parts once there's more than an hour, drops the hours part otherwise."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h{minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


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
    "code": {
        "label": "Code",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        "default_props": {"code": ""},
    },
    "controls": {
        "label": "Controls",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        # Each entry: {"name": str, "options": [str, ...], "current": str} - "current"
        # is both the starting value and where the floating control's last pick gets
        # written back, so it persists across runs once the flow is saved.
        "default_props": {"controls": []},
    },
    "skills": {
        "label": "Skills",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        # Each entry: {"region": str, "key": str} - region is an OCR region drawn
        # over just a skill's cooldown number; an empty read means it's off
        # cooldown, and the block taps `key` for it. Row order is identity - row 1
        # is skills.s1 in a Code block, row 2 is skills.s2, and so on. Only one
        # Skills block is allowed per flow (enforced in view.py's add_node), so
        # that numbering is always unambiguous.
        "default_props": {"skills": []},
    },
    "turn_off": {
        "label": "Turn Off",
        "inputs": [Port("in", "exec")],
        "outputs": [Port("out", "exec")],
        # duration: e.g. "4h", "2m", "3h30m" - counted from when the run started,
        # not from when this block is reached. Arms a background timer and
        # continues to "out" immediately; everything else in the flow keeps
        # running normally until the timer stops the whole run.
        "default_props": {"duration": ""},
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
    "code": "Runs Python code you write yourself. Read/write `variables` (the same dict Set Variable/If use), or call `ocr(\"region_name\")`, `play(\"recording_name\", repeat=1)`, `log(*values)`, `sleep(seconds)`, `telegram(message)`, `keystroke(keys, min_wait=0, max_wait=None)` (taps each key in a string/list, waiting after every tap - a fixed min_wait, or a random min_wait-max_wait gap when max_wait is given), and `stop_requested()`. Branch or loop inside the code with normal Python if/while - there's always a single \"out\" once the code finishes. An error is logged with its traceback but doesn't stop the flow.",
    "controls": "Defines one or more named, multiple-choice values (e.g. \"battle-mode\" -> abc/def/fgh) that show up as dropdowns in the floating control while a flow runs, so you can change them mid-run without touching the graph. Every value here becomes a variable any If/While/Until/Logic condition or Code block can read, same as one set by Set Variable - registered the moment the run starts, regardless of whether this block is wired into the flow. Whatever you pick while running is written back here and saved with the flow, so the next run starts from your last choice.",
    "skills": "The flow's one Skills block (only one is allowed) - each row watches an OCR region drawn over just a skill's cooldown number, and taps its Key the moment that region reads empty (off cooldown). Reaching this block in the flow checks and presses every row in it, in order. Each row is also identified by its position: row 1 publishes an \"s1_ready\" variable (True/False) any If/While/Until/Logic condition can read, row 2 an \"s2_ready\", and so on. A Code block calls skills() to get a handle exposing skills.s1, skills.s2, ... (one per row, in the same order) - .is_ready() just looks, .press() taps the key if it was ready, .wait_and_press() blocks on that one specific slot until it's off cooldown, and skills.press_ready([skills.s1, skills.s2]) does a priority scan - presses whichever's ready first, skipping ones still on cooldown rather than waiting on them. All of these work regardless of whether this block itself is wired into the flow.",
    "turn_off": "Arms a timer that stops the whole run once a duration has passed since the run started - e.g. \"4h\" (4 hours), \"2m\" (2 minutes), \"3h30m\" (3 hours 30 minutes). Reaching this block doesn't pause anything - it continues to \"out\" immediately, and everything else in the flow keeps running exactly as normal until the timer fires and stops the run, the same as pressing Stop yourself. Place it once, anywhere reached early (e.g. right after Start) - reaching it again later doesn't restart or add to the timer.",
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
    if node.type == "code":
        code = str(node.props.get("code", "")).strip()
        if not code:
            return "(empty)"
        first_line = next((line.strip() for line in code.splitlines() if line.strip()), "")
        return _truncate(first_line, 34)
    if node.type == "controls":
        entries = node.props.get("controls") or []
        if not entries:
            return "(no controls)"
        parts = [f"{e.get('name', '?')}={e.get('current', '?')}" for e in entries]
        return _truncate(", ".join(parts), 40)
    if node.type == "skills":
        entries = node.props.get("skills") or []
        if not entries:
            return "(no skills)"
        last_state = node.props.get("last_state") or {}
        parts = []
        for i, e in enumerate(entries, start=1):
            name = f"s{i}"
            state = last_state.get(name)
            parts.append(f"{name}:{state}" if state else f"{name}->{e.get('key', '?')}")
        return _truncate(", ".join(parts), 40)
    if node.type == "turn_off":
        duration = node.props.get("duration") or ""
        if parse_duration_seconds(duration) is None:
            return "(no duration)" if not duration.strip() else f"'{duration}' (invalid)"
        return f"stop after {duration}"
    return ""
