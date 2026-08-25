from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal

from ..config import Settings
from ..ocr.capture import capture_cgimage
from ..ocr.engine import read_text_from_cgimage
from ..ocr.extract import apply_extract_pattern
from ..ocr.region import Region
from ..recording.events import InputEvent
from ..recording.player import PauseFlag, Player, StopFlag
from ..telegram import TelegramError, send_message as send_telegram_message
from .conditions import evaluate_condition
from .model import Connection, Graph, Node

STEP_LIMIT = 10000
VAR_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class _LoopBreak(Exception):
    """Raised by an Exit Loop block whose condition is true. Caught by the nearest
    enclosing _execute_loop call, i.e. the direct parent loop - this falls out
    naturally from _execute_loop/_walk_chain's own call recursion for nested loops,
    with no separate loop-stack bookkeeping needed."""


class GraphRunner(QObject):
    status = Signal(str)
    node_started = Signal(str)
    node_updated = Signal(str)
    finished = Signal()

    def __init__(
        self,
        graph: Graph,
        stop_flag: StopFlag,
        recordings_dir: Path,
        pause_flag: Optional[PauseFlag] = None,
        settings: Optional[Settings] = None,
    ):
        super().__init__()
        self.graph = graph
        self.stop_flag = stop_flag
        self.pause_flag = pause_flag or PauseFlag()
        self.recordings_dir = recordings_dir
        self.settings = settings or Settings()
        self.variables: Dict[str, Any] = {}
        self._logic_eval_stack: set = set()  # guards against a Logic block cycle (A -> B -> A)
        self._telegram_send_counts: Dict[str, int] = {}  # node id -> successful sends this run()

    def _wait_while_paused(self) -> bool:
        """Blocks while paused, still watching stop_flag. Returns False if stopped
        while waiting, so the caller can bail the same way it would for a plain stop."""
        while self.pause_flag.is_set():
            if self.stop_flag.is_set():
                return False
            time.sleep(0.05)
        return True

    def _interpolate(self, text: str) -> str:
        """Replaces every $name in text with that variable's current value (set
        earlier in the flow by a Set Variable block). An unrecognized $name is left
        untouched, so a typo shows up as a literal "$typo" rather than silently
        vanishing."""

        def replace(match: "re.Match") -> str:
            name = match.group(1)
            if name in self.variables:
                return str(self.variables[name])
            return match.group(0)

        return VAR_REF_RE.sub(replace, text)

    def _emit_tap(self, conn: Optional[Connection]) -> None:
        """A wire can carry an optional Log "tap" - a message logged whenever that
        specific connection is traversed, without becoming a real step in the chain
        (the wire's own routing is completely untouched)."""
        if conn is not None and conn.log_message:
            self.status.emit(self._interpolate(conn.log_message))

    def run(self) -> None:
        start_node = next((n for n in self.graph.nodes.values() if n.type == "start"), None)
        if start_node is None:
            self.status.emit("No Start node in graph.")
            self.finished.emit()
            return

        self.variables = {}
        # Reset once per run(), not per loop iteration or per flow-repeat, so "max
        # sends" caps the total across the whole run - exactly the "despite being
        # called many times" case a loop body creates.
        self._telegram_send_counts = {}
        repeat = self.graph.repeat  # 0 = infinite
        count = 0
        aborted = False
        while not self.stop_flag.is_set():
            # A flow that's little more than Start -> Recorded Block gives no other
            # visible sign a lap ever finished and a new one began - recorded-block
            # playback doesn't log anything of its own, so without this a correctly
            # looping run and a silently-stuck one look identical. Skipped for a
            # single-pass run (repeat == 1) where lap numbering isn't informative.
            if repeat != 1:
                self.status.emit(f"Lap {count + 1}" + ("" if repeat == 0 else f" of {repeat}"))
            try:
                if not self._walk_chain(start_node):
                    aborted = True
                    break
            except _LoopBreak:
                # An Exit Loop block fired outside of any loop - nothing to break out
                # of, so just treat it as the end of this pass and keep going.
                self.status.emit("Exit Loop block used outside a loop - ignored.")
            count += 1
            if repeat != 0 and count >= repeat:
                break

        if not aborted:
            self.status.emit("Stopped." if self.stop_flag.is_set() else "Done.")
        self.finished.emit()

    def _walk_chain(self, start: Node) -> bool:
        """Walks a linear sequence of nodes, following whichever output port each
        node's own execution selects (normally "out"; branch/loop nodes pick
        differently). Returns False only on the step-limit abort; True covers both a
        normal finish and an early stop via stop_flag."""
        current: Optional[Node] = start
        steps = 0
        while current is not None:
            if self.stop_flag.is_set():
                return True
            if not self._wait_while_paused():
                return True
            steps += 1
            if steps > STEP_LIMIT:
                self.status.emit("Aborted: too many steps (possible infinite loop).")
                return False
            self.node_started.emit(current.id)
            next_port = self._execute(current)
            self.node_updated.emit(current.id)
            if next_port is None:
                return True
            conn = self.graph.outgoing(current.id, next_port)
            self._emit_tap(conn)
            current = self.graph.nodes.get(conn.to_node) if conn else None
        return True

    def _execute(self, node: Node) -> Optional[str]:
        """Executes one node. Returns the output port name the walk should continue
        from, or None to stop this chain here (either the node has no further
        continuation to pick, or stop_flag fired during a long-running step)."""
        if node.type == "start":
            return "out"

        if node.type == "delay":
            if not self._sleep_interruptible(float(node.props.get("seconds", 1.0))):
                return None
            return "out"

        if node.type == "log":
            self.status.emit(self._interpolate(str(node.props.get("message", ""))))
            return "out"

        if node.type == "telegram":
            return "out" if self._execute_telegram(node) else None

        if node.type == "recorded_block":
            self._execute_recorded_block(node)
            return None if self.stop_flag.is_set() else "out"

        if node.type == "ocr":
            self._execute_ocr(node)
            return None if self.stop_flag.is_set() else "out"

        if node.type == "set_variable":
            self._execute_set_variable(node)
            return "out"

        if node.type == "if":
            return self._execute_if(node)

        if node.type in ("for_loop", "while_loop", "until_loop"):
            return self._execute_loop(node)

        if node.type == "connector":
            return self._execute_connector(node)

        if node.type == "logic":
            result = self._evaluate_condition(node.props.get("condition", {}))
            node.props["last_value"] = result
            return "out"

        if node.type == "loop_exit":
            if self._evaluate_condition(node.props.get("condition", {})):
                raise _LoopBreak()
            return "out"

        return "out"

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleeps up to `seconds`, checking stop_flag and pause_flag frequently.
        Returns False if interrupted early. Time spent paused doesn't count against
        `seconds`, so a paused delay simply resumes with whatever was left."""
        waited = 0.0
        while waited < seconds:
            if self.stop_flag.is_set():
                return False
            if not self._wait_while_paused():
                return False
            step = min(0.05, seconds - waited)
            time.sleep(step)
            waited += step
        return True

    def _execute_recorded_block(self, node: Node) -> None:
        name = node.props.get("recording")
        if not name:
            self.status.emit(f"Recorded Block '{node.id}' has no recording set.")
            return
        path = self.recordings_dir / f"{name}.json"
        if not path.exists():
            self.status.emit(f"Recording not found: {name}")
            return
        data = json.loads(path.read_text())
        events = [InputEvent.from_dict(e) for e in data.get("events", [])]
        repeat = int(node.props.get("repeat", 1))
        Player(events, self.stop_flag, speed=1.0, pause_flag=self.pause_flag).run(repeat=repeat)

    def _read_ocr_region(self, region_name: str) -> Any:
        """Live-reads a named OCR region right now (capture + text recognition, no
        caching). Shared by a chain-walked OCR node's own execution and by any
        condition (If/While/Until) that references an OCR reading, so a loop
        condition always sees the screen as it is at each check - not a value cached
        from whenever some OCR node last happened to run as a regular chain step."""
        try:
            region = Region.load(region_name)
        except FileNotFoundError:
            self.status.emit(f"OCR region not found: {region_name}")
            return None
        image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
        return read_text_from_cgimage(image_ref)

    def _apply_extract(self, node: Node, value: Any) -> Any:
        pattern = node.props.get("extract_pattern")
        result, error = apply_extract_pattern(value, pattern)
        if error:
            self.status.emit(f"OCR '{node.id}': invalid extract pattern ({error}) - using raw text.")
        return result

    def _execute_ocr(self, node: Node) -> None:
        region_name = node.props.get("region")
        if not region_name:
            self.status.emit(f"OCR '{node.id}' has no region set.")
            return
        value = self._read_ocr_region(region_name)
        value = self._apply_extract(node, value)
        node.props["last_value"] = value
        self.status.emit(f"OCR '{region_name}': {value!r}" if value is not None else f"OCR '{region_name}': no text detected")

        # Delay after the read, not a background timer - looping back to this node
        # (e.g. via the flow's Loops setting, or a While/Until loop) is what gives
        # "read every N seconds".
        self._sleep_interruptible(float(node.props.get("interval_seconds", 5.0)))

    def _execute_set_variable(self, node: Node) -> None:
        name = node.props.get("name")
        if not name:
            self.status.emit(f"Set Variable '{node.id}' has no variable name.")
            return
        if node.props.get("source_type") == "ocr":
            source_node = self.graph.nodes.get(node.props.get("source_node"))
            value = source_node.props.get("last_value") if source_node is not None else None
        else:
            value = node.props.get("literal_value", "")
        self.variables[name] = value
        self.status.emit(f"${name} = {value!r}")

    def _execute_telegram(self, node: Node) -> bool:
        """Sends a message via the Telegram bot, honoring an optional per-block "max
        sends" cap (so a block reached many times, e.g. inside a loop, doesn't spam
        one notification per iteration) and an optional "wait before sending" delay.
        A network call, unlike Log - failures (missing bot config, no connection,
        Telegram rejecting the request) are logged the same way any other status
        message is, but never abort the flow, matching Log's own "never blocks
        anything" behavior. Returns False only if the wait was interrupted by a
        stop - a skipped-due-to-limit or failed-to-send message still lets the flow
        continue normally."""
        max_sends = int(node.props.get("max_sends", 0))
        sent_so_far = self._telegram_send_counts.get(node.id, 0)
        if max_sends and sent_so_far >= max_sends:
            self.status.emit(f"Telegram: send limit ({max_sends}) already reached, skipping.")
            return True

        wait_seconds = float(node.props.get("wait_seconds", 0.0))
        if wait_seconds > 0 and not self._sleep_interruptible(wait_seconds):
            return False

        message = self._interpolate(str(node.props.get("message", "")))
        try:
            send_telegram_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, message)
            self._telegram_send_counts[node.id] = sent_so_far + 1
            self.status.emit(f"Telegram: sent {message!r}")
        except TelegramError as exc:
            self.status.emit(f"Telegram: failed to send ({exc})")
        return True

    def _resolve_leaf_value(self, condition: dict) -> Any:
        source_type = condition.get("source_type")
        if source_type == "ocr":
            source_node = self.graph.nodes.get(condition.get("source_node"))
            region_name = source_node.props.get("region") if source_node is not None else None
            if not region_name:
                return None
            value = self._read_ocr_region(region_name)
            value = self._apply_extract(source_node, value)
            source_node.props["last_value"] = value  # keeps that OCR node's own summary in sync too
            return value
        if source_type == "variable":
            return self.variables.get(condition.get("source_name"))
        if source_type == "logic":
            source_node = self.graph.nodes.get(condition.get("source_node"))
            if source_node is None:
                return None
            if source_node.id in self._logic_eval_stack:
                self.status.emit(f"Logic block cycle detected at '{source_node.id}' - treated as False.")
                return False
            self._logic_eval_stack.add(source_node.id)
            try:
                result = self._evaluate_condition(source_node.props.get("condition", {}))
            finally:
                self._logic_eval_stack.discard(source_node.id)
            source_node.props["last_value"] = result  # keeps that Logic node's own summary in sync too
            return result
        return None

    def _evaluate_condition(self, condition: dict) -> bool:
        """A condition is either a single leaf or an AND/OR group of clauses (see
        conditions.evaluate_condition); each leaf's actual value is resolved live via
        _resolve_leaf_value at the moment it's needed."""
        return evaluate_condition(condition, self._resolve_leaf_value)

    def _execute_if(self, node: Node) -> str:
        cases = node.props.get("cases") or []
        for i, case in enumerate(cases):
            if self._evaluate_condition(case):
                return "If" if i == 0 else f"Elif {i}"
        return "Else"

    def _execute_loop(self, node: Node) -> Optional[str]:
        body_conn = self.graph.outgoing(node.id, "body")
        body_start = self.graph.nodes.get(body_conn.to_node) if body_conn else None

        try:
            if node.type == "for_loop":
                count = int(node.props.get("count", 0))
                i = 0
                while i < count:
                    if self.stop_flag.is_set():
                        return None
                    self._emit_tap(body_conn)
                    if body_start is not None and not self._walk_chain(body_start):
                        return None
                    if self.stop_flag.is_set():
                        return None
                    i += 1
                return "done"

            invert = node.type == "until_loop"  # Until repeats while the condition is still False
            guard = 0
            while True:
                if self.stop_flag.is_set():
                    return None
                condition = node.props.get("condition", {})
                result = self._evaluate_condition(condition)
                should_continue = (not result) if invert else result
                if not should_continue:
                    break
                self._emit_tap(body_conn)
                if body_start is not None and not self._walk_chain(body_start):
                    return None
                if self.stop_flag.is_set():
                    return None
                guard += 1
                if guard > STEP_LIMIT:
                    self.status.emit("Aborted: loop exceeded step limit.")
                    return None
            return "done"
        except _LoopBreak:
            return "done"

    def _execute_connector(self, node: Node) -> Optional[str]:
        """Fans out to every wire attached to its single "out" port, in the order
        they were connected, walking each one's chain to completion before moving to
        the next. A Connector has no "after the fan-out" continuation of its own - it
        always ends the chain it was reached from."""
        for conn in self.graph.outgoing_all(node.id, "out"):
            if self.stop_flag.is_set():
                return None
            self._emit_tap(conn)
            target = self.graph.nodes.get(conn.to_node)
            if target is not None and not self._walk_chain(target):
                return None
        return None
